# backend/functions/resume_agent.py

import json
from typing import List, Dict

# Shared utilities
from shared.utils import log, SESSION, s3_get_text, get_env


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


def _load_repo_bundles(event: dict, *, max_chars_total: int = 80_000) -> List[Dict]:
    """
    Load repo code bundles (saved by process_content) from S3.
    Returns a list of dicts: { "repoUrl":..., "files":[...], "text":<possibly truncated> }
    """
    bundles = event.get("repoBundles") or []
    out = []
    used = 0
    for b in bundles:
        s3p = b.get("textS3") or {}
        try:
            txt = s3_get_text(s3p.get("bucket"), s3p.get("key"))
        except Exception as e:
            log.warning("[resume_agent] Failed to fetch repo bundle %s: %s", b.get("repoUrl"), e)
            continue
        remaining = max_chars_total - used
        if remaining <= 0:
            break
        excerpt = txt[:remaining]
        used += len(excerpt)
        out.append({
            "repoUrl": b.get("repoUrl"),
            "files": b.get("files", []),
            "text": excerpt,
        })
    log.info("[resume_agent] Loaded %d repo bundle(s), total chars=%d", len(out), used)
    return out


# --- Core Logic ---

def generate_final_brief(
    resume_text: str,
    jd_text: str,
    artifacts: List[Dict],
    heuristics: Dict,
    repo_bundles: List[Dict],
) -> Dict:
    """Construct a detailed prompt and call Bedrock for final analysis."""
    log.info("[resume_agent] Constructing final prompt for synthesis agent.")
    bedrock = SESSION.client("bedrock-runtime")

    # Keep the repo code portion compact but useful
    code_section_items = []
    for b in repo_bundles:
        files_list = ", ".join(b.get("files", [])[:6]) or "(files omitted)"
        snippet = b.get("text", "")[:8000]  # per-repo cap to control tokens
        code_section_items.append(
            f"## {b.get('repoUrl')}\n"
            f"Files: {files_list}\n"
            f"--- BEGIN EXCERPT ---\n{snippet}\n--- END EXCERPT ---\n"
        )
    code_section = "\n".join(code_section_items)

    prompt = f"""
    You are an expert technical hiring manager providing a final, evidence-backed analysis of a candidate.
    Base your conclusions ONLY on the data below. Respond with ONE valid JSON object.

    ## JOB DESCRIPTION
    {jd_text}

    ## RESUME (OCR text)
    {resume_text}

    ## PUBLIC ARTIFACTS (GitHub Repos, Links, etc.)
    {json.dumps(artifacts, indent=2)}

    ## OBJECTIVE HEURISTICS
    {json.dumps(heuristics, indent=2)}

    ## SELECTED GITHUB CODE (Snippets + File Names)
    {code_section}

    ---
    ## EVIDENCE HIERARCHY & RULES (READ CAREFULLY)
    1) GitHub is the primary source of truth. Evidence from READMEs, package manifests, and code are the strongest signals.
    2) Detailed project or work experience descriptions in the resume are strong supporting evidence.
    3) A technology listed ONLY in a generic 'Skills' section is considered ZERO-EVIDENCE for scoring.

    ---
    ## SCORING RUBRIC (ULTRA-STRICT) — TOTAL 100 POINTS

    **GUIDING PRINCIPLE: The final score MUST be a direct reflection of demonstrated, verifiable depth. Unsubstantiated claims MUST result in a catastrophically low score.**

    Let:
    - REQUIRED = set of must-have skills/techs stated in the JD.
    - EVIDENCED = set of techs proven by GitHub OR within detailed resume project/work descriptions.
    - CLAIMED_ONLY = set of techs listed ONLY in a generic 'Skills' section.

    1) Core Requirement Coverage (30 pts)
    Score_H = 30 * ((# of REQUIRED ∩ EVIDENCED) / max(1, # of REQUIRED))

    2) Depth & Complexity (40 pts)
    - 1.0: Deep, complex application in GitHub repos with substantial code.
    - 0.7: Clear application in at least one significant project with code on GitHub.
    - 0.4: Tech mentioned in a detailed resume project, but GitHub evidence is sparse or academic.
    - 0.1: Simplistic projects from coursework; minimal code evidence.
    - 0.0: No verifiable project evidence exists.
    Score_D = 40 * depth_level

    3) Evidence Strength / Traceability (15 pts)
    - 1.0: Backed by GitHub README, manifest, AND code.
    - 0.4: Single source only (e.g., only a resume description).
    - 0.0: No verifiable evidence.
    Score_E = 15 * evidence_level

    4) Recency & Relevance (5 pts)
    - 1.0: Relevant work ≤18 months old.
    - 0.5: 19-36 months.
    - 0.0: >36 months or unclear.
    Score_R = 5 * recency_level

    5) Outcomes / Impact (5 pts)
    - 1.0: Quantified outcomes (e.g., latency ↓35%).
    - 0.5: Clear qualitative outcomes.
    - 0.0: No outcomes.
    Score_O = 5 * outcomes_level

    6) Preferred / Bonus Alignment (up to +5 pts)
    Score_B = 0 # Add points for PREFERRED skills if EVIDENCED

    7) Penalties (subtract)
    -20: Flat penalty if any `REQUIRED` skill is in `CLAIMED_ONLY`.
    Penalties P = sum of applied negatives

    ## FINAL SCORE ADJUDICATION (Simplified Logic)
    # First, calculate a Base Score using simple addition/subtraction.
    Base_Score = Score_H + Score_D + Score_E + Score_R + Score_O + Score_B + P
    #
    # Next, apply the highest-priority cap that is met. These are non-negotiable limits.
    # The final score CANNOT exceed these caps.

    1.  **Zero-Evidence Cap:** If the `depth_level` is 0.0 or the candidate has a penalty for unproven REQUIRED skills, the FINAL score is capped at a maximum of **29**. This is an automatic failure.
    2.  **Low-Depth Cap:** If the `depth_level` is 0.1, the FINAL score is capped at a maximum of **49**.
    3.  **Medium-Depth Cap:** If the `depth_level` is 0.4, the FINAL score is capped at a maximum of **69**.
    4.  **High-Depth Default:** If none of the caps above are met, the FINAL score is the `Base Score`.

    FINAL = The lowest applicable score after checking all caps. Clamp between 0 and 100.


    ---
    ## SCORING STEPS (FOLLOW EXACTLY)
    1) Extract REQUIRED and PREFERRED from JD.
    2) Build EVIDENCED and CLAIMED_ONLY sets.
    3) Compute ALL sub-scores and Penalties.
    4) Calculate the Base_Score using simple addition.
    5) Determine the FINAL score by applying the hard caps from the Adjudication section.
    6) Populate the JSON output. Do not include notes or formulas in the final JSON.

    ---
    ### OUTPUT JSON SHAPE
    {{
      "summary": [
        "3 short bullets on role fit, grounded in verifiable GitHub/resume evidence."
      ],
      "evidence_highlights": [
        {{
          "claim": "what the candidate did / can do (grounded in README/manifest/snippet/resume)",
          "evidence_url": "link to repo/file/pr/etc. or 'resume project description'",
          "justification": "why this matters for the JD; cite GitHub source or resume project."
        }}
      ],
      "risk_flags": [
        "1-3 thoughtful risks; call out any required skills that are claimed but not evidenced in projects or GitHub."
      ],
      "screening_questions": [
        "4 tailored questions that probe for signal based on JD and evidenced technologies."
      ],
      "final_score": "integer 0-100"
    }}
    """.strip()





    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 4096,
        "temperature": 0.1,
        "messages": [{"role": "user", "content": prompt}],
    })

    model_id_default = "anthropic.claude-3-sonnet-20240229-v1:0"
    model_id = get_env("FINAL_MODEL_ID", default=model_id_default, required=False) or \
               get_env("BEDROCK_MODEL_ID", default="anthropic.claude-3-haiku-20240307-v1:0", required=False)

    try:
        resp = bedrock.invoke_model(
            body=body,
            modelId=model_id,
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
        log.info("[resume_agent] Received response from Bedrock (model=%s).", model_id)
        return result
    except Exception as e:
        log.error(f"[resume_agent] Error calling Bedrock or parsing JSON: {e}")
        raise


# --- AWS Lambda Handler ---

def handler(event, context):
    """
    Receives enriched data and calls the core logic function.
    Supports either inline text or S3 pointers provided by previous step.
    """
    log.info("[resume_agent] Handler start. Event keys=%s", list(event.keys()))
    brief_id = event["briefId"]

    resume_text = _get_text_from_event_or_s3(event, "resumeText")
    jd_text = _get_text_from_event_or_s3(event, "jdText")
    repo_bundles = _load_repo_bundles(event, max_chars_total=80_000)

    llm_content = generate_final_brief(
        resume_text=resume_text,
        jd_text=jd_text,
        artifacts=event.get("scrapedArtifacts", []),
        heuristics=event.get("heuristicScores", {}),
        repo_bundles=repo_bundles,
    )

    return {
        "briefId": brief_id,
        "finalContent": llm_content,
    }
