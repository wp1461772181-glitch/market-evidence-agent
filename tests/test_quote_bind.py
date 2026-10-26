from app.agent_workflow.quote_bind import bind_quote

SOURCE = "Apple Inc. Total net sales were 16,128 for fiscal 2024. Operating income follows."


def test_exact_bind_returns_source_span():
    result = bind_quote("Total net sales were 16,128 for fiscal 2024.", SOURCE)
    assert result.ok and result.match_kind == "exact"
    assert "16,128" in result.resolved_quote
    assert result.resolved_quote == "Total net sales were 16,128 for fiscal 2024."
    assert result.start_char == SOURCE.index(result.resolved_quote)


def test_too_short_rejected_even_if_exact():
    result = bind_quote("16,128", SOURCE, min_chars=24)
    assert not result.ok and result.reason == "quote_too_short"


def test_not_found_is_drop_not_pending():
    result = bind_quote("This sentence does not appear anywhere in the filing text.", SOURCE)
    assert not result.ok and result.reason == "quote_not_found"
    assert result.reason != "pending"


def test_fuzzy_near_miss_uses_resolved_source_text():
    model = "Total net sales were 16,182 for fiscal 2024."
    result = bind_quote(model, SOURCE, min_chars=24)
    assert result.ok
    assert result.match_kind == "fuzzy"
    assert "16,128" in result.resolved_quote
    assert "16,182" not in result.resolved_quote
    assert result.resolved_quote == SOURCE[result.start_char:result.end_char]


def test_min_length_applies_before_normalized_match():
    source = "Revenue was 16,128 million for the year ended."
    # Curly apostrophe-free but whitespace-normalized quote that is still too short.
    result = bind_quote("16,128 million", source, min_chars=24)
    assert not result.ok and result.reason == "quote_too_short"
