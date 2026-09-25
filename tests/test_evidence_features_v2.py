from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.evidence_context import FrozenEvidenceEvent
from app.evidence_features import EvidenceFeatureError, build_evidence_features


DECISION_AT = datetime(2026, 9, 20, tzinfo=UTC)


def _fact(metric, value, comparison_value, *, unit="usd_millions", quote_validated=True):
    return {
        "metric": metric,
        "value": value,
        "comparison_value": comparison_value,
        "unit": unit,
        "comparison_unit": unit,
        "period_start": "2026-07-01",
        "period_end": "2026-09-30",
        "comparison_period_start": "2025-07-01",
        "comparison_period_end": "2025-09-30",
        "current_evidence_quote": "Current-period result was reported in the current filing.",
        "comparison_evidence_quote": "Prior-year result was reported for the comparable quarter.",
        "current_source_anchor": "source#current-result",
        "comparison_source_anchor": "source#prior-year-result",
        "current_quote_validated": quote_validated,
        "comparison_quote_validated": quote_validated,
    }


def _official(**overrides):
    event = {
        "id": "official-1",
        "event_key": "AAPL:earnings:2026-q3",
        "source_type": "official_filing",
        "source_id": "sec-1",
        "independent_source_key": "sec:0001",
        "published_at": datetime(2026, 9, 18, tzinfo=UTC),
        "is_new": True,
        "is_active": True,
        "structured_facts": {
            "facts": [_fact("revenue", 120.0, 100.0), _fact("eps", 2.4, 2.0)],
            "guidance": "raised",
            "operational_events": [{"event_type": "recall", "status": "active"}],
        },
    }
    event.update(overrides)
    return event


def _media(**overrides):
    event = {
        "id": "media-1",
        "event_key": "AAPL:product:rumor-1",
        "source_type": "uploaded_media",
        "source_id": "media-1",
        "independent_source_key": "publisher:example",
        "published_at": datetime(2026, 9, 19, tzinfo=UTC),
        "is_new": True,
        "is_active": True,
        "user_rating_stars": 4,
        "guidance": "unknown",
    }
    event.update(overrides)
    return event


def test_builds_fixed_validated_features_without_probability_adjustment():
    result = build_evidence_features([_official()], decision_at=DECISION_AT)

    assert result.schema_version == "evidence-features-v1"
    assert result.status_codes == ()
    assert result.joint_model_eligible is True
    assert result.features["revenue_yoy"] == pytest.approx(0.2)
    assert result.features["eps_yoy"] == pytest.approx(0.2)
    assert result.features["revenue_yoy_missing"] == 0.0
    assert result.features["guidance_direction"] == 1.0
    assert result.features["operational_recall_active"] == 1.0
    assert result.features["newest_event_age_days"] == 2.0
    assert result.features["official_event_count"] == 1.0
    assert result.features["media_stars_missing"] == 1.0
    assert result.used_event_ids == ("official-1",)


def test_incompatible_or_unquoted_numeric_facts_are_skipped_with_missing_flags():
    invalid_revenue = _fact("revenue", 120.0, 100.0, unit="usd_millions")
    invalid_revenue["comparison_unit"] = "usd"
    invalid_eps = _fact("eps", 2.4, 2.0, quote_validated=False)
    result = build_evidence_features(
        [_official(structured_facts={"facts": [invalid_revenue, invalid_eps]})], decision_at=DECISION_AT
    )

    assert result.status_codes == ("invalid_numeric_fact",)
    assert result.features["revenue_yoy_missing"] == 1.0
    assert result.features["eps_yoy_missing"] == 1.0
    assert result.features["revenue_yoy"] == 0.0
    assert result.features["eps_yoy"] == 0.0


