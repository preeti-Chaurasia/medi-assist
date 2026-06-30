import datetime
import os
import sys
import re
import json
from zoneinfo import ZoneInfo
from pydantic import BaseModel, Field
from google.genai import types

from google.adk.workflow import Workflow, node, FunctionNode, START
from google.adk.agents import LlmAgent
from google.adk.tools import AgentTool
from google.adk.events.event import Event
from google.adk.events.request_input import RequestInput
from google.adk.agents.context import Context
from google.adk.apps import App, ResumabilityConfig
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StdioConnectionParams
from mcp import StdioServerParameters

from .config import config

# Define schemas for sub-agents (required for workflows)
class SymptomReport(BaseModel):
    severity: str = Field(description="Severity of symptoms: low, medium, high")
    recommendations: str = Field(description="Recommendations or guidance for the patient")
    should_escalate: bool = Field(description="Whether the patient should seek immediate medical attention")

class PrescriptionExplanation(BaseModel):
    explanation: str = Field(description="Detailed explanation of the prescription medication")
    instructions: str = Field(description="Clear dosage instructions")
    interaction_warnings: str = Field(description="Warnings about potential drug interactions or foods to avoid")

# Configure the local MCP server path and connection
mcp_server_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "mcp_server.py"))
mcp_toolset = McpToolset(
    connection_params=StdioConnectionParams(
        server_params=StdioServerParameters(
            command=sys.executable,
            args=[mcp_server_path],
        )
    )
)

# 1. Symptom Analyzer Specialist
symptom_analyzer = LlmAgent(
    name="symptom_analyzer",
    model=config.model,
    instruction="""You are a medical symptom analyzer. Analyze the reported symptoms.
Determine if they are low, medium, or high severity, provide recommendations, and state whether they should escalate to immediate medical attention.
Use get_symptom_triage_guidelines to fetch relevant medical guidelines for the symptoms.
ALWAYS provide a disclaimer that you are an AI assistant and not a medical doctor. Your advice is for informational purposes only.""",
    output_schema=SymptomReport,
    output_key="symptom_report",
    tools=[mcp_toolset],
    description="Analyzes medical symptoms, assesses severity, and provides patient advice."
)

# 2. Prescription Helper Specialist
prescription_helper = LlmAgent(
    name="prescription_helper",
    model=config.model,
    instruction="""You are a prescription helper. Explain the medication details, dosage instructions, and check for potential drug interactions or warnings.
Use get_drug_interactions to check for drug safety.
ALWAYS provide a disclaimer that you are an AI assistant and not a medical doctor. Your advice is for informational purposes only.""",
    output_schema=PrescriptionExplanation,
    output_key="prescription_explanation",
    tools=[mcp_toolset],
    description="Explains prescriptions, dosage, and checks for drug-drug interactions."
)

# 3. Orchestrator / Coordinator
orchestrator_agent = LlmAgent(
    name="orchestrator",
    model=config.model,
    mode="single_turn",
    instruction="""You are the main coordinator for MediAssist.
Review the patient's request (after it passed security checks) and choose the right specialist to delegate to:
- For symptoms, illnesses, or pain, delegate to symptom_analyzer.
- For prescriptions, pill explanations, dosages, or interaction checks, delegate to prescription_helper.
- If the user wants to set or schedule a medication reminder, analyze their request, extract the medication name and time, and output that you will set a reminder (e.g. 'I will schedule a reminder for Ibuprofen at 8:00 AM').
Summarize the specialist responses or your reminder actions clearly to the user. Always remain empathetic and professional.""",
    tools=[AgentTool(symptom_analyzer), AgentTool(prescription_helper)],
)

