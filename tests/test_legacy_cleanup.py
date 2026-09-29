from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import legacy_cleanup
from scripts.legacy_cleanup import CleanupError, _candidate_v2_job_ids, _delete_candidates, is_legacy_v2_chain


def test_only_exact_old_research_worker_versions_are_cleanup_candidates() -> None:
    row = {
        "model_status": "research_only",
        "has_brief": False,
        "processor_version": "v2-research-only-worker-v1",
    }
    assert is_legacy_v2_chain([row])
    assert not is_legacy_v2_chain([])
    assert not is_legacy_v2_chain([{**row, "has_brief": True}])
    assert not is_legacy_v2_chain([{**row, "processor_version": "agent-forecast-processor-v1"}])
    assert not is_legacy_v2_chain([{**row, "model_status": "experimental_jev"}])
    assert not is_legacy_v2_chain([row, {**row, "processor_version": "agent-forecast-processor-v1"}])


def test_candidate_jobs_include_known_orphaned_failures_but_keep_other_jobs() -> None:
    versions = [{"id": "v1", "job_id": "j1"}]
    jobs = [
        {"id": "j1", "status": "succeeded", "error_type": None,
         "root_version_id": "v1", "parent_version_id": None, "result_version_id": "v1"},
        {"id": "j2", "status": "blocked_data", "error_type": "research_brief_failed",
         "root_version_id": None, "parent_version_id": None, "result_version_id": None},
        {"id": "j3", "status": "queued", "error_type": None,
         "root_version_id": None, "parent_version_id": None, "result_version_id": None},
    ]

    assert _candidate_v2_job_ids(versions, jobs) == ["j1", "j2"]


def test_candidate_jobs_refuse_retained_job_pointer_into_legacy_chain() -> None:
    versions = [{"id": "v1", "job_id": "j1"}]
    jobs = [
        {"id": "j1", "status": "succeeded", "error_type": None,
         "root_version_id": "v1", "parent_version_id": None, "result_version_id": "v1"},
        {"id": "j2", "status": "queued", "error_type": None,
         "root_version_id": "v1", "parent_version_id": None, "result_version_id": None},
    ]

    with pytest.raises(CleanupError, match="retained V2 job points into"):
        _candidate_v2_job_ids(versions, jobs)


class RecordingConnection:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, statement, params=None):
        self.statements.append(str(statement))


def test_delete_order_breaks_only_candidate_job_backrefs_before_version_delete() -> None:
    ids = {
        "forecast_snapshots": ["s1"],
        "forecast_revision_evidence": ["s2"],
        "evidence_revisions": [],
        "forecast_revisions": ["s1"],
        "forecasts": ["f1"],
        "forecast_evaluations_v2": ["e1"],
        "forecast_jobs_v2": ["j1"],
        "forecast_versions_v2": ["v1"],
        "research_runs": ["r1"],
        "event_extractions": ["c1"],
    }
    conn = RecordingConnection()

    _delete_candidates(conn, {"candidate_ids": ids})

    statements = [statement.lower() for statement in conn.statements]
    update_at = next(i for i, statement in enumerate(statements) if statement.startswith("update forecast_jobs_v2"))
    version_delete_at = next(i for i, statement in enumerate(statements) if statement.startswith('delete from "forecast_versions_v2"'))
    job_delete_at = next(i for i, statement in enumerate(statements) if statement.startswith('delete from "forecast_jobs_v2"'))
    assert update_at < version_delete_at < job_delete_at
    assert "root_version_id=null" in statements[update_at]
    assert "parent_version_id=null" in statements[update_at]
    assert "result_version_id=null" in statements[update_at]
    assert all("cascade" not in statement for statement in statements)


def test_apply_rejects_unverified_backup_before_opening_live_database(tmp_path, monkeypatch) -> None:
    fingerprint = "a" * 64
    monkeypatch.setattr(legacy_cleanup, "BACKUP_DIR", tmp_path)
    plan_path = tmp_path / f"legacy-cleanup-plan-market_evidence-{fingerprint[:16]}.json"
    verify_path = tmp_path / f"development-market_evidence-{fingerprint[:16]}.verification.json"
    plan_path.write_text(json.dumps({"manifest_fingerprint": fingerprint}), encoding="utf-8")
    verify_path.write_text(json.dumps({
        "manifest_fingerprint": fingerprint,
        "restore_verified": False,
        "isolated_cleanup_verified": False,
    }), encoding="utf-8")

    class NoDatabaseAccess:
        def begin(self):
            pytest.fail("live database must not be opened")

    monkeypatch.setattr(legacy_cleanup, "engine", NoDatabaseAccess())
    with pytest.raises(CleanupError, match="restore and isolated cleanup verification"):
        legacy_cleanup.apply_cleanup(fingerprint, "market_evidence")


def test_apply_rejects_wrong_explicit_database_name_before_disk_or_database_access(monkeypatch) -> None:
    class NoDatabaseAccess:
        def begin(self):
            pytest.fail("live database must not be opened")

    monkeypatch.setattr(legacy_cleanup, "engine", NoDatabaseAccess())
    with pytest.raises(CleanupError, match="development database name"):
        legacy_cleanup.apply_cleanup("b" * 64, "other_database")


def test_apply_rejects_manifest_fingerprint_mismatch_before_database_access(tmp_path, monkeypatch) -> None:
    fingerprint = "c" * 64
    monkeypatch.setattr(legacy_cleanup, "BACKUP_DIR", tmp_path)
    plan_path = tmp_path / f"legacy-cleanup-plan-market_evidence-{fingerprint[:16]}.json"
    verify_path = tmp_path / f"development-market_evidence-{fingerprint[:16]}.verification.json"
    plan_path.write_text(json.dumps({"manifest_fingerprint": "d" * 64}), encoding="utf-8")
    verify_path.write_text(json.dumps({"manifest_fingerprint": fingerprint}), encoding="utf-8")

    class NoDatabaseAccess:
        def begin(self):
            pytest.fail("live database must not be opened")

    monkeypatch.setattr(legacy_cleanup, "engine", NoDatabaseAccess())
    with pytest.raises(CleanupError, match="fingerprint"):
        legacy_cleanup.apply_cleanup(fingerprint, "market_evidence")
