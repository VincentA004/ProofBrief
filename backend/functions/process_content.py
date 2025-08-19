# backend/functions/process_content.py

import json
import math
import time
import uuid
import hashlib
import requests
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import text
from sqlalchemy.engine import Engine

from shared.utils import (
    get_db_engine,
    get_secret,
    s3_get_text,
    get_env,
    log,
    SESSION,
)

# ---------- Helpers: GitHub ----------

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
            # Handle rate limit
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


def scrape_github_profile(username_or_url: str, api_token: str, *, max_repos=12) -> list[dict]:
    """Scrape public repos with simple retries and cap."""
    username = _username_from_url(username_or_url)
    if not username:
        return []
    log.info(f"Scraping GitHub for '{username}'")

    headers = {"Authorization": f"Bearer {api_token}", "Accept": "application/vnd.github+json"}
    artifacts: list[dict] = []
    session = requests.Session()
    per_page = 6
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


# ---------- Selection + Skills via Bedrock (single call) ----------

def select_repos_and_extract_skills(
    resume_text: str,
    jd_text: str,
    repo_urls: list[str],
    *,
    top_k: int = 3
) -> tuple[list[str], dict]:
    """
    Ask a Bedrock small model to:
      - select the most relevant repo URLs (subset of repo_urls)
      - produce a skill_map for heuristics
    Returns (selected_urls, skill_map).
    """
    if not repo_urls:
        return [], {
            "Python": ["python", "pandas", "numpy", "fastapi", "django"],
            "Cloud": ["aws", "azure", "gcp", "kubernetes", "docker", "terraform"],
            "Data": ["sql", "postgres", "snowflake", "spark", "etl"],
        }

    br = SESSION.client("bedrock-runtime")
    model_id = get_env("BEDROCK_MODEL_ID", default="anthropic.claude-3-haiku-20240307-v1:0", required=False)

    repo_list_text = "\n".join(f"- {u}" for u in repo_urls[:20])
    prompt = (
        "You are helping a recruiter evaluate a candidate.\n"
        "Given the resume, job description, and a list of the candidate's GitHub repos, "
        "return ONLY a JSON object with the exact shape:\n"
        '{\n'
        '  "selected_urls": ["https://github.com/owner/repo1", "..."],\n'
        '  "skill_map": {"Python": ["pandas","numpy"], "AWS": ["EC2","S3","Lambda"]}\n'
        '}\n\n'
        f"Select at most {top_k} URLs from the provided list that best match the resume projects AND are most relevant to the JD.\n"
        "Use conservative, concrete interpretation—prefer repos clearly connected to resume projects. "
        "skill_map should capture concrete libraries/tools/keywords to count in code and docs.\n\n"
        f"RESUME:\n{resume_text[:8000]}\n\n"
        f"JOB DESCRIPTION:\n{jd_text[:6000]}\n\n"
        f"REPOS:\n{repo_list_text}\n"
    )
    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 512,
        "messages": [{"role": "user", "content": prompt}],
    })
    try:
        resp = br.invoke_model(
            body=body,
            modelId=model_id,
            contentType="application/json",
            accept="application/json",
        )
        payload = json.loads(resp["body"].read())
        text_out = (payload.get("content", [{}])[0] or {}).get("text", "").strip()
        if text_out.startswith("```"):
            import re as _re
            text_out = _re.sub(r"^```(?:json)?\s*|\s*```$", "", text_out, flags=_re.IGNORECASE | _re.MULTILINE)
        obj = json.loads(text_out) if text_out else {}
        selected = obj.get("selected_urls") or []
        skill_map = obj.get("skill_map") or {}

        # sanitize
        allowed = set(repo_urls)
        selected = [u for u in selected if isinstance(u, str) and u in allowed][:top_k]
        # normalize skill_map -> dict[str, list[str]]
        norm = {}
        if isinstance(skill_map, dict):
            for k, v in skill_map.items():
                if not isinstance(v, list):
                    continue
                clean_list = [str(x).strip() for x in v if str(x).strip()]
                if clean_list:
                    norm[str(k).strip()] = clean_list
        if not norm:
            norm = {
                "Python": ["python", "pandas", "numpy", "fastapi", "django"],
                "Cloud": ["aws", "azure", "gcp", "kubernetes", "docker", "terraform"],
                "Data": ["sql", "postgres", "snowflake", "spark", "etl"],
            }
        return selected, norm
    except Exception as e:
        log.warning("Bedrock selection+skills failed: %s", e)
        # fallback: pick first K, default skill_map
        return repo_urls[:top_k], {
            "Python": ["python", "pandas", "numpy", "fastapi", "django"],
            "Cloud": ["aws", "azure", "gcp", "kubernetes", "docker", "terraform"],
            "Data": ["sql", "postgres", "snowflake", "spark", "etl"],
        }


