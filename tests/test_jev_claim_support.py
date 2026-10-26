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
    assert result.question_version == "jev-claim-support-v1"
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


def test_batch_uses_one_request_and_does_not_import_forecast_learning():
    client = FakeClient(FakeResponse(200, {
        "model": "typesafe/jev-1.13-20260917",
        "answers": {
            "c1": {"type": "choice", "choice": "supports", "probabilities": {"supports": 0.9, "does_not_support": 0.05, "unrelated": 0.05}},
            "c2": {"type": "choice", "choice": "unrelated", "probabilities": {"supports": 0.1, "does_not_support": 0.1, "unrelated": 0.8}},
        },
        "id": "gen-batch",
    }))
    results = evaluate_claim_supports(
        [("c1", "quote one " * 4, "claim one"), ("c2", "quote two " * 4, "claim two")],
        client=client,
        api_key="test-key",
        daily_cap=None,
    )
    assert [item.choice for item in results] == ["supports", "unrelated"]
    assert len(client.calls) == 1
    state = client.calls[0][1]["json"]["state"]
    assert set(state) == {"claims"}
    assert [row["id"] for row in state["claims"]] == ["c1", "c2"]
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
    assert QUESTION_VERSION == "jev-claim-support-v1"
