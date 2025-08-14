#!/usr/bin/env python3
import os
import io
import re
import json
import time
import math
import logging
import urllib.parse
from typing import Dict, List, Tuple, Optional

import boto3
from botocore.exceptions import BotoCoreError, ClientError
import requests
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor, as_completed

# -------------------------
# Logging
# -------------------------
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("brief-pipeline")

# -------------------------
# Env & AWS Session
# -------------------------
load_dotenv()  # safe locally; no-op in Lambda

def get_env(name: str, default: Optional[str] = None, required: bool = False) -> str:
    v = os.getenv(name, default)
    if required and not v:
        raise RuntimeError(f"Missing required env var: {name}")
    return v

AWS_REGION = get_env("AWS_REGION", default=os.getenv("AWS_DEFAULT_REGION", "us-east-1"))
SESSION = boto3.session.Session(region_name=AWS_REGION)

# -------------------------
# DB URL (Aurora Data API)
# -------------------------
def get_db_url() -> str:
    return (
        f"postgresql+auroradataapi://:@/postgres"
        f"?aurora_cluster_arn={get_env('DB_CLUSTER_ARN', required=True)}"
        f"&secret_arn={get_env('DB_SECRET_ARN', required=True)}"
    )

# -------------------------
# S3 helpers
# -------------------------
def parse_s3_target(bucket_env: str, key_or_url: str) -> Tuple[str, str]:
    """Allow raw key or s3/http(s) URL; return (bucket, key)."""
    if key_or_url.startswith("s3://"):
        u = urllib.parse.urlparse(key_or_url)
        return u.netloc, u.path.lstrip("/")
    if key_or_url.startswith("http"):
        u = urllib.parse.urlparse(key_or_url)
        bucket = u.netloc.split(".")[0]
        key = u.path.lstrip("/")
        return bucket, key
    return bucket_env, key_or_url

def s3_get_text(s3_client, bucket: str, key: str, *, encoding="utf-8") -> str:
    obj = s3_client.get_object(Bucket=bucket, Key=key)
    return obj["Body"].read().decode(encoding)

# -------------------------
# Secrets (cached)
# -------------------------
_GITHUB_TOKEN_CACHE: Optional[str] = None

def get_github_token() -> str:
    """Fetch GitHub PAT from Secrets Manager (cached)."""
    global _GITHUB_TOKEN_CACHE
    if _GITHUB_TOKEN_CACHE:
        return _GITHUB_TOKEN_CACHE
    secret_arn = get_env("GITHUB_SECRET_ARN", required=True)
    sm = SESSION.client("secretsmanager")
    try:
        resp = sm.get_secret_value(SecretId=secret_arn)
        token = json.loads(resp["SecretString"])["GITHUB_TOKEN"]
        _GITHUB_TOKEN_CACHE = token
        return token
    except Exception as e:
        log.error("Failed to fetch GitHub token: %s", e)
        raise

# -------------------------
# GitHub scraping (robust)
# -------------------------
def _req_with_retries(session: requests.Session, url: str, headers: dict, *, retries=4, timeout=8):
    backoff = 0.5
    for attempt in range(retries):
        try:
            resp = session.get(url, headers=headers, timeout=timeout)
            # Handle rate limit explicitly
            if resp.status_code == 403 and "rate limit" in resp.text.lower():
                reset = resp.headers.get("X-RateLimit-Reset")
                wait_s = max(1, int(reset) - int(time.time())) if reset else math.ceil(backoff)
                log.warning("GitHub rate-limited. Sleeping %ss", wait_s)
                time.sleep(wait_s)
                continue
            resp.raise_for_status()
            return resp
        except requests.RequestException as e:
            if attempt == retries - 1:
                raise
            time.sleep(backoff)
            backoff *= 2
    raise RuntimeError("Unreachable")

def _github_username_from_url(url_or_username: str) -> str:
    if not url_or_username:
        return ""
    if url_or_username.startswith("http"):
        return url_or_username.rstrip("/").split("/")[-1]
    return url_or_username

def scrape_github_profile(username_or_url: str, api_token: str, *, max_repos=10) -> List[dict]:
    """Scrape recent GitHub repos (public) with retry, pagination, and caps."""
    username = _github_username_from_url(username_or_url)
    if not username:
        return []
    log.info("Scraping GitHub for '%s'", username)

    headers = {"Authorization": f"Bearer {api_token}", "Accept": "application/vnd.github+json"}
    artifacts: List[dict] = []
    session = requests.Session()

    per_page = 5
    pages = math.ceil(max_repos / per_page)
    for page in range(1, pages + 1):
        url = f"https://api.github.com/users/{username}/repos?sort=pushed&per_page={per_page}&page={page}"
        try:
            r = _req_with_retries(session, url, headers=headers)
            repos = r.json()
            if not repos:
                break
            for repo in repos:
                artifacts.append({
                    "type": "GITHUB_REPO",
                    "url": repo.get("html_url"),
                    "title": repo.get("name"),
                    "status": f"{repo.get('stargazers_count', 0)} stars",
                })
                if len(artifacts) >= max_repos:
                    break
            if len(artifacts) >= max_repos:
                break
        except requests.RequestException as e:
            log.warning("GitHub fetch failed (page %s): %s", page, e)
            break

    log.info("GitHub scrape collected %d artifacts", len(artifacts))
    return artifacts