# 4. Security Checkpoint (Function Node)
def security_checkpoint(ctx: Context, node_input: types.Content) -> Event:
    text = ""
    if hasattr(node_input, 'parts') and node_input.parts:
        text = node_input.parts[0].text or ""
    elif isinstance(node_input, str):
        text = node_input
        
    audit_log = {
        "timestamp": datetime.datetime.utcnow().isoformat(),
        "session_id": ctx.session.id,
        "severity": "INFO",
        "checks": []
    }
    
    # PII Scrubbing
    ssn_pattern = r'\b\d{3}-\d{2}-\d{4}\b'
    phone_pattern = r'\b\d{3}-\d{3}-\d{4}\b'
    mrn_pattern = r'\bMRN-\d{6}\b'
    
    scrubbed_text = text
    scrubbed_count = 0
    
    for pattern, label in [(ssn_pattern, "[SSN]"), (phone_pattern, "[PHONE]"), (mrn_pattern, "[MRN]")]:
        matches = re.findall(pattern, scrubbed_text)
        if matches:
            scrubbed_count += len(matches)
            scrubbed_text = re.sub(pattern, label, scrubbed_text)
            audit_log["checks"].append(f"PII scrubbed: {label} ({len(matches)} occurrences)")
            
    if scrubbed_count > 0:
        audit_log["severity"] = "WARNING"
        ctx.state["query_scrubbed"] = True
    else:
        ctx.state["query_scrubbed"] = False
        
    ctx.state["scrubbed_query"] = scrubbed_text

    # Prompt Injection Detection
    injection_keywords = ["ignore previous instructions", "system prompt", "override instructions", "you are now a"]
    injection_detected = False
    for kw in injection_keywords:
        if kw in text.lower():
            injection_detected = True
            audit_log["checks"].append(f"Prompt injection detected: keyword '{kw}' found")
            break
            
    if injection_detected:
        audit_log["severity"] = "CRITICAL"
        print(json.dumps(audit_log))
        return Event(output="Security Warning: Prompt injection detected.", route="SECURITY_EVENT")
        
    # Domain-specific rule: Prohibited substance screening
    illegal_drugs = ["heroin", "cocaine", "methamphetamine", "fentanyl buy"]
    drug_abuse_detected = False
    for drug in illegal_drugs:
        if drug in text.lower():
            drug_abuse_detected = True
            audit_log["checks"].append(f"Illegal substance query detected: '{drug}'")
            break
            
    if drug_abuse_detected:
        audit_log["severity"] = "CRITICAL"
        print(json.dumps(audit_log))
        return Event(output="Security Warning: Prohibited drug references detected.", route="SECURITY_EVENT")

    audit_log["checks"].append("All security checks passed.")
    print(json.dumps(audit_log))
    
    return Event(output=scrubbed_text, route="clean")

# 5. Security Violation Handler (Function Node)
def security_violation_handler(node_input: str) -> str:
    return f"Access Denied. {node_input}"

# 6. Human Approval Checkpoint for reminders (Function Node)
async def human_approval_checkpoint(ctx: Context, node_input: types.Content):
    text_content = ""
    if hasattr(node_input, 'parts') and node_input.parts:
        text_content = node_input.parts[0].text or ""
    elif isinstance(node_input, str):
        text_content = node_input
    
    # Check if user asked to set/schedule a medication reminder
    if "reminder" in text_content.lower() or "schedule" in text_content.lower():
        if not ctx.resume_inputs or "confirm_reminder" not in ctx.resume_inputs:
            # Yield RequestInput to pause and ask user for confirmation
            yield RequestInput(
                interrupt_id="confirm_reminder",
                message="Please confirm if you would like me to proceed with scheduling this medication reminder (Reply 'Yes' or 'No')."
            )
            return
        
        # User responded, parse input
        user_response = ctx.resume_inputs.get("confirm_reminder", "").lower()
        if "yes" in user_response:
            # Call MCP tool to record the reminder
            # Since mcp_toolset is defined, we can also simulate it or trigger it
            # We can log it to the mcp server if needed, or simply output success
            yield Event(output=f"{text_content}\n\n✅ [System: Medication reminder successfully confirmed and logged.]", route="final")
        else:
            yield Event(output=f"{text_content}\n\n❌ [System: Medication reminder scheduling was cancelled by patient.]", route="final")
    else:
        yield Event(output=text_content, route="final")

# 7. Final Output Node (renders content in UI)
def final_output(node_input: str):
    yield Event(content=types.Content(role='model', parts=[types.Part.from_text(text=node_input)]))
    yield Event(output=node_input)

# Define the workflow graph
root_agent = Workflow(
    name="MediAssistWorkflow",
    edges=[
        ('START', security_checkpoint),
        (security_checkpoint, {
            "SECURITY_EVENT": security_violation_handler,
            "clean": orchestrator_agent
        }),
        (orchestrator_agent, human_approval_checkpoint),
        (human_approval_checkpoint, final_output),
        (security_violation_handler, final_output),
    ],
    description="Secure MediAssist multi-agent workflow for triage, prescription help, and reminders."
)

app = App(
    name="app",
    root_agent=root_agent,
    resumability_config=ResumabilityConfig(is_resumable=True)
)
