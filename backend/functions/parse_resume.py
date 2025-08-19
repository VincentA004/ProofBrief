# backend/functions/parse_resume.py

import io
import json
import re
import time
import traceback
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
    region = loc or "us-east-1"  # AWS returns None for us-east-1
    log.info(f"[parse_resume] ensure_same_region: bucket={bucket} region={region}")
    return region


def extract_pdf_hyperlinks_from_s3(s3_client, s3_bucket: str, s3_key: str) -> list[str]:
    """Read a PDF from S3 and extract true hyperlink targets from its annotations."""
    log.info(f"[parse_resume] extract_pdf_hyperlinks_from_s3: s3://{s3_bucket}/{s3_key}")
    obj = s3_client.get_object(Bucket=s3_bucket, Key=s3_key)
    data = obj["Body"].read()
    log.info(f"[parse_resume] PDF bytes fetched: {len(data)} bytes")

    reader = PdfReader(io.BytesIO(data))
    links: set[str] = set()
    page_count = len(reader.pages)
    log.info(f"[parse_resume] PDF page count (from pypdf): {page_count}")

    for i, page in enumerate(reader.pages, start=1):
        annots = page.get("/Annots") or []
        if annots:
            log.debug(f"[parse_resume] Page {i}: {len(annots)} annotations")
        for annot_ref in annots:
            try:
                annot = annot_ref.get_object()
                if annot.get("/Subtype") == "/Link":
                    action = annot.get("/A")
                    if action and action.get("/S") == "/URI":
                        uri = action.get("/URI")
                        if isinstance(uri, str) and uri.strip():
                            links.add(uri.strip())
            except Exception as e:
                log.warning(f"[parse_resume] Malformed annotation on page {i}: {e}")
    link_list = list(links)
    log.info(f"[parse_resume] Found {len(link_list)} true PDF link targets")
    if link_list:
        log.debug(f"[parse_resume] PDF link targets (sample up to 5): {link_list[:5]}")
    return link_list


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
    txt_key = str(parent / txt_name)
    json_key = str(parent / json_name)
    log.info(f"[parse_resume] make_processed_keys: txt={txt_key} json={json_key}")
    return txt_key, json_key


def _clean_github_profile(url: str) -> str | None:
    """
    Normalize a GitHub profile URL.
    Returns canonical 'https://github.com/<username>' or None if not a profile link.
    """
    if not isinstance(url, str) or "github.com" not in url:
        return None
    orig = url
    url = re.sub(r'[)\]\s>]+$', "", url.strip())
    m = re.match(r'https?://(?:www\.)?github\.com/([A-Za-z0-9-]+)(?:/|$)', url)
    if not m:
        return None
    username = m.group(1)
    cleaned = f"https://github.com/{username}"
    if cleaned != orig:
        log.debug(f"[parse_resume] Cleaned GitHub URL: {orig} -> {cleaned}")
    return cleaned


# --- Core Logic ---

