# backend/functions/api.py
import json
import os
import uuid
from datetime import datetime

from sqlalchemy import text

from shared.utils import (
    log,
    get_db_engine,
    get_env,
    SESSION,
)

# ---------- helpers

def _resp(status: int, body: dict | list, *, cors=True):
    headers = {"Content-Type": "application/json"}
    if cors:
        headers.update({
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type,Authorization",
            "Access-Control-Allow-Methods": "GET,POST,PUT,OPTIONS",
        })
    return {"statusCode": status, "headers": headers, "body": json.dumps(body)}

def _get_identity(event):
    """
    Pull Cognito identity if present. If not present (dev/local),
    fall back to a stable pseudo-user so you can test.
    """
    claims = (event.get("requestContext", {})
                  .get("authorizer", {})
                  .get("jwt", {})
                  .get("claims", {}))
    sub = claims.get("sub")
    email = claims.get("email") or claims.get("cognito:username")
    if not sub:
        # Dev fallback (no auth)
        sub = "dev-anon-sub"
        email = email or "anon@example.com"
    return sub, email

def _method(event):
    return (event.get("httpMethod")
            or event.get("requestContext", {}).get("http", {}).get("method")
            or "").upper()

def _path(event):
    # REST API provides resource (e.g., /briefs, /briefs/{id}, /briefs/{id}/start)
    return event.get("resource") or event.get("path") or event.get("rawPath") or ""

def _path_param(event, name):
    return (event.get("pathParameters") or {}).get(name)

def _body(event):
    b = event.get("body")
    if not b:
        return {}
    try:
        return json.loads(b)
    except Exception:
        return {}

def _presign_put(s3, bucket, key, content_type):
    return s3.generate_presigned_url(
        "put_object",
        Params={"Bucket": bucket, "Key": key, "ContentType": content_type},
        ExpiresIn=900
    )

def _presign_get(s3, bucket, key):
    return s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=900
    )

# ---------- route handlers

def post_briefs(event):
    """Create user/candidate/job/brief and return presigned upload URLs."""
    sub, email = _get_identity(event)
    payload = _body(event)

    full_name = (payload.get("candidate") or {}).get("fullName") or "Unnamed"
    job_title = (payload.get("job") or {}).get("title") or "Untitled"

    engine = get_db_engine()
    s3 = SESSION.client("s3")
    bucket = get_env("S3_BUCKET_NAME")

    user_id = str(uuid.uuid4())
    cand_id = str(uuid.uuid4())
    job_id = str(uuid.uuid4())
    brief_id = str(uuid.uuid4())

    resume_key = f"candidates/{cand_id}/resume_original.pdf"
    jd_key = f"jobs/{job_id}/jd.txt"

    log.info("[api] POST /briefs sub=%s email=%s", sub, email)

    with engine.begin() as conn:
        # upsert user by cognito_id
        conn.execute(
            text("""
                INSERT INTO users (id, cognito_id, email)
                VALUES (CAST(:id AS uuid), :cog, :email)
                ON CONFLICT (cognito_id) DO UPDATE SET email = EXCLUDED.email
            """),
            {"id": user_id, "cog": sub, "email": email},
        )

        # candidate
        conn.execute(
            text("""
                INSERT INTO candidates (id, user_id, full_name, s3_resume_path)
                VALUES (CAST(:id AS uuid), (SELECT id FROM users WHERE cognito_id = :cog),
                        :name, :resume_key)
            """),
            {"id": cand_id, "cog": sub, "name": full_name, "resume_key": resume_key},
        )

        # job
        conn.execute(
            text("""
                INSERT INTO jobs (id, user_id, title, s3_jd_path)
                VALUES (CAST(:id AS uuid), (SELECT id FROM users WHERE cognito_id = :cog),
                        :title, :jd_key)
            """),
            {"id": job_id, "cog": sub, "title": job_title, "jd_key": jd_key},
        )

        # brief
        conn.execute(
            text("""
                INSERT INTO briefs (id, user_id, candidate_id, job_id, status)
                VALUES (
                    CAST(:bid AS uuid),
                    (SELECT id FROM users WHERE cognito_id = :cog),
                    CAST(:cid AS uuid),
                    CAST(:jid AS uuid),
                    'PENDING'
                )
            """),
            {"bid": brief_id, "cog": sub, "cid": cand_id, "jid": job_id},
        )

    put_resume = _presign_put(s3, bucket, resume_key, "application/pdf")
    put_jd = _presign_put(s3, bucket, jd_key, "text/plain")

    return _resp(200, {
        "briefId": brief_id,
        "uploads": {
            "resume": {"key": resume_key, "putUrl": put_resume},
            "jd": {"key": jd_key, "putUrl": put_jd},
        }
    })

