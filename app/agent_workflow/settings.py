from __future__ import annotations

import os

from ..config import DATABASE_URL


def checkpoint_database_url() -> str:
    value = os.getenv("AGENT_CHECKPOINT_DATABASE_URL", DATABASE_URL)
    if not value.startswith(("postgresql://", "postgresql+psycopg://")):
        raise RuntimeError("agent checkpoint requires configured PostgreSQL")
    return value
