# backend/functions/api.py
import json
import os
import uuid
from datetime import datetime, timedelta  # <-- added timedelta

from sqlalchemy import text

from shared.utils import (
    log,
    get_db_engine,
    get_env,
    SESSION,
)

ALLOW_DEV_NO_AUTH = os.getenv("ALLOW_DEV_NO_AUTH", "false").lower() == "true"

# ---------- helpers

def _resp(status: int, body: dict | list, *, cors=True):
    headers = {"Content-Type": "application/json"}
    if cors:
        headers.update({
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type,Authorization",
            "Access-Control-Allow-Methods": "GET,POST,PUT,DELETE,OPTIONS",  # include DELETE
        })
    return {"statusCode": status, "headers": headers, "body": json.dumps(body)}


def _get_identity(event):
    """
    Resolve the authenticated user's identity.

    - For **REST API + Cognito User Pools authorizer**, claims are in:
        event.requestContext.authorizer.claims.sub
    - For **HTTP API (JWT)**, claims may be in:
        event.requestContext.authorizer.jwt.claims.sub

    We support both. If nothing is present:
      * If ALLOW_DEV_NO_AUTH=true -> fallback to 'dev-anon-sub'
      * Else -> raise 401 so briefs are never shared across users.
    """
    rc = event.get("requestContext", {}) or {}
    auth = rc.get("authorizer", {}) or {}

    # REST API (Cognito User Pools) puts them right under 'claims'
    claims = auth.get("claims") or {}
    sub = claims.get("sub")
    email = claims.get("email") or claims.get("cognito:username")

    # HTTP API path (jwt -> claims)
    if not sub:
        jwt = auth.get("jwt") or {}
        jclaims = jwt.get("claims") or {}
        sub = sub or jclaims.get("sub")
        email = email or jclaims.get("email") or jclaims.get("cognito:username")

    if not sub:
        if ALLOW_DEV_NO_AUTH:
            log.warning("[api] No auth claims; using dev fallback identity")
            return "dev-anon-sub", (email or "anon@example.com")
        # Strict mode: deny
        raise PermissionError("Unauthorized")

    return sub, (email or "unknown@example.com")


def _method(event):
    return (event.get("httpMethod")
            or event.get("requestContext", {}).get("http", {}).get("method")
            or "").upper()


def _path(event):
    # REST API provides 'resource' with templated path
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
        ExpiresIn=900,
    )


def _presign_get(s3, bucket, key):
    return s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=900,
    )


def _expire_stale_pending(engine, *, minutes: int = 5, brief_id: str | None = None) -> int:
    """
    Mark briefs stuck in PENDING beyond `minutes` as FAILED.
    Called on list/get/delete to keep UI accurate without a separate scheduler.
    Returns number of rows updated.
    """
    cutoff = datetime.utcnow() - timedelta(minutes=minutes)
    sql = """
        UPDATE briefs
        SET status = 'FAILED'
        WHERE status = 'PENDING'
          AND created_at < :cutoff
    """
    params = {"cutoff": cutoff}
    if brief_id:
        sql += " AND id = CAST(:bid AS uuid)"
        params["bid"] = brief_id

    with engine.begin() as conn:
        res = conn.execute(text(sql), params)
        count = getattr(res, "rowcount", 0) or 0
    if count:
        log.info("[api] Auto-expired %d stale PENDING brief(s) to FAILED (cutoff=%s, brief_id=%s)",
                 count, cutoff.isoformat(), brief_id or "*")
    return count

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

    # New IDs for this brief
    user_id = str(uuid.uuid4())
    cand_id = str(uuid.uuid4())
    job_id = str(uuid.uuid4())
    brief_id = str(uuid.uuid4())

    resume_key = f"candidates/{cand_id}/resume_original.pdf"
    jd_key = f"jobs/{job_id}/jd.txt"

    log.info("[api] POST /briefs sub=%s email=%s", sub, email)

    with engine.begin() as conn:
        # Upsert user by cognito_id (ensure UNIQUE on users.cognito_id)
        conn.execute(
            text("""
                INSERT INTO users (id, cognito_id, email)
                VALUES (CAST(:id AS uuid), :cog, :email)
                ON CONFLICT (cognito_id)
                DO UPDATE SET email = EXCLUDED.email
            """),
            {"id": user_id, "cog": sub, "email": email},
        )

        # Candidate
        conn.execute(
            text("""
                INSERT INTO candidates (id, user_id, full_name, s3_resume_path)
                VALUES (
                    CAST(:id AS uuid),
                    (SELECT id FROM users WHERE cognito_id = :cog),
                    :name, :resume_key
                )
            """),
            {"id": cand_id, "cog": sub, "name": full_name, "resume_key": resume_key},
        )

        # Job
        conn.execute(
            text("""
                INSERT INTO jobs (id, user_id, title, s3_jd_path)
                VALUES (
                    CAST(:id AS uuid),
                    (SELECT id FROM users WHERE cognito_id = :cog),
                    :title, :jd_key
                )
            """),
            {"id": job_id, "cog": sub, "title": job_title, "jd_key": jd_key},
        )

        # Brief
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
    """List briefs for current user (strictly scoped by cognito_id)."""
    sub, _ = _get_identity(event)
    engine = get_db_engine()

    # Auto-expire stale PENDING before listing
    _expire_stale_pending(engine, minutes=5)

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
    """Get a single brief (ownership enforced) and a presigned URL to final output if done."""
    sub, _ = _get_identity(event)
    brief_id = _path_param(event, "id")
    if not brief_id:
        return _resp(400, {"message": "Missing brief id"})

    engine = get_db_engine()
    s3 = SESSION.client("s3")
    bucket = get_env("S3_BUCKET_NAME")

    # Auto-expire this brief if it's stale PENDING
    _expire_stale_pending(engine, minutes=5, brief_id=brief_id)

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


