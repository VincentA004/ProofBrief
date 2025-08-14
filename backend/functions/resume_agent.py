# backend/functions/resume_agent.py

import json

# Shared utilities
from shared.utils import log, SESSION, s3_get_text


def _get_text_from_event_or_s3(event: dict, field_base: str) -> str:
    """
    Read text directly from event[field_base] if present, else from event[field_base+'S3'].
    Expects shape {"bucket": "...", "key": "..."} for S3 pointer.
    """
    if field_base in event and event[field_base]:
        return event[field_base]
    pointer = event.get(f"{field_base}S3")
    if pointer and "bucket" in pointer and "key" in pointer:
        return s3_get_text(pointer["bucket"], pointer["key"])
    raise ValueError(f"Missing {field_base} or {field_base}S3 in event")


# --- Core Logic ---

def generate_final_brief(
    resume_text: str, jd_text: str, artifacts: list, heuristics: dict
) -> dict:
    """Construct a detailed prompt and call Bedrock for final analysis."""
    log.info("Constructing final prompt for synthesis agent.")
    bedrock = SESSION.client("bedrock-runtime")

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
""".strip()

    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 4096,
        "temperature": 0.1,
        "messages": [{"role": "user", "content": prompt}],
    })

    try:
        resp = bedrock.invoke_model(
            body=body,
            modelId="anthropic.claude-3-sonnet-20240229-v1:0",
            contentType="application/json",
            accept="application/json",
        )
        payload = json.loads(resp["body"].read())
        text = (payload.get("content", [{}])[0] or {}).get("text", "")
        # Try to parse JSON; strip code fences if present
        t = text.strip()
        if t.startswith("```"):
            import re as _re
            t = _re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=_re.IGNORECASE | _re.MULTILINE)
        result = json.loads(t)
        log.info("Received response from Bedrock.")
        return result
    except Exception as e:
        log.error(f"Error calling Bedrock or parsing JSON: {e}")
        raise


# --- AWS Lambda Handler ---

def handler(event, context):
    """
    Receives enriched data and calls the core logic function.
    Supports either inline text or S3 pointers provided by previous step.
    """
    log.info("ResumeAgent function triggered.")
    brief_id = event["briefId"]

    resume_text = _get_text_from_event_or_s3(event, "resumeText")
    jd_text = _get_text_from_event_or_s3(event, "jdText")

    llm_content = generate_final_brief(
        resume_text=resume_text,
        jd_text=jd_text,
        artifacts=event.get("scrapedArtifacts", []),
        heuristics=event.get("heuristicScores", {}),
    )

    return {
        "briefId": brief_id,
        "finalContent": llm_content,
    }
