import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.research_brief import ResearchBriefError, build_research_brief, validate_research_brief


CUTOFF = datetime(2026, 9, 26, 9, tzinfo=UTC)
CONTRACT = {
    "anchor_date": "2026-09-25", "anchor_close": 100.0,
    "target_end_date": "2026-10-23", "threshold": 0.02,
    "horizon_sessions": 20, "calendar_name": "XNYS",
    "calendar_version": "xnys-v1", "price_source": "quote",
    "price_version": "quotes-v1", "price_hash": "a" * 64,
    "price_basis": "provider_quote_close_v1",
    "price_basis_check": {"price_basis": "provider_quote_close_v1", "provider_behavior_verified": True,
                           "adjusted_close_present": True, "warnings": [], "verification_notes": []},
    "target_spec_version": "absolute-close-v1",
}
MARKET = {
    "symbol": "AAPL", "as_of": CUTOFF.isoformat(), "latest_close": 100.0,
    "return_5_sessions": 0.02, "return_20_sessions": 0.04,
    "volatility_20_sessions": 0.03, "volume_ratio_20_sessions": 1.1,
}


def material(
    *, analysis_id="analysis-1", source_id="source-1", published_at=None, observed_at=None,
    stars=None, status="succeeded", review_status="accepted", coverage_incomplete=False,
    explicit=False, version_no=1, content_hash="c" * 64, payload=None,
):
    published_at = published_at or CUTOFF - timedelta(days=1)
    observed_at = observed_at or CUTOFF - timedelta(hours=1)
    item_payload = payload or {
        "schema_version": "material-analysis-v1",
        "prompt_version": "material-analysis-prompt-v1",
        "summary": "Company reported higher revenue.",
        "facts": [{"id": "f1", "statement": "Revenue rose 12 percent.", "citations": [{"quote": "Revenue rose 12 percent.", "start_char": 0, "end_char": 25}] }],
        "supporting": [], "counter": [], "uncertainties": [], "key_numbers": [],
    }
    row = {
        "analysis_id": analysis_id, "source_type": "uploaded_media", "source_id": source_id,
        "evidence_version_id": f"ev-{source_id}", "version_no": version_no, "status": status,
        "payload": item_payload, "explicit": explicit,
        "source_manifest": {
            "source_type": "uploaded_media", "source_id": source_id, "symbol": "AAPL",
            "title": "Earnings update", "source_url": "https://example.org/source",
            "published_at": published_at.isoformat(), "observed_at": observed_at.isoformat(),
            "content_sha256": content_hash, "analysis_text_sha256": "d" * 64,
            "coverage": "uploaded_text", "coverage_incomplete": coverage_incomplete,
            "truncated": coverage_incomplete, "evidence_version_id": f"ev-{source_id}",
            "review_status": review_status, "user_rating_stars": stars,
        },
    }
    return row


def pointer(analysis_id="analysis-1", item_id="f1", section="facts"):
    return {"analysis_id": analysis_id, "section": section, "item_id": item_id}


def synthesis_for(*analyses, background=False, conflicts=False):
    new_facts = []
    background_items = []
    for row in analyses:
        analysis_id = row["analysis_id"]
        cite = pointer(analysis_id)
        reason = row.get("source_manifest", {}).get("selection_reason") or row.get("selection_reason")
        if reason == "background":
            background_items.append({"statement": "Prior revenue fact remains relevant.", "citations": [cite],
                                     "continuing_reason": "No later filing withdrew it."})
        else:
            new_facts.append({"statement": "Revenue increased year over year.", "citations": [cite]})
    result = {
        "new_facts": new_facts, "supporting": [], "counter": [],
        "background": background_items, "conflicts": [], "unknowns": [], "changes": [],
    }
    if conflicts and len(analyses) >= 2:
        result["conflicts"] = [{"description": "Two materials report different revenue figures.",
                                "citations": [pointer(analyses[0]["analysis_id"]), pointer(analyses[1]["analysis_id"])]}]
    return result


class FakeProvider:
    def __init__(self, output=None, *, exception=None):
        self.output = output or {"new_facts": [], "supporting": [], "counter": [], "background": [],
                                 "conflicts": [], "unknowns": [], "changes": []}
        self.exception = exception
        self.calls = []

    def extract(self, **kwargs):
        self.calls.append(kwargs)
        if self.exception:
            raise self.exception
        return SimpleNamespace(content=json.dumps(self.output), response_model="fake", usage={})


