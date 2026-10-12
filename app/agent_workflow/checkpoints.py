from __future__ import annotations

from contextlib import asynccontextmanager

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
import psycopg


def postgres_saver_url(database_url: str) -> str:
    if database_url.startswith("postgresql+psycopg://"):
        return database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    if database_url.startswith("postgresql://"):
        return database_url
    raise ValueError("agent checkpoint database must use PostgreSQL")


@asynccontextmanager
async def open_postgres_checkpointer(database_url: str):
    """Open the built-in durable saver. Schema changes are applied by explicit migration."""
    async with AsyncPostgresSaver.from_conn_string(postgres_saver_url(database_url)) as saver:
        yield saver


def checkpoint_ready(database_url: str) -> bool:
    try:
        with psycopg.connect(postgres_saver_url(database_url), connect_timeout=2) as connection:
            row = connection.execute("SELECT to_regclass('public.checkpoints'), to_regclass('public.checkpoint_blobs')").fetchone()
            return bool(row and all(row))
    except (psycopg.Error, ValueError):
        return False