# ---------- Fetch repo file contents ----------

TEXT_EXTS = {".md", ".txt", ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".go", ".rs", ".rb", ".cs",
             ".c", ".cpp", ".h", ".hpp", ".sh", ".yml", ".yaml", ".toml", ".json", ".sql"}


def _slug_from_repo_url(u: str) -> str:
    p = urlparse(u)
    path = (p.path or "/").strip("/").replace("/", "_")
    h = hashlib.sha1(u.encode("utf-8")).hexdigest()[:8]
    return f"{path}_{h}.txt"


def _download_repo_text_bundle(repo_url: str, token: str, *, max_files: int = 12, max_bytes: int = 80_000) -> str:
    """
    Heuristic: collect README + a handful of texty files from root and /src (size-capped).
    Returns a single concatenated text bundle (truncated to max_bytes).
    """
    try:
        owner, name = repo_url.rstrip("/").split("/")[-2:]
    except Exception:
        return ""

    session = requests.Session()
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}

    def gh_get(url, timeout=8):
        return _req_with_retries(session, url, headers, retries=4, timeout=timeout)

    chunks: list[str] = []

    # README
    try:
        r = gh_get(f"https://api.github.com/repos/{owner}/{name}/readme")
        j = r.json()
        if isinstance(j, dict) and j.get("download_url"):
            rr = gh_get(j["download_url"], timeout=12)
            chunks.append("\n# README\n")
            chunks.append(rr.text[:20_000])
    except Exception:
        pass

    # Root files
    files = []
    try:
        r = gh_get(f"https://api.github.com/repos/{owner}/{name}/contents/")
        items = r.json() or []
        for it in items:
            if it.get("type") == "file":
                path = it.get("path", "")
                ext = "." + path.rsplit(".", 1)[-1].lower() if "." in path else ""
                if path.lower().startswith(("readme", "license", "changelog")) or ext in TEXT_EXTS:
                    files.append({"path": path, "download_url": it.get("download_url")})
            if len(files) >= max_files // 2:
                break
    except Exception:
        pass

    # src/ files (if needed)
    if len(files) < max_files:
        try:
            r = gh_get(f"https://api.github.com/repos/{owner}/{name}/contents/src")
            items = r.json() or []
            for it in items:
                if it.get("type") == "file":
                    path = it.get("path", "")
                    ext = "." + path.rsplit(".", 1)[-1].lower() if "." in path else ""
                    if ext in TEXT_EXTS:
                        files.append({"path": path, "download_url": it.get("download_url")})
                        if len(files) >= max_files:
                            break
        except Exception:
            pass

    # Download with caps
    downloaded = 0
    for f in files[:max_files]:
        url = f.get("download_url")
        path = f.get("path")
        if not url or not path:
            continue
        try:
            rr = gh_get(url, timeout=10)
            content = rr.text
            if not content:
                continue
            if len(content) > 15_000:
                content = content[:15_000]
            chunks.append(f"\n# FILE: {path}\n")
            chunks.append(content)
            downloaded += len(content)
            if downloaded >= max_bytes:
                break
        except Exception:
            continue

    bundle = "".join(chunks)
    if len(bundle) > max_bytes:
        bundle = bundle[:max_bytes]
    return bundle


# ---------- Heuristics ----------

def calculate_heuristics(
    resume_text: str,
    artifacts: list,
    skill_map: dict,
    repo_bundles: list[str] | None = None
) -> dict:
    """Keyword count over resume + artifact titles + selected repo code/docs."""
    log.info("Calculating heuristics…")
    scores = {skill: 0 for skill in skill_map.keys()}
    corpus = (resume_text or "").lower()
    for a in artifacts or []:
        if a.get("title"):
            corpus += " " + a["title"].lower()
    if repo_bundles:
        for b in repo_bundles:
            if b:
                corpus += " " + b.lower()

    for skill, keywords in skill_map.items():
        for kw in keywords:
            if kw:
                scores[skill] += corpus.count(kw.lower())

    return {"skill_counts": scores}


# ---------- DB Insert ----------