def build(rows, provider=None, **kwargs):
    return build_research_brief(
        market_summary=kwargs.pop("market_summary", MARKET), analyses=rows,
        target_contract=kwargs.pop("target_contract", CONTRACT), decision_at=kwargs.pop("decision_at", CUTOFF),
        parent_brief=kwargs.pop("parent_brief", None), provider=provider, **kwargs,
    )


def test_build_attaches_server_fields_and_validates_analysis_item_pointers():
    row = material(stars=4)
    provider = FakeProvider(synthesis_for(row))
    brief = build([row], provider)

    assert brief.schema_version == "research-brief-v1"
    assert brief.target_contract == CONTRACT
    assert brief.market_summary == MARKET
    assert brief.material_refs[0].analysis_id == "analysis-1"
    assert brief.material_refs[0].user_rating_stars == 4
    assert brief.material_refs[0].user_rating_label == "user self-rating: 80%"
    assert [cite.model_dump() for cite in brief.new_facts[0].citations] == [pointer()]
    assert brief.input_quality.status == "ready"
    sent = json.loads(provider.calls[0]["document_payload"])
    assert sent["target_contract"] == CONTRACT
    assert "Never" not in provider.calls[0]["system_prompt"]
    assert "change_type" in provider.calls[0]["system_prompt"]
    assert '"maxItems"' in provider.calls[0]["system_prompt"]
    assert '"maxLength"' in provider.calls[0]["system_prompt"]


def test_server_metadata_cannot_be_overwritten_by_model_output():
    row = material()
    output = synthesis_for(row)
    output["target_contract"] = {"anchor_close": 0}
    provider = FakeProvider(output)
    with pytest.raises(ResearchBriefError) as error:
        build([row], provider)
    assert error.value.code == "invalid_model_output"


def test_unknown_analysis_section_or_item_is_rejected():
    row = material()
    output = synthesis_for(row)
    output["new_facts"][0]["citations"] = [pointer(item_id="f-does-not-exist")]
    with pytest.raises(ResearchBriefError, match="unknown analysis item|new fact"):
        build([row], FakeProvider(output))


def test_explicitly_selected_future_material_is_blocked_and_unselected_future_is_omitted():
    future = material(published_at=CUTOFF + timedelta(minutes=1), observed_at=CUTOFF - timedelta(minutes=1))
    with pytest.raises(ResearchBriefError) as error:
        build([future], FakeProvider(), explicit_source_refs=[{"source_type": "uploaded_media", "source_id": "source-1"}])
    assert error.value.code == "future_evidence"

    brief = build([future], FakeProvider())
    assert brief.material_refs == []
    assert brief.omitted[0].reason == "future_not_observable"
    assert brief.input_quality.status == "insufficient"


def test_groups_publication_late_discovery_and_background_against_parent_cutoff():
    base = material(published_at=CUTOFF - timedelta(days=10), observed_at=CUTOFF - timedelta(days=9))
    parent_provider = FakeProvider(synthesis_for(base))
    parent = build([base], parent_provider)
    next_cutoff = CUTOFF + timedelta(days=1)
    rows = [
        material(analysis_id="new", source_id="new", published_at=CUTOFF + timedelta(hours=1), observed_at=CUTOFF + timedelta(hours=2)),
        material(analysis_id="late", source_id="late", published_at=CUTOFF - timedelta(days=30), observed_at=CUTOFF + timedelta(hours=3)),
        material(analysis_id="old", source_id="old", published_at=CUTOFF - timedelta(days=40), observed_at=CUTOFF - timedelta(days=39)),
    ]
    for row in rows:
        row["source_manifest"]["selection_reason"] = "new_publication" if row["analysis_id"] == "new" else "newly_observed"
    rows[-1]["source_manifest"]["selection_reason"] = "background"
    # A current parent version should be classified as background even when not marked explicitly.
    rows[-1]["source_manifest"]["content_sha256"] = parent.material_refs[0].content_sha256
    provider = FakeProvider(synthesis_for(*rows))
    brief = build(rows, provider, parent_brief=parent, decision_at=next_cutoff)
    reasons = {ref.analysis_id: ref.selection_reason for ref in brief.material_refs}
    assert reasons == {"new": "new_publication", "late": "newly_observed", "old": "background"}