def delete_brief(event):
    """Delete a brief and its related candidate/job rows and S3 artifacts (ownership enforced)."""
    sub, _ = _get_identity(event)
    brief_id = _path_param(event, "id")
    if not brief_id:
        return _resp(400, {"message": "Missing brief id"})

    engine = get_db_engine()
    s3 = SESSION.client("s3")
    bucket = get_env("S3_BUCKET_NAME")

    # Auto-expire this brief if it's stale PENDING to allow deletion
    _expire_stale_pending(engine, minutes=5, brief_id=brief_id)

    with engine.begin() as conn:
        row = conn.execute(
            text("""
                SELECT b.id, b.status, b.s3_output_path,
                       c.id AS candidate_id, c.s3_resume_path,
                       j.id AS job_id, j.s3_jd_path
                FROM briefs b
                JOIN candidates c ON b.candidate_id = c.id
                JOIN jobs j ON b.job_id = j.id
                JOIN users u ON b.user_id = u.id
                WHERE b.id = CAST(:bid AS uuid) AND u.cognito_id = :cog
            """),
            {"bid": brief_id, "cog": sub},
        ).mappings().first()

        if not row:
            return _resp(404, {"message": "Not found"})

        status = (row["status"] or "").upper()
        if status not in ("DONE", "FAILED", "CANCELLED"):
            return _resp(409, {"message": f"Cannot delete while status is '{status}'. Try again when processing completes."})

        # Best-effort S3 cleanup
        keys = [row.get("s3_resume_path"), row.get("s3_jd_path"), row.get("s3_output_path")]
        for key in keys:
            if key:
                try:
                    s3.delete_object(Bucket=bucket, Key=key)
                except Exception as e:
                    log.warning("[api] Delete brief: S3 delete failed for %s: %s", key, e)

        # Delete DB rows
        conn.execute(text("DELETE FROM briefs WHERE id = CAST(:bid AS uuid)"), {"bid": brief_id})
        conn.execute(text("DELETE FROM candidates WHERE id = CAST(:cid AS uuid)"), {"cid": row["candidate_id"]})
        conn.execute(text("DELETE FROM jobs WHERE id = CAST(:jid AS uuid)"), {"jid": row["job_id"]})

    log.info("[api] DELETE /briefs/%s -> deleted", brief_id)
    return _resp(200, {"message": "deleted"})


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
        if m == "POST" and p == "/briefs":
            return post_briefs(event)
        if m == "GET" and p == "/briefs":
            return get_briefs(event)
        if m == "GET" and p == "/briefs/{id}":
            return get_brief(event)
        if m == "PUT" and p == "/briefs/{id}/start":
            return put_brief_start(event)
        if m == "DELETE" and p == "/briefs/{id}":
            return delete_brief(event)

        # Fallback for concrete paths rendered by REST API
        if m == "GET" and p.startswith("/briefs/") and "/start" not in p:
            return get_brief(event)
        if m == "PUT" and p.endswith("/start") and p.startswith("/briefs/"):
            return put_brief_start(event)
        if m == "DELETE" and p.startswith("/briefs/"):
            return delete_brief(event)

        return _resp(404, {"message": f"No route for {m} {p}"})
    except PermissionError:
        return _resp(401, {"message": "Unauthorized"})
    except Exception as e:
        log.exception("API error")
        return _resp(500, {"message": str(e)})
