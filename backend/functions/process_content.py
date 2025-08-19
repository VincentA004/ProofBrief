# backend/functions/process_content.py

import base64
import json
import math
import re
import time
import uuid
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Tuple, Optional

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

# -------------------------------
# GitHub helpers
# -------------------------------

_CODE_EXTS = {
    ".py", ".ipynb", ".js", ".jsx", ".ts", ".tsx",
    ".java", ".kt", ".scala", ".go", ".rs", ".rb",
    ".php", ".c", ".h", ".cpp", ".cc", ".hpp", ".cs",
    ".swift", ".m", ".mm", ".sql", ".sh", ".bash", ".ps1",
}

_SKIP_PATH_PATTERNS = [
    r"(^|/)\.",                 # dotfolders/files (.git, .github)
    r"(^|/)node_modules(/|$)",
    r"(^|/)dist(/|$)",
    r"(^|/)build(/|$)",
    r"(^|/)venv(/|$)",
    r"(^|/)__pycache__(/|$)",
    r"(^|/)target(/|$)",
    r"(^|/)bin(/|$)",
    r"(^|/)obj(/|$)",
    r"(^|/)coverage(/|$)",
    r"(^|/)site-packages(/|$)",
    r"(^|/)vendor(/|$)",
    r"(^|/)docs?(/|$)",
    r"(^|/)test(s)?(/|$)",
    r"(^|/)example(s)?(/|$)",
]


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


def _owner_repo_from_url(repo_url: str) -> Optional[Tuple[str, str]]:
    try:
        if not repo_url.startswith("http"):
            return None
        parts = repo_url.rstrip("/").split("/")
        owner, repo = parts[-2], parts[-1]
        return owner, repo
    except Exception:
        return None


def scrape_github_profile(username_or_url: str, api_token: str, *, max_repos=10) -> List[Dict]:
    """List public repos (basic metadata)."""
    username = _username_from_url(username_or_url)
    if not username:
        return []
    log.info(f"[process_content] Scraping GitHub for '{username}' (max {max_repos})")

    headers = {"Authorization": f"Bearer {api_token}", "Accept": "application/vnd.github+json"}
    artifacts: List[Dict] = []
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
                    "default_branch": repo.get("default_branch") or "main",
                    "pushed_at": repo.get("pushed_at"),
                    "stargazers_count": repo.get("stargazers_count", 0),
                    "language": repo.get("language"),
                })
                if len(artifacts) >= max_repos:
                    break
            if len(artifacts) >= max_repos or not repos:
                break
        except requests.RequestException as e:
            log.warning("GitHub fetch failed (page %s): %s", page, e)
            break

    log.info("[process_content] GitHub scrape collected %d artifacts", len(artifacts))
    return artifacts


# -------------------------------
# Bedrock: Skill extraction + repo selection
# -------------------------------

def extract_skills_from_jd(jd_text: str) -> Dict:
    """Use a small Bedrock model; enforce strict JSON with fallback."""
    log.info("[process_content] Extracting skills from JD via Bedrock…")
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
        model_id = get_env("BEDROCK_MODEL_ID", default="anthropic.claude-3-haiku-20240307-v1:0", required=False)
        resp = br.invoke_model(
            body=body,
            modelId=model_id,
            contentType="application/json",
            accept="application/json",
        )
        payload = json.loads(resp["body"].read())
        text = (payload.get("content", [{}])[0] or {}).get("text", "")
        text = text.strip()
        if text.startswith("```"):
            import re as _re
            text = _re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=_re.IGNORECASE | _re.MULTILINE)
        data = json.loads(text)
        norm = {
            str(skill).strip(): [str(x).strip() for x in (kw_list or []) if str(x).strip()]
            for skill, kw_list in data.items()
            if isinstance(kw_list, list)
        }
        log.info("[process_content] JD skill extraction complete (%d skills).", len(norm))
        return norm
    except Exception as e:
        log.warning("[process_content] Bedrock extraction failed, using fallback skills: %s", e)
        return {
            "Python": ["python", "pandas", "numpy", "fastapi", "django"],
            "Cloud": ["aws", "azure", "gcp", "kubernetes", "docker", "terraform"],
            "Data": ["sql", "postgres", "snowflake", "spark", "etl"],
        }


