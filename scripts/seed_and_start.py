#!/usr/bin/env python3
import argparse, json, os, sys, uuid, pathlib, urllib.request, urllib.error
from datetime import datetime
import boto3
from sqlalchemy import create_engine, text

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUTF = ROOT / "cdk-outputs.json"

def read_outputs(stack: str):
    with open(OUTF, "r") as f:
        data = json.load(f)
    o = data[stack]
    return {
        "bucket": o["S3BucketName"],
        "cluster_arn": o["DatabaseClusterARN"],
        "db_secret_arn": o["DatabaseSecretARN"],
        "api_url": o["ApiUrl"],
        "state_machine_arn": o["StateMachineArn"],
    }

def engine_from_data_api(cluster_arn: str, secret_arn: str, dbname: str, region: str):
    # Force region to match the cluster’s region (prevents us-west-2/us-east-1 drift)
    if region:
        os.environ["AWS_REGION"] = region
        os.environ["AWS_DEFAULT_REGION"] = region
    url = (
        f"postgresql+auroradataapi://:@/{dbname}"
        f"?aurora_cluster_arn={cluster_arn}"
        f"&secret_arn={secret_arn}"
    )
    return create_engine(url)

def s3_upload(bucket: str, key: str, path: str, region: str):
    s3 = boto3.client("s3", region_name=region or os.getenv("AWS_REGION") or "us-east-1")
    s3.upload_file(path, bucket, key)

def http_post_json(url: str, payload: dict, timeout=30):
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type":"application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))

def upsert_user(conn, email: str):
    user_id = str(uuid.uuid4())
    cognito_id = f"local-{uuid.uuid4()}"
    # idempotent on email
    row = conn.execute(text("""
        INSERT INTO users (id, cognito_id, email, updated_at)
        VALUES (CAST(:id AS uuid), :cog, :email, NOW())
        ON CONFLICT (email) DO UPDATE SET updated_at = NOW()
        RETURNING id::text, cognito_id
    """), {"id": user_id, "cog": cognito_id, "email": email}).fetchone()
    return row[0], row[1]

def upsert_candidate(conn, user_id: str, full_name: str, s3_resume_path: str):
    cand_id = str(uuid.uuid4())
    row = conn.execute(text("""
        INSERT INTO candidates (id, user_id, full_name, s3_resume_path, updated_at)
        VALUES (CAST(:cid AS uuid), CAST(:uid AS uuid), :name, :path, NOW())
        ON CONFLICT (id) DO NOTHING
        RETURNING id::text
    """), {"cid": cand_id, "uid": user_id, "name": full_name, "path": s3_resume_path}).fetchone()
    # If INSERT did nothing (conflict), pick an existing candidate for this user/name
    if row is None:
        row = conn.execute(text("""
            SELECT id::text FROM candidates
            WHERE user_id = CAST(:uid AS uuid) AND full_name = :name
            ORDER BY created_at DESC LIMIT 1
        """), {"uid": user_id, "name": full_name}).fetchone()
    return row[0]

def upsert_job(conn, user_id: str, title: str, s3_jd_path: str):
    job_id = str(uuid.uuid4())
    row = conn.execute(text("""
        INSERT INTO jobs (id, user_id, title, s3_jd_path, updated_at)
        VALUES (CAST(:jid AS uuid), CAST(:uid AS uuid), :title, :path, NOW())
        ON CONFLICT (id) DO NOTHING
        RETURNING id::text
    """), {"jid": job_id, "uid": user_id, "title": title, "path": s3_jd_path}).fetchone()
    if row is None:
        row = conn.execute(text("""
            SELECT id::text FROM jobs
            WHERE user_id = CAST(:uid AS uuid) AND title = :title
            ORDER BY created_at DESC LIMIT 1
        """), {"uid": user_id, "title": title}).fetchone()
    return row[0]

def create_brief(conn, user_id: str, cand_id: str, job_id: str):
    brief_id = str(uuid.uuid4())
    row = conn.execute(text("""
        INSERT INTO briefs (id, user_id, candidate_id, job_id, status, created_at)
        VALUES (
            CAST(:bid AS uuid),
            CAST(:uid AS uuid),
            CAST(:cid AS uuid),
            CAST(:jid AS uuid),
            'PENDING',
            NOW()
        )
        RETURNING id::text
    """), {"bid": brief_id, "uid": user_id, "cid": cand_id, "jid": job_id}).fetchone()
    return row[0]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", required=True)
    ap.add_argument("--candidate", required=True, help="Candidate full name")
    ap.add_argument("--job-title", required=True)
    ap.add_argument("--resume", required=True, help="Path to resume PDF")
    ap.add_argument("--jd", required=True, help="Path to JD text")
    ap.add_argument("--stack", default="ProofbriefStack")
    ap.add_argument("--api-url", default="")
    ap.add_argument("--db-name", default="postgres")
    ap.add_argument("--region", default=os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1")
    args = ap.parse_args()

    outs = read_outputs(args.stack)
    bucket = outs["bucket"]
    api_url = args.api_url or outs["api_url"]
    region = args.region

    # Build S3 keys (namespaced)
    ts = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    cand_id_for_path = str(uuid.uuid4())
    resume_key = f"candidates/{cand_id_for_path}/resume_original.pdf"
    jd_key = f"jobs/{uuid.uuid4()}/jd.txt"

    # Upload files
    s3_upload(bucket, resume_key, args.resume, region)
    s3_upload(bucket, jd_key, args.jd, region)

    # DB connect (Data API)
    eng = engine_from_data_api(outs["cluster_arn"], outs["db_secret_arn"], args.db_name, region)

    with eng.begin() as conn:
        user_id, cognito_id = upsert_user(conn, args.email)
        cand_id = upsert_candidate(conn, user_id, args.candidate, resume_key)
        job_id  = upsert_job(conn, user_id, args.job_title, jd_key)
        brief_id = create_brief(conn, user_id, cand_id, job_id)

    print(f"Seeded briefId={brief_id}")

    # Kick the API /start
    payload = {"briefId": brief_id}
    start_url = api_url if api_url.rstrip("/").endswith("/start") else api_url.rstrip("/") + "/start"

    try:
        resp = http_post_json(start_url, payload, timeout=30)
        print("Start API response:", json.dumps(resp, indent=2))
    except urllib.error.HTTPError as e:
        print("Start API HTTPError:", e.read().decode(), file=sys.stderr)
        raise
    except Exception as e:
        print("Start API error:", e, file=sys.stderr)
        raise

if __name__ == "__main__":
    sys.exit(main())