def put_brief_start(event):
    """Kick off Step Functions for a brief (validates ownership)."""
    sub, _ = _get_identity(event)
    brief_id = _path_param(event, "id")
    if not brief_id:
        return _resp(400, {"message": "Missing brief id"})

    engine = get_db_engine()
    with engine.connect() as conn:
        owned = conn.execute(
            text("""
                SELECT 1
                FROM briefs b
                JOIN users u ON b.user_id = u.id
                WHERE b.id = CAST(:bid AS uuid) AND u.cognito_id = :cog
            """),
            {"bid": brief_id, "cog": sub},
        ).scalar()

    if not owned:
        return _resp(404, {"message": "Not found"})

    sfn = SESSION.client("stepfunctions")
    sfn.start_execution(
        stateMachineArn=get_env("STATE_MACHINE_ARN"),
        input=json.dumps({"briefId": brief_id})
    )
    log.info("[api] PUT /briefs/%s/start -> StepFunctions started", brief_id)
    return _resp(202, {"message": "started"})

def get_briefs(event):
    """List briefs for current user."""
    sub, _ = _get_identity(event)
    engine = get_db_engine()
    rows = []
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT b.id, b.status, b.created_at,
                       c.id AS candidate_id, c.full_name,
                       j.id AS job_id, j.title
                FROM briefs b
                JOIN candidates c ON b.candidate_id = c.id
                JOIN jobs j ON b.job_id = j.id
                JOIN users u ON b.user_id = u.id
                WHERE u.cognito_id = :cog
                ORDER BY b.created_at DESC
            """),
            {"cog": sub},
        ).mappings().all()

    def _ts(x):
        if not x: return None
        if isinstance(x, datetime): return x.isoformat()
        return str(x)

    return _resp(200, [
        {
            "briefId": str(r["id"]),
            "status": r["status"],
            "candidate": {"id": str(r["candidate_id"]), "name": r["full_name"]},
            "job": {"id": str(r["job_id"]), "title": r["title"]},
            "createdAt": _ts(r["created_at"]),
        } for r in rows
    ])

def get_brief(event):
    """Get brief details + presigned URL to final.json when DONE."""
    sub, _ = _get_identity(event)
    brief_id = _path_param(event, "id")
    if not brief_id:
        return _resp(400, {"message": "Missing brief id"})

    engine = get_db_engine()
    s3 = SESSION.client("s3")
    bucket = get_env("S3_BUCKET_NAME")

    with engine.connect() as conn:
        r = conn.execute(
            text("""
                SELECT b.id, b.status, b.s3_output_path,
                       c.id AS candidate_id, c.full_name,
                       j.id AS job_id, j.title
                FROM briefs b
                JOIN candidates c ON b.candidate_id = c.id
                JOIN jobs j ON b.job_id = j.id
                JOIN users u ON b.user_id = u.id
                WHERE b.id = CAST(:bid AS uuid) AND u.cognito_id = :cog
            """),
            {"bid": brief_id, "cog": sub},
        ).mappings().first()

    if not r:
        return _resp(404, {"message": "Not found"})

    out = {
        "briefId": str(r["id"]),
        "status": r["status"],
        "candidate": {"id": str(r["candidate_id"]), "name": r["full_name"]},
        "job": {"id": str(r["job_id"]), "title": r["title"]},
    }
    if r["status"] == "DONE" and r["s3_output_path"]:
        out["final"] = {
            "key": r["s3_output_path"],
            "url": _presign_get(s3, bucket, r["s3_output_path"]),
        }

    return _resp(200, out)

# ---------- entrypoint (router)

def handler(event, context):
    try:
        m = _method(event)
        p = _path(event)
        log.info("[api] %s %s", m, p)

        # CORS preflight
        if m == "OPTIONS":
            return _resp(200, {"ok": True})

        # Routes:
        # POST /briefs
        if m == "POST" and p == "/briefs":
            return post_briefs(event)

        # GET /briefs
        if m == "GET" and p == "/briefs":
            return get_briefs(event)

        # GET /briefs/{id}
        if m == "GET" and p == "/briefs/{id}":
            return get_brief(event)

        # PUT /briefs/{id}/start
        if m == "PUT" and p == "/briefs/{id}/start":
            return put_brief_start(event)

        # Fallback (for REST API where p might be concrete path; try by pattern)
        if m == "GET" and p.startswith("/briefs/") and "/start" not in p:
            return get_brief(event)
        if m == "PUT" and p.endswith("/start") and p.startswith("/briefs/"):
            return put_brief_start(event)

        return _resp(404, {"message": f"No route for {m} {p}"})
    except Exception as e:
        log.exception("API error")
        return _resp(500, {"message": str(e)})
