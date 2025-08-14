# backend/functions/resume_agent/main.py

import json

# NEW: Import shared utilities for logging and AWS session management
from shared.utils import log, SESSION

# --- Core Logic Function ---

def generate_final_brief(
    resume_text: str, jd_text: str, artifacts: list, heuristics: dict
) -> dict:
    """Constructs a detailed prompt and calls Bedrock for final analysis."""
    log.info("Constructing final prompt for the synthesis agent.")
    
    # Use the shared boto3 session for consistency
    bedrock = SESSION.client("bedrock-runtime")
    
    # --- Prompt Engineering (remains the same) ---
    prompt = f"""
    You are an expert technical hiring manager providing a final, data-driven analysis of a candidate.
    Based on the comprehensive data provided below, generate a concise and factual candidate brief.
    Your entire response must be a single, valid JSON object.

    ## CONTEXT ##

    # Job Description:
    {jd_text}

    # Candidate's Resume Text:
    {resume_text}

    # Candidate's Scraped Public Artifacts:
    {json.dumps(artifacts, indent=2)}

    # Objective Heuristic Scores:
    {json.dumps(heuristics, indent=2)}

    ## INSTRUCTIONS ##

    Generate a JSON object with the following keys:
    - "summary": A 3-bullet point summary of the candidate's fit for the role.
    - "evidence_highlights": A list of 3-5 key pieces of evidence. Each item must be an object with keys "claim", "evidence_url", and "justification".
    - "risk_flags": A list of 1-3 potential risks or areas to probe in an interview.
    - "screening_questions": A list of 4 tailored, open-ended screening questions based on comparing the candidate's evidence to the job's requirements.
    """

    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 4096,
        "temperature": 0.1,
        "messages": [{"role": "user", "content": prompt}]
    })
    
    try:
        response = bedrock.invoke_model(
            body=body,
            modelId="anthropic.claude-3-sonnet-20240229-v1:0", 
            contentType="application/json",
            accept="application/json"
        )
        response_body = json.loads(response.get("body").read())
        llm_output_text = response_body['content'][0]['text']
        log.info("Successfully received response from Bedrock.")
        return json.loads(llm_output_text)
    except Exception as e:
        log.error(f"Error calling Bedrock: {e}")
        raise

# --- AWS Lambda Handler ---
def handler(event, context):
    """
    Receives enriched data and calls the core logic function.
    """
    log.info("ResumeAgent function triggered.")
    
    brief_id = event["briefId"]
    llm_content = generate_final_brief(
        resume_text=event["resumeText"],
        jd_text=event["jdText"],
        artifacts=event["scrapedArtifacts"],
        heuristics=event["heuristicScores"]
    )
    
    return {
        "briefId": brief_id,
        "finalContent": llm_content
    }