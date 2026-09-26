import math

import pytest

from app import jev_provider
from app.jev_provider import JevDecisionProvider, JevProviderError


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


def result_payload(*, probabilities=None, choice="bullish", confidence=0.72):
    return {
        "model": "typesafe/jev-1.13-20260917",
        "answers": {
            "direction": {
                "type": "choice",
                "choice": choice,
                "probabilities": probabilities or {"bullish": 0.72, "neutral": 0.18, "bearish": 0.10},
                "confidence": confidence,
            }
        },
        "usage": {"input_tokens": 123, "output_tokens": 19},
        "id": "gen-dec-example",
    }


def provider(client):
    return JevDecisionProvider(api_key="test-key-do-not-print", client=client)


def test_success_parses_distribution_and_sends_only_decisions_request():
    brief = {"schema_version": "research-brief-v1", "symbol": "AAPL", "decision_at": "2026-09-26"}
    client = FakeClient(FakeResponse(200, result_payload()))

    result = provider(client).evaluate(brief)

    assert result.probabilities == {"bullish": 0.72, "neutral": 0.18, "bearish": 0.1}
    assert result.choice == "bullish"
    assert result.confidence == 0.72
    assert result.requested_model == "typesafe/jev-1.13"
    assert result.actual_model == "typesafe/jev-1.13-20260917"
    assert result.request_id == "gen-dec-example"
    assert result.usage["input_tokens"] == 123
    assert result.question_version == "jev-direction-v1"
    url, kwargs = client.calls[0]
    assert url == "https://openrouter.ai/api/alpha/decisions"
    assert kwargs["timeout"] == 30
    assert kwargs["json"]["state"] == brief
    assert kwargs["json"]["questions"]["direction"]["criteria"].keys() == {"bullish", "neutral", "bearish"}
    assert kwargs["headers"]["Authorization"] == "Bearer test-key-do-not-print"


def test_tied_maximum_is_valid():
    payload = result_payload(probabilities={"bullish": 0.5, "neutral": 0.5, "bearish": 0}, choice="neutral")
    assert provider(FakeClient(FakeResponse(200, payload))).evaluate({}).choice == "neutral"


@pytest.mark.parametrize("value", [True, float("nan"), float("inf"), -float("inf"), -0.01, 1.01, "0.5"])
def test_rejects_invalid_probability_values(value):
    payload = result_payload(probabilities={"bullish": value, "neutral": 0.25, "bearish": 0.25})
    with pytest.raises(JevProviderError):
        provider(FakeClient(FakeResponse(200, payload))).evaluate({})


@pytest.mark.parametrize(
    "probabilities",
    [
        {"bullish": 0.4, "neutral": 0.4},
        {"bullish": 0.4, "neutral": 0.3, "bearish": 0.3, "other": 0},
        {"bullish": 0.4, "neutral": 0.4, "bearish": 0.1},
    ],
)
def test_rejects_missing_extra_or_non_normalized_probability_map(probabilities):
    with pytest.raises(JevProviderError):
        provider(FakeClient(FakeResponse(200, result_payload(probabilities=probabilities)))).evaluate({})


def test_rejects_choice_that_is_not_one_of_the_maxima():
    payload = result_payload(probabilities={"bullish": 0.7, "neutral": 0.2, "bearish": 0.1}, choice="neutral")
    with pytest.raises(JevProviderError, match="invalid_choice"):
        provider(FakeClient(FakeResponse(200, payload))).evaluate({})


@pytest.mark.parametrize("confidence", [True, "0.5", float("nan"), float("inf"), -0.1, 1.1])
def test_rejects_invalid_confidence(confidence):
    with pytest.raises(JevProviderError, match="invalid_confidence"):
        provider(FakeClient(FakeResponse(200, result_payload(confidence=confidence)))).evaluate({})


@pytest.mark.parametrize("status", [401, 402, 403])
def test_auth_and_billing_errors_are_not_retried(status):
    client = FakeClient(FakeResponse(status, {"error": "contains private material"}), FakeResponse(200, result_payload()))
    with pytest.raises(JevProviderError) as caught:
        provider(client).evaluate({})
    assert caught.value.status_code == status
    assert len(client.calls) == 1
    assert "private material" not in str(caught.value)
    assert "test-key-do-not-print" not in str(caught.value)


@pytest.mark.parametrize("status", [429, 503])
def test_retryable_statuses_back_off_and_succeed_within_three_attempts(monkeypatch, status):
    delays = []
    monkeypatch.setattr(jev_provider.time, "sleep", delays.append)
    client = FakeClient(FakeResponse(status), FakeResponse(200, result_payload()))
    provider(client).evaluate({})
    assert len(client.calls) == 2
    assert delays == [0.2]