def process_resume(s3_bucket: str, s3_key: str) -> dict:
    """Process a resume PDF with Textract; save text + raw JSON; extract true URLs."""
    started = time.time()
    s3_client = SESSION.client("s3")

    # Fail fast for wrong key/permissions + log object meta
    head = s3_client.head_object(Bucket=s3_bucket, Key=s3_key)
    size = head.get("ContentLength", -1)
    etag = head.get("ETag")
    log.info(f"[parse_resume] head_object ok: s3://{s3_bucket}/{s3_key} size={size} etag={etag}")

    bucket_region = ensure_same_region(s3_bucket)
    textract = SESSION.client("textract", region_name=bucket_region)

    log.info(f"[parse_resume] Starting Textract for s3://{s3_bucket}/{s3_key}")
    resp = textract.start_document_text_detection(
        DocumentLocation={"S3Object": {"Bucket": s3_bucket, "Name": s3_key}}
    )
    job_id = resp["JobId"]
    log.info(f"[parse_resume] Textract JobId: {job_id}")

    # Wait for completion
    poll_started = time.time()
    status = "IN_PROGRESS"
    job_result = {}
    poll_iters = 0
    while status not in ("SUCCEEDED", "FAILED"):
        time.sleep(5)
        poll_iters += 1
        job_result = textract.get_document_text_detection(JobId=job_id)
        status = job_result.get("JobStatus", "IN_PROGRESS")
        pct = job_result.get("JobStatus", "")
        log.info(
            f"[parse_resume] Poll {poll_iters}: status={status} "
            f"elapsed={int(time.time()-poll_started)}s totalElapsed={int(time.time()-started)}s"
        )

    if status == "FAILED":
        log.error(f"[parse_resume] Textract job failed. JobId={job_id} result={json.dumps(job_result)[:2000]}")
        raise RuntimeError(f"Textract job {job_id} failed")

    # Gather all blocks (pagination)
    blocks = job_result.get("Blocks", []) or []
    total_blocks = len(blocks)
    next_token = job_result.get("NextToken")
    page_fetches = 0
    while next_token:
        page_fetches += 1
        page_res = textract.get_document_text_detection(JobId=job_id, NextToken=next_token)
        page_blocks = page_res.get("Blocks", []) or []
        blocks.extend(page_blocks)
        total_blocks += len(page_blocks)
        next_token = page_res.get("NextToken")
        log.info(f"[parse_resume] Pagination fetch {page_fetches}: +{len(page_blocks)} blocks (total={total_blocks})")

    # Build OCR text (be defensive)
    full_text_lines = [b.get("Text", "") for b in blocks if b.get("BlockType") == "LINE" and b.get("Text")]
    full_text = "\n".join(full_text_lines)
    log.info(
        f"[parse_resume] OCR aggregation: lines={len(full_text_lines)} chars={len(full_text)} "
        f"blocks_total={total_blocks}"
    )

    # Extract URLs: OCR-visible + true PDF link targets
    ocr_urls = re.findall(r'https?://[^\s)>\]]+', full_text) if full_text else []
    log.info(f"[parse_resume] OCR-visible URLs: count={len(ocr_urls)}")
    if ocr_urls:
        log.debug(f"[parse_resume] OCR URL sample (up to 5): {ocr_urls[:5]}")

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
    log.info(f"[parse_resume] Final chosen GitHub URL: {github_url}")

    # Save processed artifacts to S3
    processed_txt_key, textract_json_key = make_processed_keys(s3_key)
    s3_client.put_object(Bucket=s3_bucket, Key=processed_txt_key, Body=(full_text or "").encode("utf-8"))
    s3_client.put_object(
        Bucket=s3_bucket,
        Key=textract_json_key,
        Body=json.dumps({"Blocks": blocks}, indent=2).encode("utf-8"),
        ContentType="application/json",
    )
    log.info(f"[parse_resume] Saved processed text -> s3://{s3_bucket}/{processed_txt_key}")
    log.info(f"[parse_resume] Saved Textract JSON -> s3://{s3_bucket}/{textract_json_key}")
    log.info(f"[parse_resume] Total elapsed: {int(time.time()-started)}s")

    return {
        "processedResumeTextKey": processed_txt_key,
        "processedTextractJsonKey": textract_json_key,
        "githubUrl": github_url,
    }


# --- AWS Lambda Handler ---

def handler(event, context):
    """Wrap core logic; persist processed path to DB for downstream steps."""
    # Correlate logs by briefId for easier searching
    try:
        log.info(f"[parse_resume] Handler start. Event={json.dumps(event)[:1000]}")
        brief_id = event["briefId"]
        log.info(f"[parse_resume] briefId={brief_id}")

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
            log.error(f"[parse_resume] No DB row for briefId={brief_id}")
            raise ValueError(f"Could not find resume path for briefId: {brief_id}")

        s3_key = row[0]
        log.info(f"[parse_resume] Found resume key: s3://{bucket_name}/{s3_key}")

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
        log.info(f"[parse_resume] Updated candidate with processed path for briefId={brief_id}")

        result["briefId"] = brief_id
        log.info(f"[parse_resume] Handler success for briefId={brief_id}")
        return result

    except Exception as e:
        # Log full stack for CloudWatch debugging
        log.error(f"[parse_resume] Handler exception: {e}")
        log.error(traceback.format_exc())
        # Re-raise so the Step Function sees the failure
        raise
