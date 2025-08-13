"""
Alembic environment script for ProofBrief.

This script is configured to connect to a secure RDS database using the
Data API, which allows running migrations without direct network access.
It reads the database cluster and secret ARNs from a .env file.
"""

import os
import sys
from logging.config import fileConfig

from dotenv import load_dotenv
from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

# --- Configuration ---

# Load environment variables from a .env file at the project root
load_dotenv()

# Add the parent directory (e.g., /backend) to the Python path
# This allows Alembic to find the 'models' module.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from models import Base  # noqa: E402

# This is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Set the target metadata for 'autogenerate' support
target_metadata = Base.metadata


def get_db_url() -> str:
    """
    Constructs the database connection URL for the RDS Data API
    using environment variables.
    """
    cluster_arn = os.getenv("DB_CLUSTER_ARN")
    secret_arn = os.getenv("DB_SECRET_ARN")

    if not all([cluster_arn, secret_arn]):
        raise ValueError(
            "DB_CLUSTER_ARN and DB_SECRET_ARN must be set in your .env file."
        )

    # Use a default database name if not specified
    dbname = os.getenv("DB_NAME", "postgres")

    return (
        f"postgresql+auroradataapi://:@/{dbname}"
        f"?aurora_cluster_arn={cluster_arn}"
        f"&secret_arn={secret_arn}"
    )


def run_migrations_offline() -> None:
    """

    Run migrations in 'offline' mode. This is not supported for the Data API.
    """
    raise NotImplementedError(
        "Offline migrations are not supported with the RDS Data API."
    )


def run_migrations_online() -> None:
    """
    Run migrations in 'online' mode.
    """
    # Override the sqlalchemy.url from the .ini file with our dynamic URL
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = get_db_url()

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,  # Enables detection of column type changes
            compare_server_default=True, # Enables detection of server default changes
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()