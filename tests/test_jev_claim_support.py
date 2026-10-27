import ast
from pathlib import Path

from app.jev_claim_support import (
    QUESTION_VERSION,
    evaluate_claim_support,
    evaluate_claim_supports,
)


class FakeResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self.payload = payload

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class FakeClient:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def support_payload(choice="supports", probabilities=None, question_id="claim"):
    return {
        "model": "typesafe/jev-1.13-20260917",
        "answers": {
            question_id: {
                "type": "choice",
                "choice": choice,
                "probabilities": probabilities or {
                    "supports": 0.8,
                    "does_not_support": 0.15,
                    "unrelated": 0.05,
                },
            }
        },
        "id": "gen-claim-1",
        "usage": {"input_tokens": 20, "output_tokens": 4},
    }


def test_supports_and_does_not_support_parse_with_claim_question_version():
    client = FakeClient(FakeResponse(200, support_payload()))
    result = evaluate_claim_support("Total net sales were 16,128.", "Revenue was 16,128.", client=client, api_key="test-key")
    assert result.status == "ok"
    assert result.choice == "supports"
    assert result.probabilities["supports"] == 0.8
    assert result.question_version == "jev-claim-support-v4"
    assert result.question_version != "jev-direction-v1"
    url, kwargs = client.calls[0]
    assert url == "https://openrouter.ai/api/alpha/decisions"
    assert kwargs["timeout"] == 5
    assert kwargs["json"]["state"] == {"quote": "Total net sales were 16,128.", "claim": "Revenue was 16,128."}
    assert set(kwargs["json"]["questions"]) == {"claim"}
    assert set(kwargs["json"]["questions"]["claim"]["criteria"]) == {"supports", "does_not_support", "unrelated"}
    denied = evaluate_claim_support(
        "Total net sales were 16,128.",
        "Revenue was 99.",
        client=FakeClient(FakeResponse(200, support_payload("does_not_support", {
            "supports": 0.1, "does_not_support": 0.8, "unrelated": 0.1,
        }))),
        api_key="test-key",
    )
    assert denied.status == "ok" and denied.choice == "does_not_support"


def test_http_500_is_a_single_attempt():
    client = FakeClient(FakeResponse(500, {}), FakeResponse(200, support_payload()))
    result = evaluate_claim_support("quote text long enough", "claim", client=client, api_key="test-key")
    assert result.status == "http_error"
    assert len(client.calls) == 1
    assert client.results, "the success response must not have been consumed"


def test_timeout_is_not_retried():
    client = FakeClient(TimeoutError("slow"), FakeResponse(200, support_payload()))
    result = evaluate_claim_support("quote text long enough", "claim", client=client, api_key="test-key")
    assert result.status == "timeout"
    assert len(client.calls) == 1


def test_daily_cap_short_circuits_before_http(tmp_path, monkeypatch):
    path = tmp_path / "claim-jev-daily.json"
    monkeypatch.setenv("CLAIM_JEV_DAILY_PATH", str(path))
    monkeypatch.setenv("CLAIM_JEV_DAILY_CAP", "1")
    client = FakeClient(FakeResponse(200, support_payload()), FakeResponse(200, support_payload()))
    first = evaluate_claim_support("q" * 30, "claim", client=client, api_key="test-key")
    second = evaluate_claim_support("q" * 30, "claim", client=client, api_key="test-key")
    assert first.status == "ok"
    assert second.status == "daily_cap"
    assert len(client.calls) == 1


class ByClaimClient:
    """Answers by the claim in ``state`` so thread order does not matter."""

    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        claim = kwargs["json"]["state"]["claim"]
        answer = self.answers[claim]
        if isinstance(answer, Exception):
            raise answer
        return answer


def test_each_item_is_its_own_request_with_flat_state_and_no_forecast_learning():
    client = ByClaimClient({
        "claim one": FakeResponse(200, support_payload("supports", {
            "supports": 0.9, "does_not_support": 0.05, "unrelated": 0.05,
        })),
        "claim two": FakeResponse(200, support_payload("unrelated", {
            "supports": 0.1, "does_not_support": 0.1, "unrelated": 0.8,
        })),
        "claim three": FakeResponse(200, support_payload("does_not_support", {
            "supports": 0.1, "does_not_support": 0.8, "unrelated": 0.1,
        })),
    })
    results = evaluate_claim_supports(
        [("c1", "quote one " * 4, "claim one"), ("c2", "quote two " * 4, "claim two"),
         ("c3", "quote three " * 4, "claim three")],
        client=client,
        api_key="test-key",
        daily_cap=None,
    )
    assert [item.choice for item in results] == ["supports", "unrelated", "does_not_support"]
    assert len(client.calls) == 3
    states = sorted((call[1]["json"]["state"] for call in client.calls), key=lambda s: s["claim"])
    for state in states:
        assert set(state) == {"quote", "claim"}
    assert {s["claim"]: s["quote"] for s in states} == {
        "claim one": "quote one " * 4, "claim two": "quote two " * 4, "claim three": "quote three " * 4,
    }
    for _url, kwargs in client.calls:
        assert "claims" not in kwargs["json"]["state"]
        assert set(kwargs["json"]["questions"]) == {"claim"}
    assert len({r.input_sha256 for r in results}) == 3
    source = Path("app/jev_claim_support.py").read_text()
    tree = ast.parse(source)
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    assert "app.jev_learning" not in imported
    assert "jev_learning" not in source
    assert QUESTION_VERSION == "jev-claim-support-v4"


def test_failures_are_per_item_and_cap_counts_each_request(tmp_path):
    client = ByClaimClient({
        "ok claim": FakeResponse(200, support_payload()),
        "slow claim": TimeoutError("slow"),
        "bad claim": FakeResponse(500, {}),
    })
    results = evaluate_claim_supports(
        [("a", "q" * 30, "ok claim"), ("b", "q" * 30, "slow claim"), ("c", "q" * 30, "bad claim")],
        client=client,
        api_key="test-key",
        daily_cap=None,
    )
    assert [r.status for r in results] == ["ok", "timeout", "http_error"]
    assert len(client.calls) == 3

    path = tmp_path / "daily.json"
    capped_client = ByClaimClient({"ok claim": FakeResponse(200, support_payload())})
    capped = evaluate_claim_supports(
        [("a", "q" * 30, "ok claim"), ("b", "q" * 30, "ok claim"), ("c", "q" * 30, "ok claim")],
        client=capped_client,
        api_key="test-key",
        daily_cap=2,
        daily_path=str(path),
    )
    assert [r.status for r in capped] == ["ok", "ok", "daily_cap"]
    assert len(capped_client.calls) == 2


def test_resolve_filing_issuer_alias_maps_the_company_to_claim_issuer():
    from app.jev_claim_support import _resolve_filing_issuer_alias
    quote = "As of September 28, 2024, the Company had approximately 164,000 full-time equivalent employees."
    claim = "Apple had about 164,000 full-time equivalent employees at fiscal year end 2024."
    assert "Apple" in _resolve_filing_issuer_alias(quote, claim)
    assert "the Company" not in _resolve_filing_issuer_alias(quote, claim)