def insert_artifacts(engine: Engine, brief_id: str, artifacts: list[dict]) -> None:
    """Save scraped artifacts to DB (single transaction)."""
    if not artifacts:
        return
    log.info("Inserting %d artifacts for brief %s", len(artifacts), brief_id)

    sql = text("""
        INSERT INTO artifacts (id, candidate_id, type, url, title, status, created_at)
        SELECT CAST(:id AS uuid), b.candidate_id, :type, :url, :title, :status, NOW()
        FROM briefs b
        WHERE b.id = CAST(:brief_id AS uuid)
    """)

    with engine.begin() as conn:
        for art in artifacts:
            params = {
                "id": str(uuid.uuid4()),
                "type": art.get("type"),
                "url": art.get("url"),
                "title": art.get("title"),
                "status": art.get("status"),
                "brief_id": brief_id,
            }
            conn.execute(sql, params)


# ---------- Lambda Handler ----------

def handler(event, context):
    """
    Pipeline:
      1) Load resume & JD text from S3 (paths from DB)
      2) Scrape GitHub repos (names/urls)
      3) Single Bedrock call -> (selected repo URLs, skill_map)
      4) Fetch selected repos' text bundles -> save to S3
      5) Persist artifacts; compute heuristics over resume + repo bundles
      6) Return S3 pointers (resume, jd, repo bundles) + scores
    """
    brief_id = event["briefId"]
    github_url = (event.get("githubUrl") or "").strip()

    bucket = get_env("S3_BUCKET_NAME")
    engine = get_db_engine()

    # 1) Get S3 keys from DB
    with engine.connect() as conn:
        row = conn.execute(
            text("""
                SELECT c.s3_processed_resume_path, j.s3_jd_path
                FROM briefs b
                JOIN candidates c ON b.candidate_id = c.id
                JOIN jobs j       ON b.job_id       = j.id
                WHERE b.id = CAST(:brief_id AS uuid)
            """),
            {"brief_id": brief_id},
        ).fetchone()

    if not row:
        raise ValueError(f"Could not find S3 paths for brief {brief_id}")

    processed_resume_key, jd_key = row
    resume_text = s3_get_text(bucket, processed_resume_key)
    jd_text = s3_get_text(bucket, jd_key)

    # 2) Scrape repos (if we have a GH profile)
    artifacts: list[dict] = []
    if github_url:
        gh_token = get_secret("GITHUB_SECRET_ARN", "GITHUB_TOKEN")
        artifacts = scrape_github_profile(github_url, gh_token, max_repos=12)

    repo_urls = [a["url"] for a in artifacts if a.get("type") == "GITHUB_REPO" and a.get("url")]

    # 3) Bedrock: choose repos + produce skill_map
    selected_urls, skill_map = select_repos_and_extract_skills(resume_text, jd_text, repo_urls, top_k=3)

    # 4) Download selected repos -> S3
    selected_repo_text_s3: list[dict] = []
    repo_bundles_for_scores: list[str] = []
    if selected_urls:
        gh_token = get_secret("GITHUB_SECRET_ARN", "GITHUB_TOKEN")
        s3 = SESSION.client("s3")
        for url in selected_urls:
            try:
                bundle = _download_repo_text_bundle(url, gh_token, max_files=12, max_bytes=80_000)
                if not bundle:
                    continue
                repo_bundles_for_scores.append(bundle)
                key = f"briefs/{brief_id}/repos/{_slug_from_repo_url(url)}"
                s3.put_object(
                    Bucket=bucket,
                    Key=key,
                    Body=bundle.encode("utf-8"),
                    ContentType="text/plain",
                )
                selected_repo_text_s3.append({"url": url, "s3": {"bucket": bucket, "key": key}})
            except Exception as e:
                log.warning("Repo bundle failed for %s: %s", url, e)

    # 5) Persist artifacts; compute heuristics
    insert_artifacts(engine, brief_id, artifacts)
    heuristic_scores = calculate_heuristics(resume_text, artifacts, skill_map, repo_bundles_for_scores)

    # 6) Return pointers
    return {
        "briefId": brief_id,
        "resumeTextS3": {"bucket": bucket, "key": processed_resume_key},
        "jdTextS3": {"bucket": bucket, "key": jd_key},
        "scrapedArtifacts": artifacts,               # all repos discovered
        "selectedRepoUrls": selected_urls,           # the chosen few
        "selectedRepoTextS3": selected_repo_text_s3, # [{url, s3:{bucket,key}}, ...]
        "skillMap": skill_map,                       # from the same Bedrock call
        "heuristicScores": heuristic_scores,
    }