def test_retry_limit_is_three_http_attempts(monkeypatch):
    delays = []
    monkeypatch.setattr(jev_provider.time, "sleep", delays.append)
    client = FakeClient(FakeResponse(503), FakeResponse(503), FakeResponse(503), FakeResponse(200, result_payload()))
    with pytest.raises(JevProviderError) as caught:
        provider(client).evaluate({})
    assert caught.value.status_code == 503
    assert caught.value.retryable is True
    assert len(client.calls) == 3
    assert delays == [0.2, 0.4]


def test_transport_failure_retries_then_succeeds(monkeypatch):
    monkeypatch.setattr(jev_provider.time, "sleep", lambda _: None)
    client = FakeClient(TimeoutError("private timeout detail"), FakeResponse(200, result_payload()))
    provider(client).evaluate({})
    assert len(client.calls) == 2


def test_transport_failure_message_is_sanitized_after_retry_limit(monkeypatch):
    monkeypatch.setattr(jev_provider.time, "sleep", lambda _: None)
    client = FakeClient(*(TimeoutError("test-key-do-not-print") for _ in range(3)))
    with pytest.raises(JevProviderError) as caught:
        provider(client).evaluate({})
    assert caught.value.code == "transport_error"
    assert "test-key-do-not-print" not in str(caught.value)
    assert len(client.calls) == 3


def test_unknown_transport_exception_is_sanitized_without_retry():
    client = FakeClient(RuntimeError("test-key-do-not-print"), FakeResponse(200, result_payload()))
    with pytest.raises(JevProviderError) as caught:
        provider(client).evaluate({})
    assert caught.value.code == "transport_error"
    assert "test-key-do-not-print" not in str(caught.value)
    assert len(client.calls) == 1


def test_invalid_json_and_unexpected_request_errors_are_not_retried():
    malformed = FakeClient(FakeResponse(200, ValueError("key-like response detail")), FakeResponse(200, result_payload()))
    with pytest.raises(JevProviderError, match="invalid_json_response"):
        provider(malformed).evaluate({})
    assert len(malformed.calls) == 1

    rejected = FakeClient(FakeResponse(422), FakeResponse(200, result_payload()))
    with pytest.raises(JevProviderError, match="request_rejected"):
        provider(rejected).evaluate({})
    assert len(rejected.calls) == 1


def test_rejects_invalid_brief_before_http_request():
    client = FakeClient()
    with pytest.raises(JevProviderError, match="invalid_brief"):
        provider(client).evaluate({"bad": math.nan})
    assert client.calls == []


def test_env_factory_loads_only_project_env_and_honors_default_model(monkeypatch):
    loaded = []
    monkeypatch.setattr(JevDecisionProvider, "_load_project_env", lambda self=None: loaded.append(True))
    monkeypatch.setenv("OPENROUTER_API_KEY", "present-but-never-print")
    monkeypatch.delenv("JEV_MODEL", raising=False)
    created = JevDecisionProvider()
    assert created.model == "typesafe/jev-1.13"
    assert loaded == [True]


def test_provider_rejects_other_models():
    with pytest.raises(JevProviderError, match="unsupported_model"):
        JevDecisionProvider(api_key="x", model="another/vendor-model", client=FakeClient())


def test_redirect_is_rejected_without_following_or_forwarding_credentials(monkeypatch):
    class RedirectResponse:
        status = 302

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, count):
            return b""

    class StubOpener:
        def __init__(self):
            self.requests = []

        def open(self, request, timeout):
            self.requests.append((request, timeout))
            return RedirectResponse()

    opener = StubOpener()
    handlers = []
    monkeypatch.setattr(jev_provider.urllib.request, "build_opener", lambda *items: handlers.extend(items) or opener)
    with pytest.raises(JevProviderError, match="request_rejected"):
        JevDecisionProvider(api_key="never-forward-this", client=jev_provider._UrllibClient()).evaluate({})
    assert len(opener.requests) == 1
    assert opener.requests[0][0].full_url == "https://openrouter.ai/api/alpha/decisions"
    assert opener.requests[0][0].get_header("Authorization") == "Bearer never-forward-this"
    assert len(handlers) == 1
    assert handlers[0]().redirect_request(None, None, 302, "Found", {}, "https://attacker.example/") is None


def test_oversized_response_is_bounded_and_rejected(monkeypatch):
    class LargeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, count):
            assert count == jev_provider.MAX_RESPONSE_BYTES + 1
            return b"x" * count

    class StubOpener:
        def open(self, request, timeout):
            return LargeResponse()

    monkeypatch.setattr(jev_provider.urllib.request, "build_opener", lambda *items: StubOpener())
    with pytest.raises(JevProviderError, match="response_too_large"):
        JevDecisionProvider(api_key="x", client=jev_provider._UrllibClient()).evaluate({})