def test_no_new_material_carries_parent_risks_and_refreshes_market_without_model_call():
    row = material()
    parent = build([row], FakeProvider(synthesis_for(row)))
    inherited_row = material(analysis_id="analysis-1", source_id="source-1")
    fresh_market = dict(MARKET, latest_close=101.0, as_of=(CUTOFF + timedelta(days=1)).isoformat())
    provider = FakeProvider()
    brief = build([inherited_row], provider, market_summary=fresh_market, parent_brief=parent, decision_at=CUTOFF + timedelta(days=1))

    assert provider.calls == []
    assert brief.market_summary["latest_close"] == 101.0
    assert brief.new_facts == []
    assert brief.background[0].statement == parent.new_facts[0].statement
    assert brief.background[0].continuing_reason
    assert brief.material_refs[0].selection_reason == "background"


def test_parent_sources_all_rejected_are_not_restored_into_empty_revision():
    rows = [
        material(analysis_id="analysis-1", source_id="source-1"),
        material(analysis_id="analysis-2", source_id="source-2"),
    ]
    parent = build(rows, FakeProvider(synthesis_for(*rows)))
    rejected = [material(analysis_id=row["analysis_id"], source_id=row["source_id"], review_status="rejected") for row in rows]
    provider = FakeProvider()

    brief = build(rejected, provider, parent_brief=parent, decision_at=CUTOFF + timedelta(days=1))

    assert provider.calls == []
    assert brief.material_refs == []
    assert brief.background == []
    assert {item.reason for item in brief.omitted} == {"source_rejected"}
    assert brief.input_quality.status == "insufficient"


def test_parent_carries_only_still_valid_sources_when_another_is_rejected():
    rows = [
        material(analysis_id="analysis-1", source_id="source-1"),
        material(analysis_id="analysis-2", source_id="source-2"),
    ]
    parent = build(rows, FakeProvider(synthesis_for(*rows)))
    currently_valid = material(analysis_id="analysis-1", source_id="source-1")
    rejected = material(analysis_id="analysis-2", source_id="source-2", review_status="rejected")
    provider = FakeProvider(synthesis_for({**currently_valid, "selection_reason": "background"}))

    brief = build([currently_valid, rejected], provider, parent_brief=parent, decision_at=CUTOFF + timedelta(days=1))

    assert len(provider.calls) == 1
    assert [ref.analysis_id for ref in brief.material_refs] == ["analysis-1"]
    cited_ids = {cite.analysis_id for item in brief.background for cite in item.citations}
    assert cited_ids == {"analysis-1"}
    assert [item.reason for item in brief.omitted] == ["source_rejected"]
    assert any(item.change_type == "source_status_changed" for item in brief.changes)


def test_parent_resynthesizes_same_content_reanalysis_and_review_changes():
    original = material(analysis_id="analysis-v1", source_id="source-1", stars=2)
    parent = build([original], FakeProvider(synthesis_for(original)))

    reanalysis = material(analysis_id="analysis-v2", source_id="source-1", stars=2)
    reanalysis_provider = FakeProvider(synthesis_for({**reanalysis, "selection_reason": "background"}))
    reanalysis_brief = build(
        [reanalysis], reanalysis_provider, parent_brief=parent,
        decision_at=CUTOFF + timedelta(days=1),
    )
    assert len(reanalysis_provider.calls) == 1
    assert reanalysis_brief.material_refs[0].selection_reason == "background"
    assert reanalysis_brief.material_refs[0].analysis_id == "analysis-v2"
    assert any(change.change_type == "modified" for change in reanalysis_brief.changes)

    reviewed = material(analysis_id="analysis-v1", source_id="source-1", stars=5)
    review_provider = FakeProvider(synthesis_for({**reviewed, "selection_reason": "background"}))
    review_brief = build(
        [reviewed], review_provider, parent_brief=parent,
        decision_at=CUTOFF + timedelta(days=1),
    )
    assert len(review_provider.calls) == 1
    assert review_brief.material_refs[0].user_rating_stars == 5
    assert any(change.change_type == "source_status_changed" for change in review_brief.changes)


def test_parent_sources_missing_from_current_inputs_are_unknown_and_not_carried():
    row = material(analysis_id="analysis-1", source_id="source-1")
    parent = build([row], FakeProvider(synthesis_for(row)))

    brief = build([], FakeProvider(), parent_brief=parent, decision_at=CUTOFF + timedelta(days=1))

    assert brief.material_refs == []
    assert brief.background == []
    assert [(item.analysis_id, item.reason) for item in brief.omitted] == [("analysis-1", "analysis_unavailable")]
    assert brief.input_quality.status == "insufficient"