def _select_repos_with_llm(resume_text: str, repo_urls: List[str], max_pick: int = 3) -> List[str]:
    """
    Ask Haiku to pick repos that best match projects on the resume, plus 1 extra if appropriate.
    Returns a list of repo URLs (subset of repo_urls).
    """
    log.info("[process_content] Selecting repos with LLM from %d candidates (max_pick=%d).",
             len(repo_urls), max_pick)
    if not repo_urls:
        return []

    br = SESSION.client("bedrock-runtime")
    prompt = (
        "You will be given a candidate resume and a list of that candidate's GitHub repositories.\n"
        "Pick the repositories that best map to the projects described in the resume. "
        f"Return ONLY a compact JSON array (no extra text) of up to {max_pick} repo URLs from the list. "
        "Prefer repos that showcase skills, complexity, and recent activity. If nothing matches, "
        "return an empty array [].\n\n"
        "Resume:\n"
        f"{resume_text[:6000]}\n\n"
        "Repo URLs:\n"
        + "\n".join(repo_urls)
    )

    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 256,
        "messages": [{"role": "user", "content": prompt}],
    })
    try:
        model_id = get_env("BEDROCK_MODEL_ID", default="anthropic.claude-3-haiku-20240307-v1:0", required=False)
        resp = br.invoke_model(
            body=body,
            modelId=model_id,
            contentType="application/json",
            accept="application/json",
        )
        payload = json.loads(resp["body"].read())
        text = (payload.get("content", [{}])[0] or {}).get("text", "").strip()
        if text.startswith("```"):
            import re as _re
            text = _re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=_re.IGNORECASE | _re.MULTILINE)
        picks = json.loads(text)
        picks = [u for u in picks if isinstance(u, str) and u in repo_urls]
        log.info("[process_content] LLM selected %d repos.", len(picks))
        return picks[:max_pick]
    except Exception as e:
        log.warning("[process_content] Repo selection via LLM failed: %s. Falling back to first %d.", e, max_pick)
        return repo_urls[:max_pick]


# -------------------------------
# GitHub: fetch README + key code files
# -------------------------------

def _should_skip_path(path: str) -> bool:
    for pat in _SKIP_PATH_PATTERNS:
        if re.search(pat, path, flags=re.IGNORECASE):
            return True
    return False


def _get_default_branch(session: requests.Session, headers: dict, owner: str, repo: str) -> str:
    url = f"https://api.github.com/repos/{owner}/{repo}"
    r = _req_with_retries(session, url, headers=headers)
    data = r.json() or {}
    return data.get("default_branch") or "main"


def _get_tree(session: requests.Session, headers: dict, owner: str, repo: str, ref: str) -> List[Dict]:
    url = f"https://api.github.com/repos/{owner}/{repo}/git/trees/{ref}?recursive=1"
    r = _req_with_retries(session, url, headers=headers, timeout=12)
    data = r.json() or {}
    if data.get("truncated"):
        log.warning("[process_content] Git tree is truncated for %s/%s at %s", owner, repo, ref)
    return data.get("tree", []) or []


def _get_readme(session: requests.Session, headers: dict, owner: str, repo: str, ref: str) -> str:
    # Prefer the dedicated /readme endpoint
    url = f"https://api.github.com/repos/{owner}/{repo}/readme"
    try:
        r = _req_with_retries(session, url, headers=headers, timeout=8)
        data = r.json() or {}
        content = data.get("content")
        if content:
            return base64.b64decode(content).decode("utf-8", errors="replace")
    except Exception:
        pass

    # Fallback common filenames
    for name in ["README.md", "README.MD", "Readme.md", "readme.md", "README", "readme"]:
        url2 = f"https://api.github.com/repos/{owner}/{repo}/contents/{name}?ref={ref}"
        try:
            r2 = _req_with_retries(session, url2, headers=headers, timeout=8)
            data2 = r2.json() or {}
            content = data2.get("content")
            if content:
                return base64.b64decode(content).decode("utf-8", errors="replace")
        except Exception:
            continue
    return ""


