# backend/functions/parse_resume/main.py

import io
import json
import re
import time

from sqlalchemy import text
from pypdf import PdfReader

# Import shared utilities
from shared.utils import get_db_engine, get_env, log, SESSION

# --- Logic-Specific Helpers ---

def ensure_same_region(bucket: str) -> str:
    """Gets the bucket's region to ensure the Textract client is in the same region."""
    s3 = SESSION.client("s3")
    loc = s3.get_bucket_location(Bucket=bucket).get("LocationConstraint")
    return loc or "us-east-1" # AWS returns None for us-east-1

def extract_pdf_hyperlinks_from_s3(s3_client, s3_bucket: str, s3_key: str) -> list[str]:
    """Reads a PDF from S3 and extracts true hyperlink targets from its annotations."""
    obj = s3_client.get_object(Bucket=s3_bucket, Key=s3_key)
    data = obj["Body"].read()
    reader = PdfReader(io.BytesIO(data))
    links: set[str] = set()

    for page in reader.pages:
        if "/Annots" in page:
            for annot in page["/Annots"]:
                try:
                    annot_obj = annot.get_object()
                    if annot_obj.get("/Subtype") == "/Link" and "/A" in annot_obj:
                        action = annot_obj["/A"]
                        if action.get("/S") == "/URI":
                            links.add(action["/URI"])
                except Exception:
                    pass # Ignore malformed annotations
    return list(links)

# --- Core Logic Function ---

def process_resume(s3_bucket: str, s3_key: str) -> dict:
    """Core logic to process a resume PDF using AWS Textract."""
    s3_client = SESSION.client("s3")
    bucket_region = ensure_same_region(s3_bucket)
    textract = SESSION.client("textract", region_name=bucket_region)

    log.info(f"Starting Textract job for s3://{s3_bucket}/{s3_key}")
    response = textract.start_document_text_detection(
        DocumentLocation={"S3Object": {"Bucket": s3_bucket, "Name": s3_key}}
    )
    job_id = response["JobId"]

    # Wait for completion
    while True:
        time.sleep(5)
        job_result = textract.get_document_text_detection(JobId=job_id)
        status = job_result["JobStatus"]
        log.info(f"Textract job {job_id} status: {status}")
        if status in ("SUCCEEDED", "FAILED"):
            break
    if status == "FAILED":
        raise RuntimeError(f"Textract job {job_id} failed.")

    # Paginate and extract text
    blocks = job_result.get("Blocks", [])
    next_token = job_result.get("NextToken")
    while next_token:
        page_res = textract.get_document_text_detection(JobId=job_id, NextToken=next_token)
        blocks.extend(page_res.get("Blocks", []))
        next_token = page_res.get("NextToken")
        
    full_text = "\n".join(b["Text"] for b in blocks if b.get("BlockType") == "LINE")
    
    # Save processed text and extract links
    processed_key = s3_key.replace("resume_original.pdf", "resume_processed.json")
    s3_client.put_object(
        Bucket=s3_bucket, Key=processed_key, Body=json.dumps(job_result, indent=2)
    )
    
    ocr_urls = re.findall(r'https?://\S+', full_text)
    true_link_targets = extract_pdf_hyperlinks_from_s3(s3_client, s3_bucket, s3_key)
    
    all_urls = list({*true_link_targets, *ocr_urls})
    github_url = next((u for u in all_urls if "github.com" in u), None)
    
    return {
        "processedResumePath": processed_key,
        "githubUrl": github_url
    }

# --- AWS Lambda Handler ---

def handler(event, context):
    """Lambda handler that wraps the core resume processing logic."""
    log.info(f"Event: {json.dumps(event)}")
    brief_id = event["briefId"]
    
    engine = get_db_engine()
    bucket_name = get_env("S3_BUCKET_NAME")

    with engine.begin() as connection:
        connection.execute(text(
            "UPDATE briefs SET status = 'PROCESSING', updated_at = NOW() WHERE id = :brief_id"
        ), {"brief_id": brief_id})
        
        result = connection.execute(text(
            "SELECT c.s3_resume_path FROM candidates c JOIN briefs b ON c.id = b.candidate_id WHERE b.id = :brief_id"
        ), {"brief_id": brief_id}).fetchone()

    if not result:
        raise ValueError(f"Could not find resume path for briefId: {brief_id}")
    
    s3_key = result[0]
    
    # Call the core, testable logic
    processing_result = process_resume(bucket_name, s3_key)
    
    # Pass briefId to the next step in the workflow
    processing_result["briefId"] = brief_id
    
    return processing_result