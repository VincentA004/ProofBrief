# backend/shared/utils.py

import os
import json
import logging
from typing import Optional

import boto3
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from dotenv import load_dotenv

# --- Configuration ---
load_dotenv()
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("proofbrief-pipeline")

AWS_REGION = os.getenv("AWS_REGION", os.getenv("AWS_DEFAULT_REGION", "us-east-1"))
SESSION = boto3.session.Session(region_name=AWS_REGION)
_DB_ENGINE: Optional[Engine] = None
_GITHUB_TOKEN_CACHE: Optional[str] = None

# --- Core Utilities ---

def get_env(name: str, required: bool = True) -> str:
    """Gets an environment variable, raising an error if required and not found."""
    v = os.getenv(name)
    if required and not v:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return v

def get_db_engine() -> Engine:
    """Creates a reusable SQLAlchemy engine for the Aurora Data API."""
    global _DB_ENGINE
    if _DB_ENGINE:
        return _DB_ENGINE

    cluster_arn = get_env("DB_CLUSTER_ARN")
    secret_arn = get_env("DB_SECRET_ARN")
    db_name = os.getenv("DB_NAME", "postgres")

    url = (
        f"postgresql+auroradataapi://:@/{db_name}"
        f"?aurora_cluster_arn={cluster_arn}"
        f"&secret_arn={secret_arn}"
    )
    _DB_ENGINE = create_engine(url)
    return _DB_ENGINE

def get_secret(secret_arn_env: str, secret_key: str) -> str:
    """Fetches a specific key from a secret in AWS Secrets Manager."""
    # Simple caching for the GitHub token case
    if secret_arn_env == "GITHUB_SECRET_ARN":
        global _GITHUB_TOKEN_CACHE
        if _GITHUB_TOKEN_CACHE:
            return _GITHUB_TOKEN_CACHE

    secret_arn = get_env(secret_arn_env)
    sm_client = SESSION.client("secretsmanager")
    response = sm_client.get_secret_value(SecretId=secret_arn)
    token = json.loads(response["SecretString"])[secret_key]

    if secret_arn_env == "GITHUB_SECRET_ARN":
        _GITHUB_TOKEN_CACHE = token
    return token

def s3_get_text(bucket: str, key: str) -> str:
    """Gets the text content of an object from S3."""
    s3_client = SESSION.client("s3")
    obj = s3_client.get_object(Bucket=bucket, Key=key)
    return obj["Body"].read().decode("utf-8")