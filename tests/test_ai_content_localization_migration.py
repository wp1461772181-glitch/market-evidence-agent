from __future__ import annotations

from app.database import Base, engine
from app.localization_models import LOCALIZATION_TABLES
from scripts.migrate_ai_content_translations import apply_schema, schema_state


def test_localization_migration_is_additive_and_idempotent(disposable_database):
    Base.metadata.create_all(bind=engine)

    before = schema_state(engine)
    assert before["missing_prerequisites"] == []

    first = apply_schema(engine)
    second = apply_schema(engine)

    assert first["missing"] == []
    assert second["missing"] == []
    assert {table.name for table in LOCALIZATION_TABLES} <= set(second["present"])
    assert second["missing_prerequisites"] == []