def test_model_must_not_invent_or_reweight_user_rating_as_probability():
    row = material(stars=5)
    output = synthesis_for(row)
    output["new_facts"][0]["probability_weight"] = 1.0
    with pytest.raises(ResearchBriefError, match="valid cited synthesis"):
        build([row], FakeProvider(output))


def test_rejected_source_is_excluded_and_recorded():
    rejected = material(review_status="rejected")
    brief = build([rejected], FakeProvider())
    assert brief.material_refs == []
    assert brief.omitted[0].reason == "source_rejected"


def test_explicit_selection_over_limit_returns_422_style_error():
    refs = [{"source_type": "uploaded_media", "source_id": str(index)} for index in range(9)]
    with pytest.raises(ResearchBriefError) as error:
        build([], FakeProvider(), explicit_source_refs=refs)
    assert error.value.status_code == 422
    assert error.value.code == "explicit_material_limit"


def test_caps_selection_to_five_new_and_three_background_and_marks_omissions():
    rows = []
    for index in range(6):
        row = material(analysis_id=f"new-{index}", source_id=f"new-{index}", published_at=CUTOFF - timedelta(hours=index + 1))
        rows.append(row)
    provider = FakeProvider({"new_facts": [], "supporting": [], "counter": [], "background": [], "conflicts": [], "unknowns": [], "changes": []})
    brief = build(rows, provider)
    assert len(brief.material_refs) == 5
    assert any(item.reason == "new_material_limit" for item in brief.omitted)


def test_failed_explicit_analyses_yield_insufficient_without_provider_call():
    row = material(status="failed", explicit=True)
    provider = FakeProvider()
    brief = build([row], provider)
    assert provider.calls == []
    assert brief.input_quality.status == "insufficient"
    assert brief.omitted[0].reason == "analysis_not_succeeded"


def test_conflicts_need_two_distinct_valid_citations():
    rows = [material(analysis_id="one", source_id="one"), material(analysis_id="two", source_id="two")]
    output = synthesis_for(*rows, conflicts=True)
    brief = build(rows, FakeProvider(output))
    assert len(brief.conflicts) == 1

    output["conflicts"][0]["citations"] = [pointer("one"), pointer("one")]
    with pytest.raises(ResearchBriefError, match="two distinct"):
        build(rows, FakeProvider(output))


def test_limited_coverage_and_missing_optional_market_fields_are_visible():
    row = material(coverage_incomplete=True)
    sparse_market = {"symbol": "AAPL", "as_of": CUTOFF.isoformat(), "latest_close": 100.0}
    brief = build([row], FakeProvider(synthesis_for(row)), market_summary=sparse_market)
    assert brief.input_quality.status == "limited"
    assert any("limited text coverage" in reason for reason in brief.input_quality.reasons)


def test_future_observed_at_and_future_market_snapshot_are_blocked():
    row = material(observed_at=CUTOFF + timedelta(seconds=1))
    with pytest.raises(ResearchBriefError, match="not observable"):
        build([row], FakeProvider(), explicit_source_refs=[{"source_type": "uploaded_media", "source_id": "source-1"}])
    with pytest.raises(ResearchBriefError) as error:
        build([], FakeProvider(), market_summary=dict(MARKET, as_of=(CUTOFF + timedelta(days=1)).isoformat()))
    assert error.value.code == "future_market_data"


def test_duplicate_frozen_source_content_keeps_latest_successful_analysis():
    first = material(analysis_id="older", version_no=1)
    second = material(analysis_id="latest", version_no=2)
    provider = FakeProvider({"new_facts": [], "supporting": [], "counter": [], "background": [], "conflicts": [], "unknowns": [], "changes": []})
    brief = build([first, second], provider)
    assert [ref.analysis_id for ref in brief.material_refs] == ["latest"]
    assert [item.reason for item in brief.omitted] == ["duplicate_source_content"]


def test_validate_brief_rejects_citation_to_unselected_analysis():
    row = material()
    brief = build([row], FakeProvider(synthesis_for(row)))
    invalid = brief.model_dump(mode="json")
    invalid["new_facts"][0]["citations"][0]["analysis_id"] = "other-analysis"
    with pytest.raises(ResearchBriefError, match="outside material_refs"):
        validate_research_brief(invalid)