def _fetch_code_file(session: requests.Session, headers: dict, owner: str, repo: str, path: str, ref: str) -> Optional[str]:
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}?ref={ref}"
    try:
        r = _req_with_retries(session, url, headers=headers, timeout=10)
        j = r.json() or {}
        if isinstance(j, dict) and j.get("type") == "file" and j.get("encoding") == "base64" and j.get("content"):
            raw = base64.b64decode(j["content"])
            # Protect Lambda memory: only allow up to 64KB per file
            return raw[:64 * 1024].decode("utf-8", errors="replace")
    except Exception as e:
        log.warning("[process_content] Failed to fetch file %s in %s/%s: %s", path, owner, repo, e)
    return None


def _choose_code_paths(tree: List[Dict], max_files: int = 5) -> List[str]:
    files = []
    for node in tree:
        if node.get("type") != "blob":
            continue
        path = node.get("path") or ""
        if not path or _should_skip_path(path):
            continue
        ext = "." + path.rsplit(".", 1)[-1] if "." in path else ""
        if ext.lower() in _CODE_EXTS:
            size = int(node.get("size") or 0)
            files.append((path, size))
    # Choose larger files first (tend to be more substantive), but cap by count
    files.sort(key=lambda t: t[1], reverse=True)
    return [p for p, _ in files[:max_files]]


def _bundle_repo_text(session: requests.Session, headers: dict, repo_url: str) -> Tuple[str, List[str]]:
    owner_repo = _owner_repo_from_url(repo_url)
    if not owner_repo:
        return "", []
    owner, repo = owner_repo

    try:
        default_ref = _get_default_branch(session, headers, owner, repo)
        tree = _get_tree(session, headers, owner, repo, default_ref)
        code_paths = _choose_code_paths(tree, max_files=5)
        readme = _get_readme(session, headers, owner, repo, default_ref)
        parts = []
        if readme:
            parts.append(f"# README ({owner}/{repo})\n{readme}\n")
        for pth in code_paths:
            content = _fetch_code_file(session, headers, owner, repo, pth, default_ref)
            if content:
                parts.append(f"\n# FILE: {pth}\n{content}\n")
        bundle = "\n".join(parts).strip()
        return bundle, code_paths
    except Exception as e:
        log.warning("[process_content] Failed bundling repo %s: %s", repo_url, e)
        return "", []


def _save_repo_bundle_to_s3(bucket: str, brief_id: str, repo_url: str, bundle_text: str) -> str:
    key_safe = repo_url.replace("https://github.com/", "").replace("/", "__")
    key = f"briefs/{brief_id}/repos/{key_safe}.txt"
    s3 = SESSION.client("s3")
    s3.put_object(Bucket=bucket, Key=key, Body=bundle_text.encode("utf-8"), ContentType="text/plain")
    return key


# -------------------------------
# Heuristics
# -------------------------------

def calculate_heuristics(resume_text: str, artifacts: List[Dict], skill_map: Dict, extra_corpora: Optional[List[str]] = None) -> Dict:
    """Simple keyword-density heuristic over resume text + artifact titles + optional code text corpora."""
    log.info("[process_content] Calculating heuristics…")
    scores = {skill: 0 for skill in skill_map.keys()}
    corpus = (resume_text or "").lower()
    for a in artifacts or []:
        if a.get("title"):
            corpus += " " + a["title"].lower()
    if extra_corpora:
        for blob in extra_corpora:
            if blob:
                # down-case and lightly normalize
                corpus += " " + blob.lower()

    for skill, keywords in skill_map.items():
        for kw in keywords:
            if kw:
                scores[skill] += corpus.count(kw.lower())

    return {"skill_counts": scores}


