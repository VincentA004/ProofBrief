# backend/functions/parse_resume.py

import io
import json
import re
import time
from pathlib import PurePosixPath

from sqlalchemy import text
from pypdf import PdfReader

# Shared utilities
from shared.utils import get_db_engine, get_env, log, SESSION


# --- Helpers ---

def ensure_same_region(bucket: str) -> str:
    """Get the bucket's region (Textract must be in the same region)."""
    s3 = SESSION.client("s3")
    loc = s3.get_bucket_location(Bucket=bucket).get("LocationConstraint")
    return loc or "us-east-1"  # AWS returns None for us-east-1


def extract_pdf_hyperlinks_from_s3(s3_client, s3_bucket: str, s3_key: str) -> list[str]:
    """Read a PDF from S3 and extract true hyperlink targets from its annotations."""
    obj = s3_client.get_object(Bucket=s3_bucket, Key=s3_key)
    data = obj["Body"].read()

    reader = PdfReader(io.BytesIO(data))
    links: set[str] = set()

    for page in reader.pages:
        annots = page.get("/Annots") or []
        for annot_ref in annots:
            try:
                annot = annot_ref.get_object()
                if annot.get("/Subtype") == "/Link":
                    action = annot.get("/A")
                    if action and action.get("/S") == "/URI":
                        uri = action.get("/URI")
                        if isinstance(uri, str) and uri.strip():
                            links.add(uri.strip())
            except Exception:
                # Ignore malformed annotations
                pass
    return list(links)


def make_processed_keys(src_key: str) -> tuple[str, str]:
    """
    Given '.../resume_original.pdf', return:
      ('.../resume_processed.txt', '.../resume_textract.json')
    If the pattern isn't present, replace extension.
    """
    p = PurePosixPath(src_key)
    parent = p.parent
    stem = p.stem  # e.g., 'resume_original'
    txt_name = "resume_processed.txt" if "original" in stem else p.with_suffix(".txt").name
    json_name = "resume_textract.json" if "original" in stem else p.with_suffix(".json").name
    return str(parent / txt_name), str(parent / json_name)


def _clean_github_profile(url: str) -> str | None:
    """
    Normalize a GitHub profile URL.
    Returns canonical 'https://github.com/<username>' or None if not a profile link.
    """
    if not isinstance(url, str) or "github.com" not in url:
        return None
    # Trim common trailing OCR/annotation junk
    url = re.sub(r'[)\]\s>]+$', "", url.strip())
    m = re.match(r'https?://(?:www\.)?github\.com/([A-Za-z0-9-]+)(?:/|$)', url)
    if not m:
        return None
    username = m.group(1)
    return f"https://github.com/{username}"


# --- Core Logic ---

def process_resume(s3_bucket: str, s3_key: str) -> dict:
    """Process a resume PDF with Textract; save text + raw JSON; extract true URLs."""
    s3_client = SESSION.client("s3")
    # Fail fast for wrong key/permissions
    s3_client.head_object(Bucket=s3_bucket, Key=s3_key)

    bucket_region = ensure_same_region(s3_bucket)
    textract = SESSION.client("textract", region_name=bucket_region)

    log.info(f"Starting Textract for s3://{s3_bucket}/{s3_key}")
    resp = textract.start_document_text_detection(
        DocumentLocation={"S3Object": {"Bucket": s3_bucket, "Name": s3_key}}
    )
    job_id = resp["JobId"]

    # Wait for completion
    status = "IN_PROGRESS"
    job_result = {}
    while status not in ("SUCCEEDED", "FAILED"):
        time.sleep(5)
        job_result = textract.get_document_text_detection(JobId=job_id)
        status = job_result.get("JobStatus", "IN_PROGRESS")
        log.info(f"Textract job {job_id} status: {status}")

    if status == "FAILED":
        raise RuntimeError(f"Textract job {job_id} failed")

    # Gather all blocks (pagination)
    blocks = job_result.get("Blocks", []) or []
    next_token = job_result.get("NextToken")
    while next_token:
        page_res = textract.get_document_text_detection(JobId=job_id, NextToken=next_token)
        blocks.extend(page_res.get("Blocks", []) or [])
        next_token = page_res.get("NextToken")

    # Build OCR text (be defensive)
    full_text_lines = [b.get("Text", "") for b in blocks if b.get("BlockType") == "LINE" and b.get("Text")]
    full_text = "\n".join(full_text_lines)

    # Extract URLs: OCR-visible + true PDF link targets
    ocr_urls = re.findall(r'https?://[^\s)>\]]+', full_text) if full_text else []
    true_link_targets = extract_pdf_hyperlinks_from_s3(s3_client, s3_bucket, s3_key)

    # Prefer true PDF annotation targets; fallback to OCR text
    github_url = None
    for u in true_link_targets:
        cleaned = _clean_github_profile(u)
        if cleaned:
            github_url = cleaned
            break
    if not github_url:
        for u in ocr_urls:
            cleaned = _clean_github_profile(u)
            if cleaned:
                github_url = cleaned
                break

    # Save processed artifacts to S3
    processed_txt_key, textract_json_key = make_processed_keys(s3_key)
    s3_client.put_object(Bucket=s3_bucket, Key=processed_txt_key, Body=(full_text or "").encode("utf-8"))
    s3_client.put_object(Bucket=s3_bucket, Key=textract_json_key, Body=json.dumps({"Blocks": blocks}, indent=2).encode("utf-8"))
    log.info(f"Saved processed text to s3://{s3_bucket}/{processed_txt_key}")
    log.info(f"Saved Textract JSON to s3://{s3_bucket}/{textract_json_key}")

    return {
        "processedResumeTextKey": processed_txt_key,
        "processedTextractJsonKey": textract_json_key,
        "githubUrl": github_url,
    }


# --- AWS Lambda Handler ---

def handler(event, context):
    """Wrap core logic; persist processed path to DB for downstream steps."""
    log.info(f"Event: {json.dumps(event)}")
    brief_id = event["briefId"]

    engine = get_db_engine()
    bucket_name = get_env("S3_BUCKET_NAME")

    # Fetch resume S3 key
    with engine.connect() as conn:
        row = conn.execute(
            text("""
                SELECT c.s3_resume_path
                FROM candidates c
                JOIN briefs b ON c.id = b.candidate_id
                WHERE b.id = CAST(:brief_id AS uuid)
            """),
            {"brief_id": brief_id},
        ).fetchone()

    if not row:
        raise ValueError(f"Could not find resume path for briefId: {brief_id}")

    s3_key = row[0]

    # Run processing
    result = process_resume(bucket_name, s3_key)

    # Persist processed text path for later steps
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE candidates AS c
                SET s3_processed_resume_path = :path, updated_at = NOW()
                FROM briefs b
                WHERE b.candidate_id = c.id
                  AND b.id = CAST(:brief_id AS uuid)
                """
            ),
            {"path": result["processedResumeTextKey"], "brief_id": brief_id},
        )

    result["briefId"] = brief_id
    return result
