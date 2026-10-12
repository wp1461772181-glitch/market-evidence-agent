"""Additive durable idempotency ledger for authorized Agent actions."""
from sqlalchemy import text
from app.database import engine


def apply() -> None:
    with engine.begin() as connection:
        connection.execute(text("""CREATE TABLE IF NOT EXISTS agent_action_requests (
            id UUID PRIMARY KEY, owner_hash VARCHAR(64) NOT NULL,
            request_key VARCHAR(128) NOT NULL, action VARCHAR(40) NOT NULL,
            payload_digest VARCHAR(64) NOT NULL, status VARCHAR(32) NOT NULL,
            job_kind VARCHAR(32), job_id UUID, result JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_agent_action_owner_key UNIQUE(owner_hash, request_key)
        )"""))


if __name__ == "__main__":
    apply()