def insert_artifacts(engine: Engine, brief_id: str, artifacts: List[Dict]) -> None:
    """Save scraped artifacts to DB (single transaction)."""
    if not artifacts:
        return
    log.info("[process_content] Inserting %d artifacts for brief %s", len(artifacts), brief_id)
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


# -------------------------------
# AWS Lambda Handler
# -------------------------------

def handler(event, context):
    """
    Orchestrates:
      1) Load resume & JD text from S3 (paths read from DB)
      2) GitHub scrape; pick best-matching repos with Haiku
      3) Fetch README + key code files for those repos; save to S3
      4) Persist artifacts; compute heuristics (including code)
      5) Return enriched payload with S3 pointers for texts + repo bundles
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
                WHERE b.id = CAST(:brief_id AS uuid)
            """),
            {"brief_id": brief_id},
        ).fetchone()

    if not row:
        raise ValueError(f"Could not find S3 paths for brief {brief_id}")

    processed_resume_key, jd_key = row
    resume_text = s3_get_text(bucket, processed_resume_key)
    jd_text = s3_get_text(bucket, jd_key)

    # --- Parallel: GitHub scrape + JD skills
    with ThreadPoolExecutor(max_workers=2) as executor:
        if github_url:
            gh_token = get_secret("GITHUB_SECRET_ARN", "GITHUB_TOKEN")
            gh_future = executor.submit(scrape_github_profile, github_url, gh_token, max_repos=20)
        else:
            gh_future = executor.submit(lambda: [])
        skills_future = executor.submit(extract_skills_from_jd, jd_text)

        artifacts = gh_future.result()
        skill_map = skills_future.result()

    # Persist raw repo list
    insert_artifacts(engine, brief_id, artifacts)

    # --- LLM selection of best repos
    repo_urls = [a["url"] for a in artifacts if a.get("url")]
    selected_repo_urls = _select_repos_with_llm(resume_text, repo_urls, max_pick=3)

    # Save selected picks as artifacts too (optional)
    selected_artifacts = [
        {"type": "GITHUB_REPO_SELECTED", "url": u, "title": u.split("/")[-1], "status": "selected"}
        for u in selected_repo_urls
    ]
    insert_artifacts(engine, brief_id, selected_artifacts)

    # --- Fetch README + key code files; save bundles to S3
    repo_bundles = []  # [{repoUrl, textS3:{bucket,key}, files:[paths...], size}]
    extra_corpora = []
    if selected_repo_urls:
        session = requests.Session()
        gh_token = get_secret("GITHUB_SECRET_ARN", "GITHUB_TOKEN")
        headers = {"Authorization": f"Bearer {gh_token}", "Accept": "application/vnd.github+json"}

        futures = {}
        with ThreadPoolExecutor(max_workers=3) as pool:
            for url in selected_repo_urls:
                futures[pool.submit(_bundle_repo_text, session, headers, url)] = url

            for fut in as_completed(futures):
                repo_url = futures[fut]
                bundle_text, files = fut.result()
                if not bundle_text:
                    log.info("[process_content] Empty bundle for %s", repo_url)
                    continue
                key = _save_repo_bundle_to_s3(bucket, brief_id, repo_url, bundle_text)
                repo_bundles.append({
                    "repoUrl": repo_url,
                    "textS3": {"bucket": bucket, "key": key},
                    "files": files,
                    "size": len(bundle_text),
                })
                extra_corpora.append(bundle_text)

    # --- Heuristics including code text
    heuristic_scores = calculate_heuristics(resume_text, artifacts, skill_map, extra_corpora=extra_corpora)

    # Return pointers
    return {
        "briefId": brief_id,
        "resumeTextS3": {"bucket": bucket, "key": processed_resume_key},
        "jdTextS3": {"bucket": bucket, "key": jd_key},
        "scrapedArtifacts": artifacts,
        "selectedRepoUrls": selected_repo_urls,
        "repoBundles": repo_bundles,  # each has textS3 pointer
        "skillMap": skill_map,
        "heuristicScores": heuristic_scores,
    }
