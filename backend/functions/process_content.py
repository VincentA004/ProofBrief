# backend/functions/process_content/main.py

import json
import requests
from sqlalchemy import text
from sqlalchemy.engine import Engine
from concurrent.futures import ThreadPoolExecutor

# Import shared utilities
from shared.utils import (
    get_db_engine,
    get_secret,
    s3_get_text,
    get_env,
    log,
    SESSION
)

# --- Core Logic Functions ---

def scrape_github_profile(username: str, api_token: str) -> list[dict]:
    """Scrapes a GitHub user's profile for recent, relevant activity."""
    log.info(f"Scraping GitHub profile for user: {username}")
    headers = {"Authorization": f"Bearer {api_token}", "Accept": "application/vnd.github+json"}
    artifacts = []
    repos_url = f"https://api.github.com/users/{username}/repos?sort=pushed&per_page=5"
    try:
        repos_response = requests.get(repos_url, headers=headers)
        repos_response.raise_for_status()
        for repo in repos_response.json():
            artifacts.append({
                "type": "GITHUB_REPO", "url": repo.get('html_url'),
                "title": repo.get('name'), "status": f"{repo.get('stargazers_count', 0)} stars",
            })
    except requests.exceptions.RequestException as e:
        log.warning(f"Could not scrape GitHub repos: {e}")
    log.info(f"GitHub scrape complete. Found {len(artifacts)} artifacts.")
    return artifacts

def extract_skills_from_jd(jd_text: str) -> dict:
    """Uses a small, fast LLM to dynamically extract skills from a job description."""
    log.info("Extracting skills from JD via Bedrock…")
    bedrock = SESSION.client("bedrock-runtime")
    prompt = (
        "You are an expert software engineering hiring manager. Analyze the job description "
        "and extract the key technical skills. For each skill, provide related code-level "
        "keywords, libraries, or tools. Respond ONLY with a valid JSON object in the format "
        '{"Python": ["pandas","numpy"], "AWS": ["EC2","S3","Lambda"]}.\n\n'
        f"Job Description:\n{jd_text}\n"
    )
    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 2048,
        "messages": [{"role": "user", "content": prompt}]
    })
    try:
        response = bedrock.invoke_model(
            body=body, modelId="anthropic.claude-3-haiku-20240307-v1:0",
            contentType="application/json", accept="application/json"
        )
        response_body = json.loads(response.get("body").read())
        skill_map_text = response_body['content'][0]['text']
        log.info("JD skill extraction complete.")
        return json.loads(skill_map_text)
    except Exception as e:
        log.warning("Bedrock skill extraction failed, returning empty map: %s", e)
        return {}

def calculate_heuristics(resume_text: str, artifacts: list, skill_map: dict) -> dict:
    """Calculates objective scores using the dynamically generated skill map."""
    log.info("Calculating heuristics...")
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
    """Saves scraped artifacts to the database."""
    if not artifacts:
        return
    log.info(f"Inserting {len(artifacts)} artifacts into DB for brief {brief_id}")
    with engine.begin() as conn:
        for art in artifacts:
            conn.execute(text(
                """
                INSERT INTO artifacts (candidate_id, type, url, title, status)
                SELECT b.candidate_id, :type, :url, :title, :status
                FROM briefs b WHERE b.id = :brief_id
                """
            ), {**art, "brief_id": brief_id})


# --- AWS Lambda Handler ---
def handler(event, context):
    """Orchestrates parallel data gathering and heuristic calculation."""
    brief_id = event["briefId"]
    github_url = event.get("githubUrl")
    
    bucket = get_env("S3_BUCKET_NAME")
    engine = get_db_engine()

    # Get initial S3 data
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT c.s3_processed_resume_path, j.s3_jd_path FROM briefs b JOIN candidates c ON b.candidate_id = c.id JOIN jobs j ON b.job_id = j.id WHERE b.id = :brief_id"
        ), {"brief_id": brief_id}).fetchone()
    
    if not row:
        raise ValueError(f"Could not find S3 paths for brief {brief_id}")
    
    processed_resume_key, jd_key = row
    resume_text = s3_get_text(bucket, processed_resume_key)
    jd_text = s3_get_text(bucket, jd_key)

    # Run scraping and JD analysis in parallel for efficiency
    with ThreadPoolExecutor(max_workers=2) as executor:
        github_token = get_secret(get_env("GITHUB_SECRET_ARN"))["GITHUB_TOKEN"] if github_url else None
        scrape_future = executor.submit(scrape_github_profile, github_url.strip('/').split('/')[-1], github_token) if github_url else executor.submit(lambda: [])
        skill_map_future = executor.submit(extract_skills_from_jd, jd_text)
        
        artifacts = scrape_future.result()
        skill_map = skill_map_future.result()

    # Save artifacts and calculate final heuristics
    insert_artifacts(engine, brief_id, artifacts)
    heuristic_scores = calculate_heuristics(resume_text, artifacts, skill_map)

    # Package and return the enriched data for the next step
    return {
        "briefId": brief_id,
        "resumeText": resume_text,
        "jdText": jd_text,
        "scrapedArtifacts": artifacts,
        "skillMap": skill_map,
        "heuristicScores": heuristic_scores,
    }