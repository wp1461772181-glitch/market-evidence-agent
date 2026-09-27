import importlib.util
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, inspect, text

from app.database import engine


_MIGRATION_PATH = Path(__file__).parents[1] / "scripts" / "migrate_agent_forecast.py"
_MIGRATION_SPEC = importlib.util.spec_from_file_location("migrate_agent_forecast", _MIGRATION_PATH)
assert _MIGRATION_SPEC is not None and _MIGRATION_SPEC.loader is not None
_MIGRATION = importlib.util.module_from_spec(_MIGRATION_SPEC)
_MIGRATION_SPEC.loader.exec_module(_MIGRATION)


def test_agent_forecast_migration_is_additive_idempotent_and_enables_experimental_jev():
    database_name = f"test_agent_forecast_migration_{uuid4().hex[:12]}"
    admin_engine = create_engine(engine.url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    isolated_engine = create_engine(engine.url.set(database=database_name))
    created = False
    try:
        with admin_engine.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{database_name}"'))
        created = True
        with isolated_engine.begin() as connection:
            connection.execute(text(
                "CREATE TABLE forecast_versions_v2 ("
                "id UUID PRIMARY KEY, model_status VARCHAR(32) NOT NULL, "
                "CONSTRAINT ck_forecast_versions_v2_model_status CHECK "
                "(model_status IN ('research_only', 'experimental_joint', 'baseline_only', 'blocked_data')))"
            ))
            connection.execute(text(
                "INSERT INTO forecast_versions_v2 (id, model_status) "
                "VALUES ('5c0c95f9-b8c0-49ed-96b0-1fdff3d473e5', 'research_only')"
            ))

        before = _MIGRATION.schema_state(isolated_engine)
        assert before["table_present"] is True
        assert before["missing_columns"] == ["decision_probabilities", "research_brief"]
        assert before["jev_status_enabled"] is False
        assert _MIGRATION.apply_schema(isolated_engine) == {
            "table_present": True, "missing_columns": [], "jev_status_enabled": True,
        }
        assert _MIGRATION.apply_schema(isolated_engine) == {
            "table_present": True, "missing_columns": [], "jev_status_enabled": True,
        }

        columns = {column["name"] for column in inspect(isolated_engine).get_columns("forecast_versions_v2")}
        assert {"decision_probabilities", "research_brief"} <= columns
        with isolated_engine.begin() as connection:
            old = connection.execute(text(
                "SELECT model_status, decision_probabilities, research_brief "
                "FROM forecast_versions_v2 WHERE id = '5c0c95f9-b8c0-49ed-96b0-1fdff3d473e5'"
            )).mappings().one()
            assert old == {"model_status": "research_only", "decision_probabilities": None, "research_brief": None}
            connection.execute(text(
                "INSERT INTO forecast_versions_v2 (id, model_status) "
                "VALUES ('b4d9d9a7-2734-4c3e-a734-2a38921e4372', 'experimental_jev')"
            ))
    finally:
        isolated_engine.dispose()
        if created:
            with admin_engine.connect() as connection:
                connection.execute(text(f'DROP DATABASE IF EXISTS "{database_name}" WITH (FORCE)'))
        admin_engine.dispose()
