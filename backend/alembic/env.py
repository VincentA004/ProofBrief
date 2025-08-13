# backend/alembic/env.py

import os
import json
import boto3
from dotenv import load_dotenv
from logging.config import fileConfig
from sqlalchemy import engine_from_config
from sqlalchemy import pool
from alembic import context
from models import Base

# Load environment variables from .env file (for the DB_SECRET_ARN)
load_dotenv()

# --- Function to get credentials from Secrets Manager ---
def get_db_url_from_secrets_manager():
    secret_arn = os.getenv("DB_SECRET_ARN")
    if not secret_arn:
        raise ValueError("DB_SECRET_ARN environment variable not set.")

    session = boto3.session.Session()
    client = session.client(service_name='secretsmanager')
    
    get_secret_value_response = client.get_secret_value(SecretId=secret_arn)
    secret = json.loads(get_secret_value_response['SecretString'])
    
    return (
        f"postgresql://{secret['username']}:{secret['password']}"
        f"@{secret['host']}:{secret['port']}/postgres"
    )

# --- Standard Alembic Config ---
config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

def run_migrations_offline() -> None:
    url = get_db_url_from_secrets_manager()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()

def run_migrations_online() -> None:
    db_url = get_db_url_from_secrets_manager()
    
    configuration = config.get_section(config.config_ini_section)
    configuration["sqlalchemy.url"] = db_url

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )
        with context.begin_transaction():
            context.run_migrations()

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()