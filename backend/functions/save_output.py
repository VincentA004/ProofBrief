# backend/functions/save_output.py

import json
from datetime import datetime
from sqlalchemy import text

from shared.utils import get_db_engine, get_env, log, SESSION


def handler(event, context):
    """
    Expected input (from previous step):
    {
      "briefId": "<uuid>",
      "finalContent": { ... }   # JSON from resume_agent
      ...                       # any pass-through fields are ignored
    }
    """
    log.info("SaveOutput handler invoked.")
    brief_id = event.get("briefId")
    final = event.get("finalContent")

    if not brief_id:
        raise ValueError("Missing briefId in event")
    if final is None:
        raise ValueError("Missing finalContent in event")

    bucket = get_env("S3_BUCKET_NAME")
    key = f"briefs/{brief_id}/final.json"

    # Save JSON to S3
    s3 = SESSION.client("s3")
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(final, indent=2).encode("utf-8"),
        ContentType="application/json",
    )
    log.info(f"Saved final JSON to s3://{bucket}/{key}")

    # Update DB: mark brief DONE + set s3_output_path
    engine = get_db_engine()
    with engine.begin() as conn:
        conn.execute(
            text("""
                UPDATE briefs
                SET status = 'DONE',
                    s3_output_path = :path,
                    updated_at = NOW()
                WHERE id = CAST(:brief_id AS uuid)
            """),
            {"path": key, "brief_id": brief_id},
        )

    # Return enriched payload (for observability/chaining)
    out = {
        **event,
        "savedOutput": {
            "bucket": bucket,
            "key": key,
            "url": f"s3://{bucket}/{key}",
            "savedAt": datetime.utcnow().isoformat() + "Z",
        },
        "briefStatus": "DONE",
    }
    log.info(f"Completed SaveOutput for briefId={brief_id}")
    return out
