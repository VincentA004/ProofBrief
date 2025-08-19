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

    ## PUBLIC ARTIFACTS (links + meta)
    {json.dumps(artifacts, indent=2)}

    ## OBJECTIVE HEURISTICS
    {json.dumps(heuristics, indent=2)}

    ## SELECTED GITHUB CODE (snippets + file names)
    {code_section}

    ## EVIDENCE HIERARCHY & RULES (READ CAREFULLY)
    1) **Prefer README-derived technology lists** for each repo as the primary source of what technologies were used.
    2) Treat **package manifests** (e.g., requirements.txt, pyproject.toml, package.json, poetry.lock) and **build files** as strong confirmation signals.
    3) Treat **code snippets** as supporting evidence, but DO NOT assume the entire project uses a technology just because one small snippet shows it.
    4) The **resume text** can confirm technologies claimed by the candidate; use it to cross-check repo claims.
    5) The job description is for relevance only; DO NOT infer the candidate used a technology just because the JD lists it.

    STRICT VERIFICATION POLICY
    - Make a technology claim ONLY if it is explicitly supported by: (a) README tech list, OR (b) a manifest/build file, OR (c) a code snippet, OR (d) the resume text.
    - If README lists a tech, you may assert it is used even if no code snippet is shown here.
    - If evidence is conflicting or insufficient, write: "Not found in provided data" or "Uncertain: insufficient evidence".
    - Every item in "evidence_highlights" MUST include an "evidence_url" that points to a concrete artifact (repo, file, PR, etc.) and a brief justification referencing README/manifest/snippet/resume.
    - DO NOT add technologies that are not present in README/manifest/snippets/resume text.

    ### OUTPUT JSON SHAPE
    {{
    "summary": [
        "3 short bullets on role fit, grounded in README/manifest/snippet/resume evidence"
    ],
    "evidence_highlights": [
        {{
        "claim": "what the candidate did / can do (grounded in README/manifest/snippet/resume)",
        "evidence_url": "link to repo/file/pr/etc.",
        "justification": "why this matters for the JD; cite which source (README / manifest / snippet / resume)"
        }}
    ],
    "risk_flags": [
        "1-3 thoughtful risks; if a tech or outcome isn't evidenced in the sources, call it out"
    ],
    "screening_questions": [
        "4 tailored questions that probe for signal based on JD and evidenced technologies"
    ],
    "final_score": [
        "1-100 score based on the evidence and the JD"
    ]
    }}

    OUTPUT REQUIREMENTS
    - Be concise and factual; do not guess.
    - Cite sources (README/manifest/snippet/resume) in each justification where relevant.
    - If data is missing, say so. Do not hallucinate.
    - Output MUST be valid JSON and nothing else.
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
