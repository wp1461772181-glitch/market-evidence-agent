"""Deterministic V2 features from frozen, source-validated evidence.

This module deliberately does no document reading, network access, LLM work,
or probability adjustment.  It turns only already frozen facts and events into
a small, versioned numeric contract for a separately trained model.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Iterable, Mapping, Sequence


EVIDENCE_FEATURE_SCHEMA_VERSION = "evidence-features-v1"
OFFICIAL_SOURCE = "official_filing"
MEDIA_SOURCE = "uploaded_media"
SUPPORTED_SOURCE_TYPES = frozenset({OFFICIAL_SOURCE, MEDIA_SOURCE})
DEFAULT_MODEL_SOURCE_TYPES = frozenset({OFFICIAL_SOURCE})

_GUIDANCE_VALUES = {"raised": 1.0, "lowered": -1.0, "maintained": 0.0, "unknown": 0.0}
_OPERATIONAL_FEATURES = {
    "recall": "operational_recall_active",
    "product_recall": "operational_recall_active",
    "production_disruption": "operational_service_disruption_active",
    "service_disruption": "operational_service_disruption_active",
    "major_regulatory_event": "operational_regulatory_active",
    "regulatory_action": "operational_regulatory_active",
}


class EvidenceFeatureError(ValueError):
    """Raised for an invalid feature-building request, never for one bad fact."""


@dataclass(frozen=True)
class EvidenceFeatureResult:
    schema_version: str
    features: dict[str, float]
    status_codes: tuple[str, ...]
    used_event_ids: tuple[str, ...]
    skipped_event_ids: tuple[str, ...]
    supported_source_types: tuple[str, ...]

    @property
    def joint_model_eligible(self) -> bool:
        """Whether every selected source channel has a validated model path."""
        return "unsupported_evidence_channel" not in self.status_codes


def build_evidence_features(
    events: Sequence[object],
    *,
    decision_at: datetime,
    supported_source_types: Iterable[str] = DEFAULT_MODEL_SOURCE_TYPES,
) -> EvidenceFeatureResult:
    """Build a fixed feature vector from frozen context events.

    Events may be mappings or simple objects, which lets this pure layer accept
    ``EvidenceContext.events`` without importing its persistence module.  Bad
    numeric facts are rejected individually: they never become zero-valued
    financial changes, and their missing flags remain set.
    """
    cutoff = _utc_datetime(decision_at, "decision_at")
    supported = frozenset(supported_source_types)
    unknown_channels = supported - SUPPORTED_SOURCE_TYPES
    if unknown_channels:
        raise EvidenceFeatureError("supported_source_types contains an unknown source type")

    features = _empty_features()
    statuses: set[str] = set()
    used: list[str] = []
    skipped: list[str] = []
    valid_metric_candidates: dict[str, tuple[datetime, str, float]] = {}
    latest_age_days: float | None = None
    independent_sources: set[str] = set()
    media_stars: list[float] = []

    normalized_events = sorted((_normalise_event(raw) for raw in events), key=_event_sort_key)
    seen_event_source: set[tuple[str, str]] = set()
    for event in normalized_events:
        event_id = event["id"]
        source_type = event["source_type"]
        if source_type not in SUPPORTED_SOURCE_TYPES:
            statuses.add("unsupported_evidence_channel")
            skipped.append(event_id)
            continue
        if source_type not in supported:
            statuses.add("unsupported_evidence_channel")
        if not event["active"]:
            features["corrected_or_withdrawn_present"] = 1.0
            skipped.append(event_id)
            continue

        identity = (event["event_key"], event["source_id"])
        if identity in seen_event_source:
            skipped.append(event_id)
            continue
        seen_event_source.add(identity)
        used.append(event_id)
        channel_count = "official_event_count" if source_type == OFFICIAL_SOURCE else "media_event_count"
        features[channel_count] += 1.0
        features["active_event_count"] += 1.0
        if event["is_new"]:
            features["new_event_present"] = 1.0
        independent_sources.add(event["independent_source_key"])
        age_days = max(0.0, (cutoff - event["published_at"]).total_seconds() / 86_400.0)
        latest_age_days = age_days if latest_age_days is None else min(latest_age_days, age_days)
        if event["state"] in {"corrected", "withdrawn"}:
            features["corrected_or_withdrawn_present"] = 1.0
        if event["conflict"]:
            features["event_conflict"] = 1.0

        if source_type == MEDIA_SOURCE:
            stars = event["user_stars"]
            if stars is None:
                features["media_stars_missing"] = 1.0
            else:
                media_stars.append(stars / 5.0)

        _apply_guidance(features, event, statuses)
        _apply_operational_events(features, event, statuses)
        if source_type in supported:
            _collect_numeric_facts(valid_metric_candidates, event, statuses)

    features["independent_source_count"] = float(len(independent_sources))
    if latest_age_days is None:
        features["newest_event_age_days_missing"] = 1.0
    else:
        features["newest_event_age_days"] = min(latest_age_days, 365.0)
        features["newest_event_age_days_missing"] = 0.0
    if media_stars:
        features["media_stars_mean"] = sum(media_stars) / len(media_stars)
        features["media_stars_missing"] = 0.0

    for metric, (_, _, value) in valid_metric_candidates.items():
        features[f"{metric}_yoy"] = value
        features[f"{metric}_yoy_missing"] = 0.0

    return EvidenceFeatureResult(
        schema_version=EVIDENCE_FEATURE_SCHEMA_VERSION,
        features=features,
        status_codes=tuple(sorted(statuses)),
        used_event_ids=tuple(used),
        skipped_event_ids=tuple(skipped),
        supported_source_types=tuple(sorted(supported)),
    )


def _empty_features() -> dict[str, float]:
    return {
        "revenue_yoy": 0.0,
        "revenue_yoy_missing": 1.0,
        "eps_yoy": 0.0,
        "eps_yoy_missing": 1.0,
        "guidance_direction": 0.0,
        "guidance_missing": 1.0,
        "operational_recall_active": 0.0,
        "operational_service_disruption_active": 0.0,
        "operational_regulatory_active": 0.0,
        "operational_unknown": 0.0,
        "new_event_present": 0.0,
        "newest_event_age_days": 0.0,
        "newest_event_age_days_missing": 1.0,
        "active_event_count": 0.0,
        "corrected_or_withdrawn_present": 0.0,
        "event_conflict": 0.0,
        "official_event_count": 0.0,
        "media_event_count": 0.0,
        "independent_source_count": 0.0,
        "media_stars_mean": 0.0,
        "media_stars_missing": 1.0,
    }


def _normalise_event(raw: object) -> dict[str, Any]:
    source_type = _text(_read(raw, "source_type"))
    event_id = _text(_read(raw, "id"))
    source_id = _text(_read(raw, "source_id"))
    event_key = _text(_read(raw, "event_key")) or f"source:{source_type}:{source_id}"
    if not source_type or not event_id or not source_id:
        raise EvidenceFeatureError("each event needs id, source_id, and source_type")
    published_at = _utc_datetime(_read(raw, "published_at", _read(raw, "public_at")), "event published_at")
    stars = _read(raw, "user_rating_stars", _read(raw, "user_stars"))
    if stars is not None and (not _finite_number(stars) or int(stars) != stars or not 1 <= int(stars) <= 5):
        raise EvidenceFeatureError("event user stars must be an integer from 1 to 5")
    state = _text(_read(raw, "state", "active")).casefold() or "active"
    if bool(_read(raw, "is_corrected_or_withdrawn", False)) and state == "active":
        state = "corrected"
    active = bool(_read(raw, "is_active", state not in {"withdrawn", "inactive"}))
    return {
        "id": event_id,
        "source_type": source_type,
        "source_id": source_id,
        "event_key": event_key,
        "published_at": published_at,
        "user_stars": int(stars) if stars is not None else None,
        "is_new": bool(_read(raw, "is_new", False)),
        "state": state,
        "active": active,
        "conflict": bool(_read(raw, "conflict", False)),
        "independent_source_key": _text(_read(raw, "independent_source_key")) or f"{source_type}:{source_id}",
        "facts": _facts(raw),
        "guidance": _guidance(raw),
        "operational_events": _operational_events(raw),
    }


def _facts(raw: object) -> list[Mapping[str, Any]]:
    structured = _facts_mapping(raw)
    direct = _read(raw, "facts", None)
    values = direct or structured.get("facts", structured.get("numeric_facts", ()))
    return [fact for fact in values if isinstance(fact, Mapping)] if isinstance(values, Sequence) and not isinstance(values, str) else []


def _facts_mapping(raw: object) -> Mapping[str, Any]:
    value = _read(raw, "structured_facts", {})
    return value if isinstance(value, Mapping) else {}


def _operational_events(raw: object) -> list[Mapping[str, Any]]:
    structured = _facts_mapping(raw)
    direct = _read(raw, "operational_events", None)
    values = direct or structured.get("operational_events", ())
    records: list[Mapping[str, Any]] = []
    if isinstance(values, Sequence) and not isinstance(values, str):
        records.extend(value for value in values if isinstance(value, Mapping))
    event_type = _text(_read(raw, "operational_event_type"))
    if event_type:
        records.append({"event_type": event_type, "status": _read(raw, "operational_event_status", "unknown")})
    return records


def _guidance(raw: object) -> Any:
    direct = _read(raw, "guidance", None)
    if _text(direct).casefold() not in {"", "unknown"}:
        return direct
    return _facts_mapping(raw).get("guidance", direct or "unknown")


def _apply_guidance(features: dict[str, float], event: Mapping[str, Any], statuses: set[str]) -> None:
    raw = event["guidance"]
    value = _text(raw).casefold()
    if not value or value == "unknown":
        return
    if value not in _GUIDANCE_VALUES:
        statuses.add("invalid_guidance_fact")
        return
    features["guidance_direction"] = _GUIDANCE_VALUES[value]
    features["guidance_missing"] = 0.0


def _apply_operational_events(features: dict[str, float], event: Mapping[str, Any], statuses: set[str]) -> None:
    for raw in event["operational_events"]:
        event_type = _text(raw.get("event_type")).casefold()
        status = _text(raw.get("status", "unknown")).casefold()
        feature = _OPERATIONAL_FEATURES.get(event_type)
        if feature is None:
            features["operational_unknown"] = 1.0
            continue
        if status == "active":
            features[feature] = 1.0
        elif status not in {"resolved", "withdrawn", "unknown"}:
            statuses.add("invalid_operational_event")


def _collect_numeric_facts(
    candidates: dict[str, tuple[datetime, str, float]], event: Mapping[str, Any], statuses: set[str]
) -> None:
    for fact in event["facts"]:
        metric = _text(fact.get("metric", fact.get("fact_type"))).casefold()
        if metric not in {"revenue", "eps"}:
            continue
        value = _valid_yoy_value(fact)
        if value is None:
            statuses.add("invalid_numeric_fact")
            continue
        candidate = (event["published_at"], event["id"], value)
        current = candidates.get(metric)
        if current is None or candidate[:2] > current[:2]:
            candidates[metric] = candidate


def _valid_yoy_value(fact: Mapping[str, Any]) -> float | None:
    # A year-over-year value has two factual endpoints.  A single validated
    # sentence cannot support both, even when it happens to contain two
    # numbers.  Extraction must preserve a quote and frozen source anchor for
    # each endpoint before this module accepts the derived change.
    if not (
        bool(fact.get("current_quote_validated"))
        and bool(fact.get("comparison_quote_validated"))
        and _text(fact.get("current_evidence_quote", fact.get("current_quote")))
        and _text(fact.get("comparison_evidence_quote", fact.get("comparison_quote")))
        and _text(fact.get("current_source_anchor"))
        and _text(fact.get("comparison_source_anchor"))
    ):
        return None
    value, comparison = fact.get("value"), fact.get("comparison_value")
    if not _finite_number(value) or not _finite_number(comparison) or float(comparison) == 0:
        return None
    unit = _text(fact.get("unit")).casefold()
    comparison_unit = _text(fact.get("comparison_unit")).casefold()
    if not unit or unit != comparison_unit:
        return None
    current_period = _period_duration(fact, "")
    comparison_period = _period_duration(fact, "comparison_")
    if current_period is None or comparison_period is None or abs(current_period - comparison_period) > 7:
        return None
    current_end = _date_value(fact.get("period_end"))
    comparison_end = _date_value(fact.get("comparison_period_end"))
    if current_end is None or comparison_end is None or not 300 <= (current_end - comparison_end).days <= 430:
        return None
    result = float(value) / float(comparison) - 1.0
    return result if math.isfinite(result) else None


def _period_duration(fact: Mapping[str, Any], prefix: str) -> int | None:
    start = _date_value(fact.get(f"{prefix}period_start"))
    end = _date_value(fact.get(f"{prefix}period_end"))
    if start is None or end is None or end < start:
        return None
    return (end - start).days + 1


def _event_sort_key(event: Mapping[str, Any]) -> tuple[datetime, str, str]:
    return event["published_at"], event["event_key"], event["id"]


def _read(value: object, name: str, default: Any = None) -> Any:
    return value.get(name, default) if isinstance(value, Mapping) else getattr(value, name, default)


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else str(value).strip() if value is not None else ""


def _finite_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _utc_datetime(value: object, name: str) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise EvidenceFeatureError(f"{name} must be an ISO timestamp") from exc
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise EvidenceFeatureError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def _date_value(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None
