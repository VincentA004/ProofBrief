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


def _load_selected_repo_excerpts(event: dict, max_per_repo_chars: int = 8000, max_repos: int = 4) -> list[dict]:
    """
    Reads event['selectedRepoTextS3'] -> list of {"url": ..., "s3": {"bucket":..., "key":...}}
    Returns list of {"url": ..., "excerpt": "..."}.
    """
    out = []
    items = event.get("selectedRepoTextS3") or []
    for item in items[:max_repos]:
        try:
            url = item.get("url")
            s3p = item.get("s3") or {}
            bucket = s3p.get("bucket")
            key = s3p.get("key")
            if not (url and bucket and key):
                continue
            text = s3_get_text(bucket, key)
            if text and len(text) > max_per_repo_chars:
                text = text[:max_per_repo_chars]
            out.append({"url": url, "excerpt": text or ""})
        except Exception as e:
            log.warning("Failed to load repo excerpt: %s", e)
    return out


# --- Core Logic ---

def generate_final_brief(
    resume_text: str,
    jd_text: str,
    artifacts: list,
    heuristics: dict,
    repo_excerpts: list[dict],
    skill_map: dict,
) -> dict:
    """Construct a detailed prompt and call Bedrock for final analysis."""
    log.info("Constructing final prompt for synthesis agent.")
    bedrock = SESSION.client("bedrock-runtime")

    # Build a compact repo excerpts section
    repo_section_parts = []
    for r in repo_excerpts:
        repo_section_parts.append(f"- URL: {r.get('url')}\n{r.get('excerpt','')}\n")
    repo_section = "\n\n".join(repo_section_parts) if repo_section_parts else "None"

    prompt = f"""
You are an expert technical hiring manager providing a final, data-driven analysis of a candidate.
Return ONLY a single, valid JSON object as described below—no prose outside JSON.

## CONTEXT ##

# Job Description:
{jd_text}

# Candidate's Resume Text:
{resume_text}

# Candidate's Scraped Public Artifacts (all):
{json.dumps(artifacts, indent=2)}

# Selected Repository Excerpts (subset chosen for relevance):
{repo_section}

# Objective Heuristic Scores:
{json.dumps(heuristics, indent=2)}

# Skill Map (keywords to watch for; already derived from JD+resume context):
{json.dumps(skill_map, indent=2)}

## OUTPUT JSON SHAPE (exact keys) ##
{{
  "summary": ["...", "...", "..."],
  "evidence_highlights": [
    {{"claim": "...", "evidence_url": "...", "justification": "..."}}
  ],
  "risk_flags": ["...", "..."],
  "screening_questions": ["...", "...", "...", "..."]
}}

Only return that JSON object.
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

    repo_excerpts = _load_selected_repo_excerpts(event)
    skill_map = event.get("skillMap", {}) or {}
    heuristics = event.get("heuristicScores", {}) or {}
    artifacts = event.get("scrapedArtifacts", []) or []

    llm_content = generate_final_brief(
        resume_text=resume_text,
        jd_text=jd_text,
        artifacts=artifacts,
        heuristics=heuristics,
        repo_excerpts=repo_excerpts,
        skill_map=skill_map,
    )

    return {
        "briefId": brief_id,
        "finalContent": llm_content,
    }
