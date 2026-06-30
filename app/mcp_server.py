import sys
from mcp.server.fastmcp import FastMCP

# Initialize FastMCP Server
mcp = FastMCP("MediAssist MCP Server")

@mcp.tool()
def get_drug_interactions(drug_a: str, drug_b: str) -> str:
    """Check if drug_a and drug_b have adverse interactions.

    Args:
        drug_a: Name of the first drug (e.g. aspirin, warfarin).
        drug_b: Name of the second drug (e.g. aspirin, warfarin).
    """
    a_lower = drug_a.lower().strip()
    b_lower = drug_b.lower().strip()
    
    # Simulated interaction rules
    interactions = {
        ("aspirin", "warfarin"): "WARNING: Increased risk of bleeding. Close monitoring is recommended.",
        ("ibuprofen", "aspirin"): "Moderate interaction: May decrease the cardioprotective effect of low-dose aspirin.",
        ("simvastatin", "amiodarone"): "WARNING: Increased risk of myopathy/rhabdomyolysis.",
        ("sildenafil", "nitroglycerin"): "CRITICAL DANGER: Severe hypotension. Do NOT take together under any circumstances."
    }
    
    # Check both combinations
    if (a_lower, b_lower) in interactions:
        return interactions[(a_lower, b_lower)]
    if (b_lower, a_lower) in interactions:
        return interactions[(b_lower, a_lower)]
        
    return f"No severe drug interactions known between {drug_a} and {drug_b} in our database. Always consult your pharmacist."

@mcp.tool()
def get_symptom_triage_guidelines(symptom: str) -> str:
    """Retrieve triage rules and safety guidelines based on a symptom keyword.

    Args:
        symptom: The symptom description or keyword (e.g. chest pain, fever, cough).
    """
    s_lower = symptom.lower().strip()
    if "chest pain" in s_lower or "shortness of breath" in s_lower or "breathing" in s_lower or "heart" in s_lower:
        return "CRITICAL TRIAGE: Seek emergency medical services (911) immediately. Do not drive yourself. A health professional must evaluate you."
    if "fever" in s_lower or "temperature" in s_lower:
        return "MODERATE TRIAGE: Monitor temperature. If fever exceeds 103°F (39.4°C) or lasts more than 3 days, see a doctor. Keep hydrated and rest."
    if "cough" in s_lower or "cold" in s_lower or "sore throat" in s_lower:
        return "LOW TRIAGE: Rest, stay hydrated. If cough lasts over 2 weeks or is accompanied by high fever or wheezing, consult a healthcare provider."
        
    return f"General Triage: For symptom '{symptom}', rest and monitor. If symptoms persist or worsen, seek professional medical evaluation."

@mcp.tool()
def log_medication_reminder(medication: str, time: str) -> str:
    """Log a medication reminder setup.

    Args:
        medication: Name of the medication.
        time: Time to take it (e.g., '8:00 AM' or 'nightly').
    """
    return f"SUCCESS: Scheduled medication reminder for {medication} at {time} in patient health record."

if __name__ == "__main__":
    mcp.run()
