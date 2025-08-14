# backend/functions/process_content.py

import json
import math
import time
import requests
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import text
from sqlalchemy.engine import Engine

# Shared utilities
from shared.utils import (
    get_db_engine,
    get_secret,
    s3_get_text,
    get_env,
    log,
    SESSION,
)


# --- GitHub scraping ---

def _username_from_url(url_or_username: str) -> str:
    if not url_or_username:
        return ""
    if url_or_username.startswith("http"):
        return url_or_username.rstrip("/").split("/")[-1]
    return url_or_username

def _req_with_retries(session: requests.Session, url: str, headers: dict, *, retries=4, timeout=8):
    backoff = 0.5
    for attempt in range(retries):
        try:
            resp = session.get(url, headers=headers, timeout=timeout)
            # handle rate limit
            if resp.status_code == 403 and "rate limit" in (resp.text or "").lower():
                reset = resp.headers.get("X-RateLimit-Reset")
                wait_s = max(1, int(reset) - int(time.time())) if reset else math.ceil(backoff)
                log.warning("GitHub rate-limited; sleeping %ss", wait_s)
                time.sleep(wait_s)
                continue
            resp.raise_for_status()
            return resp
        except requests.RequestException as e:
            if attempt == retries - 1:
                raise
            time.sleep(backoff)
            backoff *= 2

def scrape_github_profile(username_or_url: str, api_token: str, *, max_repos=10) -> list[dict]:
    """Scrape public repos with simple retries and cap."""
    username = _username_from_url(username_or_url)
    if not username:
        return []
    log.info(f"Scraping GitHub for '{username}'")

    headers = {"Authorization": f"Bearer {api_token}", "Accept": "application/vnd.github+json"}
    artifacts: list[dict] = []
    session = requests.Session()
    per_page = 5
    pages = math.ceil(max_repos / per_page)

    for page in range(1, pages + 1):
        url = f"https://api.github.com/users/{username}/repos?sort=pushed&per_page={per_page}&page={page}"
        try:
            r = _req_with_retries(session, url, headers=headers)
            repos = r.json() or []
            for repo in repos:
                artifacts.append({
                    "type": "GITHUB_REPO",
                    "url": repo.get("html_url"),
                    "title": repo.get("name"),
                    "status": f"{repo.get('stargazers_count', 0)} stars",
                })
                if len(artifacts) >= max_repos:
                    break
            if len(artifacts) >= max_repos or not repos:
                break
        except requests.RequestException as e:
            log.warning("GitHub fetch failed (page %s): %s", page, e)
            break

    log.info("GitHub scrape collected %d artifacts", len(artifacts))
    return artifacts


# --- JD → skills via Bedrock ---

def extract_skills_from_jd(jd_text: str) -> dict:
    """Use a small Bedrock model; enforce strict JSON with fallback."""
    log.info("Extracting skills from JD via Bedrock…")
    br = SESSION.client("bedrock-runtime")
    prompt = (
        "You are an expert software engineering hiring manager. "
        "Analyze the job description and extract the key technical skills. "
        "For each skill, provide related code-level keywords, libraries, or tools. "
        "Respond ONLY with a JSON object like: "
        '{"Python": ["pandas","numpy"], "AWS": ["EC2","S3","Lambda"]}\n\n'
        f"Job Description:\n{jd_text}\n"
    )
    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 512,
        "messages": [{"role": "user", "content": prompt}],
    })
    try:
        resp = br.invoke_model(
            body=body,
            modelId=get_env("BEDROCK_MODEL_ID", default="anthropic.claude-3-haiku-20240307-v1:0", required=False),
            contentType="application/json",
            accept="application/json",
        )
        payload = json.loads(resp["body"].read())
        text = (payload.get("content", [{}])[0] or {}).get("text", "")
        # strip possible code fences
        text = text.strip()
        if text.startswith("```"):
            import re as _re
            text = _re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=_re.IGNORECASE | _re.MULTILINE)
        data = json.loads(text)
        # normalize dict[str, list[str]]
        norm = {
            str(skill).strip(): [str(x).strip() for x in (kw_list or []) if str(x).strip()]
            for skill, kw_list in data.items()
            if isinstance(kw_list, list)
        }
        log.info("JD skill extraction complete (%d skills).", len(norm))
        return norm
    except Exception as e:
        log.warning("Bedrock extraction failed, returning fallback: %s", e)
        return {
            "Python": ["python", "pandas", "numpy", "fastapi", "django"],
            "Cloud": ["aws", "azure", "gcp", "kubernetes", "docker", "terraform"],
            "Data": ["sql", "postgres", "snowflake", "spark", "etl"],
        }