# -------------------------
# Bedrock skill extraction
# -------------------------
def extract_skills_from_jd(jd_text: str) -> Dict[str, List[str]]:
    """
    Use a small Bedrock model; enforce strict JSON, with a conservative fallback.
    """
    log.info("Extracting skills from JD via Bedrock…")
    br = SESSION.client("bedrock-runtime", region_name=get_env("BEDROCK_REGION", default=AWS_REGION))
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
            modelId=get_env("BEDROCK_MODEL_ID", default="anthropic.claude-3-haiku-20240307-v1:0"),
            contentType="application/json",
            accept="application/json",
        )
        payload = json.loads(resp["body"].read())
        text = payload["content"][0]["text"]
        # Strict JSON parse (strip code fences if any)
        text = text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE|re.MULTILINE)
        data = json.loads(text)
        # Normalize to {skill: [keywords]}
        norm: Dict[str, List[str]] = {
            str(skill).strip(): [str(x).strip() for x in (kw_list or [])]
            for skill, kw_list in data.items()
            if isinstance(kw_list, list)
        }
        log.info("JD skill extraction complete with %d skills", len(norm))
        return norm
    except Exception as e:
        log.warning("Bedrock extraction failed, falling back: %s", e)
        # Very conservative fallback: basic keyword buckets
        fallback = {
            "Python": ["python", "pandas", "numpy", "fastapi", "django"],
            "Cloud": ["aws", "azure", "gcp", "kubernetes", "docker", "terraform"],
            "Data": ["sql", "postgres", "snowflake", "spark", "etl"],
        }
        return fallback

# -------------------------
# Heuristic scoring
# -------------------------
def calculate_heuristics(resume_text: str, artifacts: List[dict], skill_map: Dict[str, List[str]]) -> Dict[str, dict]:
    log.info("Calculating heuristic scores…")
    scores = {skill: 0 for skill in skill_map.keys()}
    corpus = (resume_text or "").lower()
    for a in artifacts or []:
        if a.get("title"):
            corpus += " " + a["title"].lower()

    for skill, keywords in skill_map.items():
        for kw in keywords:
            if not kw:
                continue
            # simple count; you can upgrade to tokenized match or fuzzy
            scores[skill] += corpus.count(kw.lower())

    return {"skill_counts": scores}

# -------------------------
# SQL helpers
# -------------------------
def get_engine() -> Engine:
    return create_engine(get_db_url())

def insert_artifacts(engine: Engine, brief_id: int, artifacts: List[dict]) -> None:
    if not artifacts:
        return
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

# -------------------------
# Lambda Handler
# -------------------------
def handler(event, context):
    """
    Orchestrates:
      1) Load resume & JD from S3
      2) In parallel:
         - Scrape GitHub artifacts
         - Extract JD skills via Bedrock
      3) Store artifacts; compute heuristic scores
      4) Return enriched context
    """
    log.info("Event: %s", json.dumps(event))
    brief_id = event["briefId"]
    github_url = (event.get("githubUrl") or "").strip()

    bucket = get_env("S3_BUCKET_NAME", required=True)
    s3 = SESSION.client("s3")
    engine = get_engine()

    # Pull S3 keys from DB
    with engine.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT c.s3_processed_resume_path, j.s3_jd_path
                FROM briefs b
                JOIN candidates c ON b.candidate_id = c.id
                JOIN jobs j       ON b.job_id = j.id
                WHERE b.id = :brief_id
                """
            ),
            {"brief_id": brief_id},
        ).fetchone()

    if not row:
        raise ValueError(f"No S3 paths for brief {brief_id}")

    processed_resume_key, jd_key = row
    # Allow s3://… form or raw keys
    _, resume_key = parse_s3_target(bucket, processed_resume_key)
    _, jd_txt_key = parse_s3_target(bucket, jd_key)

    # Get texts
    resume_text = s3_get_text(s3, bucket, resume_key)
    jd_text = s3_get_text(s3, bucket, jd_txt_key)

    # Parallel: GitHub + Bedrock
    artifacts: List[dict] = []
    skill_map: Dict[str, List[str]] = {}

    with ThreadPoolExecutor(max_workers=2) as pool:
        futs = {}
        if github_url:
            futs["gh"] = pool.submit(scrape_github_profile, github_url, get_github_token())
        futs["jd"] = pool.submit(extract_skills_from_jd, jd_text)

        for name, fut in list(futs.items()):
            try:
                res = fut.result(timeout=45)
                if name == "gh":
                    artifacts = res or []
                else:
                    skill_map = res or {}
            except Exception as e:
                log.warning("Parallel task '%s' failed: %s", name, e)
                if name == "jd":
                    skill_map = skill_map or {}
                if name == "gh":
                    artifacts = artifacts or []

    # Store artifacts (single txn)
    try:
        insert_artifacts(engine, brief_id, artifacts)
    except Exception as e:
        log.warning("Failed to persist artifacts (continuing): %s", e)

    heuristic_scores = calculate_heuristics(resume_text, artifacts, skill_map)

    return {
        "briefId": brief_id,
        "resumeText": resume_text,  # consider truncating if very large
        "jdText": jd_text,
        "scrapedArtifacts": artifacts,
        "skillMap": skill_map,
        "heuristicScores": heuristic_scores,
    }

# -------------------------
# Local test
# -------------------------
if __name__ == "__main__":
    # Minimal local driver (expects DB + S3 set up in env)
    example = {
        "briefId": int(get_env("TEST_BRIEF_ID", default="1")),
        "githubUrl": get_env("TEST_GITHUB_URL", default=""),
    }
    out = handler(example, None)
    print(json.dumps(out, indent=2)[:2000])
