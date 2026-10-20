"""Which saved materials may revise a forecast: one rule for the workspace, the agent and the API.

A revision uses material published within seven days after the forecast it revises, or material
published within the thirty days before it that only reached the library after it.  Material the
forecast already used is offered again only when its state changed since.

Saved sources outlive each test here, so every test keeps to a symbol of its own.
"""
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.forecast_v2_api as api
from app.database import SessionLocal
from app.evidence_context import freeze_evidence_context
from app.forecast_revision_candidates import REVISION_LOOKAHEAD, REVISION_LOOKBACK, revision_candidates
from app.forecast_v2_models import EvidenceEventVersionV2, ForecastJobV2, ForecastVersionV2
from app.models import SecFilingInventory, UploadedEvidence

from test_forecast_api_v2 import _create_version, _media_source, _official_source, _test_db, v2_tables  # noqa: F401


def forget_symbols(symbols):
    """Remove what a test saved for its own symbols: later modules sync or evaluate every saved row."""
    with SessionLocal() as db:
        db.query(ForecastJobV2).filter(ForecastJobV2.symbol.in_(symbols)).update(
            {ForecastJobV2.root_version_id: None, ForecastJobV2.parent_version_id: None,
             ForecastJobV2.result_version_id: None}, synchronize_session=False)
        db.query(ForecastVersionV2).filter(ForecastVersionV2.symbol.in_(symbols)).update(
            {ForecastVersionV2.parent_version_id: None, ForecastVersionV2.root_id: ForecastVersionV2.id},
            synchronize_session=False)
        for model in (ForecastVersionV2, ForecastJobV2, UploadedEvidence, SecFilingInventory, EvidenceEventVersionV2):
            db.query(model).filter(model.symbol.in_(symbols)).delete(synchronize_session=False)
        db.commit()


@pytest.fixture(autouse=True)
def _own_symbols_only():
    yield
    forget_symbols([f'RV{letter}' for letter in 'ABCDEFGH'])


@pytest.fixture()
def client():
    test_app = FastAPI()
    test_app.include_router(api.router)
    test_app.dependency_overrides[api.get_v2_db] = _test_db
    with TestClient(test_app) as test_client:
        yield test_client


def _candidates(version_id):
    with SessionLocal() as db:
        return revision_candidates(db, db.get(ForecastVersionV2, version_id))


def _ids(rows):
    return {row['source_id'] for row in rows}


def _freeze_into(version_id, symbol, decided, ref):
    with SessionLocal() as db:
        parent = db.get(ForecastVersionV2, version_id)
        parent.evidence_version_manifest = freeze_evidence_context(
            db=db, symbol=symbol, decision_at=decided, mode='observed', source_refs=[ref],
            include_automatic_new_sources=False,
        ).evidence_manifest()
        db.commit()


def test_the_window_is_thirty_days_back_and_seven_days_forward():
    assert REVISION_LOOKBACK == timedelta(days=30)
    assert REVISION_LOOKAHEAD == timedelta(days=7)


def test_material_published_within_seven_days_after_the_forecast_is_new():
    decided = datetime.now(UTC) - timedelta(days=12)
    version = _create_version(symbol='RVA', decision_at=decided)
    sixth_day = _media_source(symbol='RVA', published_at=decided + timedelta(days=6),
                              observed_at=decided + timedelta(days=6, hours=1))
    _media_source(symbol='RVA', published_at=decided + timedelta(days=8), observed_at=decided + timedelta(days=8, hours=1))

    rows = _candidates(version)

    assert _ids(rows) == {str(sixth_day)}
    assert rows[0]['category'] == 'new'
    assert rows[0]['source_type'] == 'uploaded_media'
    assert rows[0]['title'] == 'Reported product issue'


def test_older_material_counts_only_when_it_reached_the_library_after_the_forecast():
    decided = datetime.now(UTC) - timedelta(days=2)
    version = _create_version(symbol='RVB', decision_at=decided)
    added_later = _official_source(symbol='RVB', accepted_at=decided - timedelta(days=20),
                                   observed_at=decided + timedelta(hours=1))
    _official_source(symbol='RVB', accepted_at=decided - timedelta(days=31), observed_at=decided + timedelta(hours=1))
    _official_source(symbol='RVB', accepted_at=decided - timedelta(days=5), observed_at=decided - timedelta(hours=1))

    rows = _candidates(version)

    assert _ids(rows) == {str(added_later)}
    assert rows[0]['category'] == 'backfilled'


def test_material_the_forecast_used_is_offered_again_only_after_its_state_changed():
    decided = datetime.now(UTC) - timedelta(hours=1)
    version = _create_version(symbol='RVC', decision_at=decided)
    used = _official_source(symbol='RVC', accepted_at=decided - timedelta(days=1), observed_at=decided - timedelta(minutes=10))
    with SessionLocal() as db:
        source = db.get(SecFilingInventory, used)
        source.review_status = 'pending_review'
        source.reviewed_at = None
        db.commit()
    _freeze_into(version, 'RVC', decided, {'source_type': 'official_filing', 'source_id': str(used)})

    assert _candidates(version) == []

    with SessionLocal() as db:
        source = db.get(SecFilingInventory, used)
        source.review_status = 'accepted'
        source.reviewed_at = datetime.now(UTC) - timedelta(minutes=5)
        db.commit()

    rows = _candidates(version)
    assert _ids(rows) == {str(used)}
    assert rows[0]['category'] == 'updated'


