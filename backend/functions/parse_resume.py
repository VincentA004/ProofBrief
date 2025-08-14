#!/usr/bin/env python3
import os
import io
import json
import time
import re
import urllib.parse
import boto3
from sqlalchemy import create_engine, text
from dotenv import load_dotenv
from pypdf import PdfReader
# pip install pypdf python-dotenv boto3 sqlalchemy aws-aurora-data-api-py (if you use DB)

# --- Helper function to build the Data API connection URL ---
def get_db_url():
    """Constructs the Data API URL from environment variables."""
    return (
        f"postgresql+auroradataapi://:@/postgres"
        f"?aurora_cluster_arn={os.environ['DB_CLUSTER_ARN']}"
        f"&secret_arn={os.environ['DB_SECRET_ARN']}"
    )

# -------------------------------
# S3/Textract utility helpers
# -------------------------------
def parse_s3_target(bucket_env: str, key_env: str):
    """
    Accepts either a raw key (preferred) or an s3/http(s) URL in TEST_RESUME_KEY.
    Returns (bucket, key).
    """
    if key_env and (key_env.startswith("s3://") or key_env.startswith("http")):
        u = urllib.parse.urlparse(key_env)
        if u.scheme == "s3":
            return u.netloc, u.path.lstrip("/")
        # https://<bucket>.s3.<region>.amazonaws.com/<key>
        host_parts = u.netloc.split(".")
        bucket = host_parts[0]
        key = u.path.lstrip("/")
        return bucket, key
    # Fall back to env bucket + raw key
    return bucket_env, key_env

def ensure_same_region(bucket: str) -> str:
    """
    Get the bucket's region. Use it when creating the Textract client so the
    job can read the object (Textract must be in the same region).
    """
    s3 = boto3.client("s3")
    loc = s3.get_bucket_location(Bucket=bucket).get("LocationConstraint")
    # AWS returns None for us-east-1
    return loc or "us-east-1"

def extract_pdf_hyperlinks_from_s3(s3_bucket: str, s3_key: str) -> list[str]:
    """
    Read the PDF bytes from S3 and return true hyperlink targets (/URI) from annotations.
    This captures the actual click-through URLs, which may differ from visible text.
    """
    s3 = boto3.client("s3")
    obj = s3.get_object(Bucket=s3_bucket, Key=s3_key)
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

# --- Core Logic Function (Testable) ---
def process_resume(s3_bucket: str, s3_key: str) -> dict:
    """
    Core logic to process a resume PDF using AWS Textract.
    Also extracts true hyperlink targets from PDF annotations.
    """
    # Preflight: ensure object exists and discover region
    s3_client = boto3.client("s3")
    s3_client.head_object(Bucket=s3_bucket, Key=s3_key)  # raises if not found
    bucket_region = ensure_same_region(s3_bucket)

    print(f"Starting Textract job for s3://{s3_bucket}/{s3_key} in region {bucket_region}")
    textract = boto3.client("textract", region_name=bucket_region)
    s3_object = {"Bucket": s3_bucket, "Name": s3_key}

    # 1) Start async Textract job
    response = textract.start_document_text_detection(DocumentLocation={"S3Object": s3_object})
    job_id = response["JobId"]
    print(f"Textract job started with ID: {job_id}")

    # 2) Wait for completion
    job_status = ""
    while job_status not in ("SUCCEEDED", "FAILED"):
        time.sleep(5)
        job_result = textract.get_document_text_detection(JobId=job_id)
        job_status = job_result["JobStatus"]
        print(f"Job status: {job_status}")
    if job_status == "FAILED":
        raise Exception("Textract job failed.")

    # 3) Paginate to gather all blocks
    blocks = job_result.get("Blocks", [])
    next_token = job_result.get("NextToken")
    while next_token:
        page_res = textract.get_document_text_detection(JobId=job_id, NextToken=next_token)
        blocks.extend(page_res.get("Blocks", []))
        next_token = page_res.get("NextToken")

    # 4) Build OCR text from lines
    full_text = "\n".join(b["Text"] for b in blocks if b.get("BlockType") == "LINE")

    # 5) URLs from visible text (OCR)
    ocr_urls = re.findall(r'https?://\S+', full_text)

    # 6) TRUE link targets from PDF annotations
    true_link_targets = extract_pdf_hyperlinks_from_s3(s3_bucket, s3_key)

    # 7) Save processed text back to S3
    processed_key = s3_key.replace("original", "processed").replace(".pdf", ".txt")
    s3_client.put_object(Bucket=s3_bucket, Key=processed_key, Body=full_text)
    print(f"Processed text saved to s3://{s3_bucket}/{processed_key}")

    # 8) Merge URLs (prefer true annotation targets)
    all_urls = list({*true_link_targets, *ocr_urls})
    github_url = next((u for u in all_urls if "github.com" in u), None)

    return {
        "processedResumePath": f"s3://{s3_bucket}/{processed_key}",
        "linkAnnotationUrls": true_link_targets,     # real clickable targets
        "visibleTextUrls": ocr_urls,                 # what Textract saw in text
        "urls": all_urls,                            # merged, deduped
        "githubUrl": github_url,
    }

# --- AWS Lambda Handler (The "Wrapper") ---
def handler(event, context):
    """
    Lambda handler that connects to the database, gets the S3 path,
    and calls the core logic function.
    (DB section kept as-is; you said local run ignores it.)
    """
    print(f"Received event: {json.dumps(event)}")
    brief_id = event["briefId"]

    engine = create_engine(get_db_url())
    with engine.connect() as connection:
        connection.execute(text(
            "UPDATE briefs SET status = 'PROCESSING', updated_at = NOW() WHERE id = :brief_id"
        ), {"brief_id": brief_id})
        result = connection.execute(text(
            "SELECT c.s3_resume_path FROM candidates c JOIN briefs b ON c.id = b.candidate_id WHERE b.id = :brief_id"
        ), {"brief_id": brief_id}).fetchone()
        connection.commit()

    if not result:
        raise ValueError(f"Could not find resume path for briefId: {brief_id}")

    full_s3_path = result[0]
    bucket_name = os.environ['S3_BUCKET_NAME']
    s3_key = full_s3_path  # assuming DB stores just the key

    processing_result = process_resume(bucket_name, s3_key)
    processing_result["briefId"] = brief_id
    return processing_result

# --- Local Testing Block ---
if __name__ == "__main__":
    print("--- Running Local Test ---")
    load_dotenv()

    raw_bucket = os.environ.get("S3_BUCKET_NAME")
    raw_key = os.environ.get("TEST_RESUME_KEY")   # can be raw key OR s3/http(s) URL

    if raw_bucket and raw_key:
        bucket, key = parse_s3_target(raw_bucket, raw_key)
        try:
            result = process_resume(bucket, key)
            print("\n--- Local Test Successful ---")
            print(json.dumps(result, indent=2))
        except Exception as e:
            print(f"\n--- Local Test Failed: {e} ---")
    else:
        print("Please set S3_BUCKET_NAME and TEST_RESUME_KEY in your .env")
