import os
import re
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url


_DEFAULT_TEST_DATABASE_URL = (
    "postgresql+psycopg://market_evidence:market_evidence_dev@localhost:55432/test_market_evidence"
)
_TEST_DATABASE_NAME = re.compile(r"test_[a-z0-9_]+")


def _make_disposable_database_url() -> URL:
    """Return a unique PostgreSQL database URL reserved for this pytest run.

    ``DATABASE_URL`` is deliberately ignored so tests cannot mutate the
    developer database.  Set ``TEST_DATABASE_URL`` to a PostgreSQL URL whose
    database name begins with ``test_`` when the local defaults are unsuitable.
    """
    configured_url = make_url(os.getenv("TEST_DATABASE_URL", _DEFAULT_TEST_DATABASE_URL))
    if configured_url.get_backend_name() != "postgresql":
        raise RuntimeError("TEST_DATABASE_URL must use PostgreSQL")
    if not configured_url.database or not _TEST_DATABASE_NAME.fullmatch(configured_url.database):
        raise RuntimeError(
            "TEST_DATABASE_URL must name a database beginning with 'test_' to protect development data"
        )

    disposable_name = f"{configured_url.database}_{uuid4().hex}"
    if len(disposable_name) > 63:
        raise RuntimeError("TEST_DATABASE_URL database name leaves no room for a disposable test suffix")

    return configured_url.set(database=disposable_name)


_DISPOSABLE_DATABASE_URL = _make_disposable_database_url()
os.environ["DATABASE_URL"] = _DISPOSABLE_DATABASE_URL.render_as_string(hide_password=False)

# These imports must remain after DATABASE_URL is set so every application
# module and test shares the disposable database engine.
from app.database import engine
from app.main import app


@pytest.fixture(scope="session", autouse=True)
def disposable_database():
    admin_url = _DISPOSABLE_DATABASE_URL.set(database="postgres")
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    created = False
    try:
        with admin_engine.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{_DISPOSABLE_DATABASE_URL.database}"'))
        created = True
        yield
    finally:
        engine.dispose()
        try:
            if created:
                with admin_engine.connect() as connection:
                    connection.execute(
                        text(f'DROP DATABASE IF EXISTS "{_DISPOSABLE_DATABASE_URL.database}" WITH (FORCE)')
                    )
        finally:
            admin_engine.dispose()


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as test_client:
        yield test_client