def test_a_copy_of_used_content_under_another_record_is_not_new_material():
    decided = datetime.now(UTC) - timedelta(hours=2)
    version = _create_version(symbol='RVD', decision_at=decided)
    first = _official_source(symbol='RVD', accepted_at=decided - timedelta(days=1), observed_at=decided - timedelta(minutes=10))
    _freeze_into(version, 'RVD', decided, {'source_type': 'official_filing', 'source_id': str(first)})
    # The fixture's filings all carry the same excerpt, so this one repeats the used content.
    _official_source(symbol='RVD', accepted_at=decided + timedelta(hours=1), observed_at=decided + timedelta(hours=1))

    assert _candidates(version) == []


def test_other_symbols_stay_out_and_the_newest_comes_first():
    decided = datetime.now(UTC) - timedelta(days=3)
    version = _create_version(symbol='RVE', decision_at=decided)
    _media_source(symbol='RVF', published_at=decided + timedelta(days=1), observed_at=decided + timedelta(days=1))
    older = _media_source(symbol='RVE', published_at=decided + timedelta(days=1), observed_at=decided + timedelta(days=1))
    newer = _media_source(symbol='RVE', published_at=decided + timedelta(days=2), observed_at=decided + timedelta(days=2))

    rows = _candidates(version)

    assert [row['source_id'] for row in rows] == [str(newer), str(older)]
    assert all(row['published_at'].endswith('+00:00') for row in rows)


def test_the_api_refuses_material_published_too_long_after_the_forecast(client):
    decided = datetime.now(UTC) - timedelta(days=12)
    version = _create_version(symbol='RVG', decision_at=decided)
    late = _official_source(symbol='RVG', accepted_at=decided + timedelta(days=8), observed_at=decided + timedelta(days=8))
    in_time = _official_source(symbol='RVG', accepted_at=decided + timedelta(days=6), observed_at=decided + timedelta(days=6))

    refused = client.post(
        f'/v2/forecast-versions/{version}/revision-jobs',
        json={'source_refs': [{'source_type': 'official_filing', 'source_id': str(late)}]},
        headers={'Idempotency-Key': 'outside-window'},
    )
    accepted = client.post(
        f'/v2/forecast-versions/{version}/revision-jobs',
        json={'source_refs': [{'source_type': 'official_filing', 'source_id': str(in_time)}]},
        headers={'Idempotency-Key': 'inside-window'},
    )

    assert refused.status_code == 422
    assert refused.json()['detail'] == 'manual revision source is outside the revision window of the selected forecast'
    assert accepted.status_code == 202


def test_the_api_refuses_older_material_beyond_the_thirty_days(client):
    decided = datetime.now(UTC) - timedelta(days=2)
    version = _create_version(symbol='RVH', decision_at=decided)
    too_old = _official_source(symbol='RVH', accepted_at=decided - timedelta(days=31), observed_at=decided + timedelta(hours=1))
    recent = _official_source(symbol='RVH', accepted_at=decided - timedelta(days=20), observed_at=decided + timedelta(hours=1))

    refused = client.post(
        f'/v2/forecast-versions/{version}/revision-jobs',
        json={'source_refs': [{'source_type': 'official_filing', 'source_id': str(too_old)}]},
        headers={'Idempotency-Key': 'too-old'},
    )
    accepted = client.post(
        f'/v2/forecast-versions/{version}/revision-jobs',
        json={'source_refs': [{'source_type': 'official_filing', 'source_id': str(recent)}]},
        headers={'Idempotency-Key': 'thirty-days'},
    )

    assert refused.status_code == 422
    assert refused.json()['detail'] == 'manual revision source is outside the revision window of the selected forecast'
    assert accepted.status_code == 202


def test_materials_outside_the_window_say_why():
    from app.forecast_revision_candidates import assess_revision_sources
    decided = datetime.now(UTC) - timedelta(days=10)
    version = _create_version(symbol='RVA', decision_at=decided)
    too_old = _official_source(symbol='RVA', accepted_at=decided - timedelta(days=40), observed_at=decided + timedelta(hours=1))
    late = _media_source(symbol='RVA', published_at=decided + timedelta(days=9), observed_at=decided + timedelta(days=9))
    known = _media_source(symbol='RVA', published_at=decided - timedelta(days=5), observed_at=decided - timedelta(hours=1))
    with SessionLocal() as db:
        rows = {row['source_id']: row for row in assess_revision_sources(db, db.get(ForecastVersionV2, version))}

    assert rows[str(late)]['reason'] == 'published_after_window' and rows[str(late)]['category'] is None
    assert rows[str(too_old)]['reason'] == 'published_before_window'
    assert rows[str(known)]['reason'] == 'stored_before_forecast'
    assert '7 天' in rows[str(late)]['reason_text'] and '30 天' in rows[str(too_old)]['reason_text']