# --- Heuristics ---

def calculate_heuristics(resume_text: str, artifacts: list, skill_map: dict) -> dict:
    """Simple keyword-density heuristic over resume text + artifact titles."""
    log.info("Calculating heuristics…")
    scores = {skill: 0 for skill in skill_map.keys()}
    corpus = (resume_text or "").lower()
    for a in artifacts or []:
        if a.get("title"):
            corpus += " " + a["title"].lower()

    for skill, keywords in skill_map.items():
        for kw in keywords:
            if kw:
                scores[skill] += corpus.count(kw.lower())

    return {"skill_counts": scores}


def insert_artifacts(engine: Engine, brief_id: str, artifacts: list[dict]) -> None:
    """Save scraped artifacts to DB (single transaction)."""
    if not artifacts:
        return
    log.info("Inserting %d artifacts for brief %s", len(artifacts), brief_id)
    with engine.begin() as conn:
        for art in artifacts:
            conn.execute(
                text(
                    """
                    INSERT INTO artifacts (candidate_id, type, url, title, status)
                    SELECT b.candidate_id, :type, :url, :title, :status
                    FROM briefs b WHERE b.id = :brief_id
                    """
                ),
                {**art, "brief_id": brief_id},
            )


# --- AWS Lambda Handler ---

def handler(event, context):
    """
    Orchestrates:
      1) Load resume & JD text from S3 (paths read from DB)
      2) In parallel: GitHub scrape + JD skill extraction
      3) Persist artifacts; compute heuristics
      4) Return enriched payload with S3 pointers for texts
    """
    brief_id = event["briefId"]
    github_url = (event.get("githubUrl") or "").strip()

    bucket = get_env("S3_BUCKET_NAME")
    engine = get_db_engine()

    # Get S3 keys from DB
    with engine.connect() as conn:
        row = conn.execute(
            text("""
                SELECT c.s3_processed_resume_path, j.s3_jd_path
                FROM briefs b
                JOIN candidates c ON b.candidate_id = c.id
                JOIN jobs j       ON b.job_id       = j.id
                WHERE b.id = :brief_id
            """),
            {"brief_id": brief_id},
        ).fetchone()

    if not row:
        raise ValueError(f"Could not find S3 paths for brief {brief_id}")

    processed_resume_key, jd_key = row
    resume_text = s3_get_text(bucket, processed_resume_key)
    jd_text = s3_get_text(bucket, jd_key)

    # Parallel tasks
    with ThreadPoolExecutor(max_workers=2) as executor:
        # GitHub
        if github_url:
            gh_token = get_secret("GITHUB_SECRET_ARN", "GITHUB_TOKEN")
            gh_future = executor.submit(scrape_github_profile, github_url, gh_token)
        else:
            gh_future = executor.submit(lambda: [])
        # Skills
        skills_future = executor.submit(extract_skills_from_jd, jd_text)

        artifacts = gh_future.result()
        skill_map = skills_future.result()

    insert_artifacts(engine, brief_id, artifacts)
    heuristic_scores = calculate_heuristics(resume_text, artifacts, skill_map)

    # Prefer returning pointers (Step Functions payload safety)
    return {
        "briefId": brief_id,
        "resumeTextS3": {"bucket": bucket, "key": processed_resume_key},
        "jdTextS3": {"bucket": bucket, "key": jd_key},
        "scrapedArtifacts": artifacts,
        "skillMap": skill_map,
        "heuristicScores": heuristic_scores,
    }
