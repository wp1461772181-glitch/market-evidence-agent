import json
from collections import Counter

from app.agent_workflow.claim_review import apply_claim_gate
from app.jev_claim_support import ClaimSupportResult
from scripts import score_claim_jev_labels as scorer

ROWS = scorer.load_labels()


def test_label_set_is_scorable_not_example_only():
    ids = [row["id"] for row in ROWS]
    assert len(ids) == len(set(ids))
    counts = Counter(row["label"] for row in ROWS)
    assert set(counts) == set(scorer.LABELS)
    assert counts["supports"] >= 10 and counts["does_not_support"] >= 10 and counts["unrelated"] >= 8
    for row in ROWS:
        assert row["quote"].strip() and row["claim"].strip() and row["notes"].strip() and row["origin"]
        assert "example" not in row["notes"].lower()
    origins = Counter(row["origin"].split(":")[0] for row in ROWS)
    assert origins["fixture"] >= 5 and origins["shadow-style"] >= 30


def test_score_counts_precision_recall_and_true_sentence_drops():
    rows = [
        {"id": "a", "label": "does_not_support"},
        {"id": "b", "label": "does_not_support"},
        {"id": "c", "label": "supports"},
        {"id": "d", "label": "unrelated"},
        {"id": "e", "label": "supports"},
    ]
    predictions = {
        "a": scorer.Prediction("does_not_support"),
        "b": scorer.Prediction(None, "timeout"),
        "c": scorer.Prediction("does_not_support"),
        "d": scorer.Prediction("unrelated"),
    }
    result = scorer.score(rows, predictions, evaluator="t")
    assert result.dns_precision == 0.5
    assert result.dns_recall == 0.5
    assert result.supports_drop_rate == 0.5
    assert result.ok_rate == 0.6
    assert result.confusion["does_not_support"]["unchecked"] == 1
    assert result.confusion["supports"]["unchecked"] == 1
    assert not result.passes()


def test_oracle_passes_and_local_checker_is_reported():
    oracle = {row["id"]: scorer.Prediction(row["label"]) for row in ROWS}
    assert scorer.score(ROWS, oracle, evaluator="oracle").passes()
    predictions, _ = scorer.local_predictions(ROWS)
    local = scorer.score(ROWS, predictions, evaluator="local")
    assert local.passes(), f"local checker misses the enforcement bar: {local.confusion}"
    assert "does_not_support precision" in scorer.render(local, ROWS)


def test_live_batches_like_production_and_skips_the_daily_counter():
    calls = []

    def fake(items, **kwargs):
        calls.append((len(items), kwargs))
        return [ClaimSupportResult("supports", {"supports": 1.0, "does_not_support": 0.0, "unrelated": 0.0}, "ok") for _ in items]

    predictions, latencies = scorer.live_predictions(ROWS, batch_size=4, evaluate=fake)
    assert len(predictions) == len(ROWS)
    assert max(n for n, _ in calls) == 4 and len(latencies) == len(calls)
    assert all(kwargs == {"daily_cap": None} for _, kwargs in calls)


def test_replay_round_trip_and_check_exit_code(tmp_path):
    replay = tmp_path / "replay.json"
    replay.write_text(json.dumps({
        "request_latency_ms": [800, 1200],
        "results": {row["id"]: {"choice": row["label"], "status": "ok"} for row in ROWS},
    }))
    out = tmp_path / "section.md"
    assert scorer.main(["--replay", str(replay), "--check", "--out", str(out)]) == 0
    assert "p50 1000 ms" in out.read_text()
    replay.write_text(json.dumps({"results": {row["id"]: {"choice": "does_not_support", "status": "ok"} for row in ROWS}}))
    assert scorer.main(["--replay", str(replay), "--check", "--out", str(out)]) == 1


def _gate_row(row, choice):
    claims = {"claims": [{
        "id": "c1",
        "statement": row["claim"],
        "citation_ordinal": 1,
        "evidence": {"type": "quote", "evidence_quote": row["quote"].splitlines()[-1]},
    }]}
    citations = [{"ordinal": 1, "quote": row["quote"], "source_type": "official_filing"}]

    def support(items, **kwargs):
        return [ClaimSupportResult(choice, {name: float(name == choice) for name in scorer.LABELS}, "ok") for _ in items]

    return apply_claim_gate(
        f"{row['claim']} [1]\n" + json.dumps(claims, ensure_ascii=False),
        citations, mode="enforce", support_mode="enforce", support_fn=support,
    )


def test_enforce_on_labelled_set_drops_only_does_not_support_rows():
    reached = Counter()
    for row in ROWS:
        application = _gate_row(row, row["label"])
        if not application.audit["jev_support_calls"]:
            continue  # the quote gate already rejected it before Jev
        reached[row["label"]] += 1
        dropped = [issue["reason"] for issue in application.issues]
        if row["label"] == "does_not_support":
            assert dropped == ["does_not_support"], row["id"]
            assert row["claim"] not in application.kept_prose
        else:
            assert dropped == [], row["id"]
            assert row["claim"] in application.kept_prose, row["id"]
    assert reached["supports"] >= 5 and reached["does_not_support"] >= 5 and reached["unrelated"] >= 2


def test_reached_subset_matches_the_gate():
    reached = [row["id"] for row in ROWS if scorer.reaches_jev(row)]
    assert "fx-revenue-exact" in reached and "fx-millions-header" in reached
    assert "fx-digit-typo" not in reached  # unsupported_numeric before Jev