def test_financial_yoy_rejects_a_single_quote_or_non_year_compatible_period():
    one_quote = _fact("revenue", 120.0, 100.0)
    one_quote["comparison_evidence_quote"] = ""
    too_recent = _fact("eps", 2.4, 2.0)
    too_recent["comparison_period_start"] = "2026-04-01"
    too_recent["comparison_period_end"] = "2026-06-30"
    result = build_evidence_features(
        [_official(structured_facts={"facts": [one_quote, too_recent]})], decision_at=DECISION_AT
    )

    assert result.status_codes == ("invalid_numeric_fact",)
    assert result.features["revenue_yoy_missing"] == 1.0
    assert result.features["eps_yoy_missing"] == 1.0


def test_media_channel_is_explicitly_unsupported_until_its_training_path_exists():
    result = build_evidence_features([_official(), _media()], decision_at=DECISION_AT)

    assert result.status_codes == ("unsupported_evidence_channel",)
    assert result.joint_model_eligible is False
    assert result.features["media_event_count"] == 1.0
    assert result.features["media_stars_mean"] == pytest.approx(0.8)
    assert result.features["media_stars_missing"] == 0.0
    assert result.features["independent_source_count"] == 2.0


def test_supporting_media_channel_makes_stars_a_named_feature_not_a_probability_multiplier():
    result = build_evidence_features(
        [_media()], decision_at=DECISION_AT, supported_source_types={"official_filing", "uploaded_media"}
    )

    assert result.status_codes == ()
    assert result.features["media_stars_mean"] == pytest.approx(0.8)
    assert set(result.features) == {
        "revenue_yoy", "revenue_yoy_missing", "eps_yoy", "eps_yoy_missing", "guidance_direction",
        "guidance_missing", "operational_recall_active", "operational_service_disruption_active",
        "operational_regulatory_active", "operational_unknown", "new_event_present", "newest_event_age_days",
        "newest_event_age_days_missing", "active_event_count", "corrected_or_withdrawn_present", "event_conflict",
        "official_event_count", "media_event_count", "independent_source_count", "media_stars_mean",
        "media_stars_missing",
    }


def test_accepts_frozen_object_events_and_is_deterministic_under_input_reordering():
    object_event = SimpleNamespace(
        **_official(),
        facts=(),
        guidance="unknown",
        operational_event_type="regulatory_action",
        operational_event_status="active",
    )
    first = build_evidence_features([_media(), object_event], decision_at=DECISION_AT)
    second = build_evidence_features([object_event, _media()], decision_at=DECISION_AT)

    assert first == second
    assert first.features["revenue_yoy"] == pytest.approx(0.2)
    assert first.features["guidance_direction"] == 1.0
    assert first.features["operational_regulatory_active"] == 1.0


def test_accepts_the_frozen_evidence_context_event_contract_without_import_cycle():
    frozen = FrozenEvidenceEvent(
        id=uuid4(),
        event_key="AAPL:earnings:2026-q3",
        source_type="official_filing",
        source_id=uuid4(),
        content_sha256="a" * 64,
        published_at=datetime(2026, 9, 18, tzinfo=UTC),
        observed_at=datetime(2026, 9, 18, 1, tzinfo=UTC),
        review_status="accepted",
        user_rating_stars=None,
        is_new=True,
        discovery_kind="new_publication",
        source_snapshot={"source_url": "https://www.sec.gov/example", "analysis_text": "fixed excerpt"},
        facts=(_fact("revenue", 120.0, 100.0),),
        guidance="lowered",
        operational_event_type="service_disruption",
        operational_event_status="active",
    )

    result = build_evidence_features([frozen], decision_at=DECISION_AT)

    assert result.status_codes == ()
    assert result.features["revenue_yoy"] == pytest.approx(0.2)
    assert result.features["guidance_direction"] == -1.0
    assert result.features["operational_service_disruption_active"] == 1.0


def test_unknown_channel_and_bad_star_rating_fail_closed():
    bad_channel = _official(source_type="social_feed")
    result = build_evidence_features([bad_channel], decision_at=DECISION_AT)
    assert result.status_codes == ("unsupported_evidence_channel",)
    assert result.used_event_ids == ()
    with pytest.raises(EvidenceFeatureError, match="integer from 1 to 5"):
        build_evidence_features([_media(user_rating_stars=6)], decision_at=DECISION_AT)
