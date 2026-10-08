"""Unit tests for the Ollama provider (spec sections 17-19, 22).

Every test injects either a fake poster or a fake connection, so no
Ollama server is required.
"""

from __future__ import annotations

import asyncio
import errno
import http.client
import json
from typing import Any

import pytest

import simulation.models.ollama as ollama_module
from simulation.actions.actions import Action
from simulation.experiments.config import LLMSettings, load_config
from simulation.models.base import ChatMessage
from simulation.models.llm import ModelConfig
from simulation.models.ollama import (
    OllamaError,
    OllamaProvider,
    build_payload,
    think_from_effort,
)

MESSAGES = [ChatMessage(role="user", content="pick an action")]


def make_config(**overrides: Any) -> ModelConfig:
    base: dict[str, Any] = {
        "name": "hauhau-qwen:latest",
        "base_url": "http://localhost:11434",
        "temperature": 0.2,
        "max_tokens": 256,
        "timeout_seconds": 7,
        "options": {"num_ctx": 8192},
    }
    base.update(overrides)
    return ModelConfig(**base)


# ----------------------------------------------------------------------
# Payload construction
# ----------------------------------------------------------------------


def test_payload_carries_configured_model_and_details() -> None:
    payload = build_payload(MESSAGES, Action, make_config(reasoning_effort="none"))
    assert payload["model"] == "hauhau-qwen:latest"
    assert payload["stream"] is False
    assert payload["think"] is False  # hybrid thinking is off (effort: none)
    assert payload["messages"] == [{"role": "user", "content": "pick an action"}]
    assert payload["format"]["title"] == "Action"  # JSON schema for structured output
    assert payload["options"] == {
        "temperature": 0.2,
        "num_predict": 256,
        "num_ctx": 8192,
    }


@pytest.mark.parametrize(
    ("effort", "expected"),
    [
        ("none", False),
        ("OFF", False),
        ("low", True),
        ("medium", True),
        ("high", True),
    ],
)
def test_reasoning_effort_maps_to_think_flag(effort: str, expected: bool) -> None:
    payload = build_payload(MESSAGES, Action, make_config(reasoning_effort=effort))
    assert payload["think"] is expected


def test_unknown_reasoning_effort_is_rejected() -> None:
    with pytest.raises(ValueError, match="reasoning_effort"):
        build_payload(MESSAGES, Action, make_config(reasoning_effort="extreme"))


def test_payload_options_override_normalized_defaults() -> None:
    config = make_config(options={"temperature": 0.9, "num_predict": 64})
    payload = build_payload(MESSAGES, Action, config)
    assert payload["options"]["temperature"] == 0.9
    assert payload["options"]["num_predict"] == 64


def test_payload_falls_back_to_json_when_schema_unsupported() -> None:
    payload = build_payload(MESSAGES, dict, make_config())
    assert payload["format"] == "json"


# ----------------------------------------------------------------------
# Provider behaviour
# ----------------------------------------------------------------------


def test_generate_returns_content_model_and_latency() -> None:
    seen: dict[str, Any] = {}

    def poster(url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
        seen.update(url=url, payload=payload, timeout=timeout)
        return {
            "model": "hauhau-qwen:latest",
            "message": {"role": "assistant", "content": '{"action": "VOTE"}'},
            "eval_count": 42,
        }

    provider = OllamaProvider(poster=poster)
    response = asyncio.run(
        provider.generate(MESSAGES, Action, make_config())
    )

    assert response.content == '{"action": "VOTE"}'
    assert response.model == "hauhau-qwen:latest"
    assert response.tokens_used == 42
    assert response.latency_ms is not None and response.latency_ms >= 0
    assert seen["url"] == "http://localhost:11434/api/chat"
    assert seen["payload"]["model"] == "hauhau-qwen:latest"
    assert seen["timeout"] == 7.0
    assert provider.calls == 1


def test_generate_respects_configured_base_url() -> None:
    seen: dict[str, Any] = {}

    def poster(url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
        seen["url"] = url
        return {"message": {"role": "assistant", "content": "{}"}}

    config = make_config(base_url="http://127.0.0.1:11434/")
    asyncio.run(OllamaProvider(poster=poster).generate(MESSAGES, Action, config))
    assert seen["url"] == "http://127.0.0.1:11434/api/chat"


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"message": "not a dict"},
        {"message": {"role": "assistant"}},
        ["not", "a", "dict"],
    ],
)
def test_generate_rejects_malformed_responses(body: Any) -> None:
    provider = OllamaProvider(poster=lambda url, payload, timeout: body)
    with pytest.raises(OllamaError):
        asyncio.run(provider.generate(MESSAGES, Action, make_config()))


def test_generate_raises_on_transport_error() -> None:
    def poster(url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
        raise OllamaError("cannot reach Ollama")

    provider = OllamaProvider(poster=poster)
    with pytest.raises(OllamaError):
        asyncio.run(provider.generate(MESSAGES, Action, make_config()))


# The default poster's own behaviour - the connection it opens, the
# headers it sends, and how it classifies failures - is covered under
# "The transport the default poster uses" below.


# ----------------------------------------------------------------------
# Config-driven model selection
# ----------------------------------------------------------------------


def test_llm_settings_build_model_config() -> None:
    settings = LLMSettings(
        provider="ollama",
        model="hauhau-qwen:latest",
        base_url="http://localhost:11434",
        temperature=0.3,
        max_tokens=128,
        timeout_seconds=30,
        options={"top_p": 0.9},
    )
    config = settings.to_model_config()
    assert config.provider == "ollama"
    assert config.name == "hauhau-qwen:latest"
    assert config.base_url == "http://localhost:11434"
    assert config.temperature == 0.3
    assert config.max_tokens == 128
    assert config.timeout_seconds == 30
    assert config.options == {"top_p": 0.9}


def test_game_config_llm_block_is_config_driven() -> None:
    # Structural only: the model details live in YAML (currently uncommitted),
    # so nothing here hardcodes a model id or effort value.
    config = load_config("configs/traitors/basic.yaml")
    model_config = config.llm.to_model_config()
    assert model_config.provider == "ollama"
    assert model_config.name == config.llm.model
    assert model_config.name  # model id comes from the YAML, not code
    assert model_config.base_url == config.llm.base_url
    assert config.llm.max_concurrency >= 1
    payload = build_payload(MESSAGES, Action, model_config)
    assert payload["model"] == config.llm.model
    assert payload["think"] is think_from_effort(config.llm.reasoning_effort)


def test_unknown_llm_field_is_rejected() -> None:
    with pytest.raises(Exception):
        LLMSettings(bogus_field=1)


# ----------------------------------------------------------------------
# Retry of transient failures (a cold model load must not kill a run)
# ----------------------------------------------------------------------

OK_BODY = {
    "model": "hauhau-qwen:latest",
    "message": {"role": "assistant", "content": '{"action": "vote"}'},
    "eval_count": 7,
}


def test_retries_a_transient_failure_then_succeeds() -> None:
    attempts: list[float] = []

    def poster(url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
        attempts.append(timeout)
        if len(attempts) < 3:
            raise OllamaError("timed out after 300.0s", retryable=True)
        return OK_BODY

    provider = OllamaProvider(poster=poster, backoff=(0.0, 0.0, 0.0))
    response = asyncio.run(
        provider.generate(MESSAGES, Action, make_config(retries=3))
    )

    assert response.content == '{"action": "vote"}'
    assert len(attempts) == 3
    assert provider.calls == 1
    assert provider.retries == 2


def test_does_not_retry_a_permanent_error() -> None:
    calls = {"n": 0}

    def poster(url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
        calls["n"] += 1
        raise OllamaError("HTTP 404 from http://localhost:11434/api/chat: no model")

    provider = OllamaProvider(poster=poster, backoff=(0.0, 0.0, 0.0))
    with pytest.raises(OllamaError, match="HTTP 404"):
        asyncio.run(provider.generate(MESSAGES, Action, make_config(retries=3)))

    assert calls["n"] == 1
    assert provider.retries == 0


def test_gives_up_after_the_retry_budget() -> None:
    calls = {"n": 0}

    def poster(url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
        calls["n"] += 1
        raise OllamaError("cannot reach Ollama", retryable=True)

    provider = OllamaProvider(poster=poster, backoff=(0.0, 0.0, 0.0))
    with pytest.raises(OllamaError, match="cannot reach Ollama"):
        asyncio.run(provider.generate(MESSAGES, Action, make_config(retries=2)))

    assert calls["n"] == 3  # the first attempt plus two retries
    assert provider.retries == 2


def test_retries_can_be_switched_off() -> None:
    calls = {"n": 0}

    def poster(url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
        calls["n"] += 1
        raise OllamaError("timed out", retryable=True)

    provider = OllamaProvider(poster=poster, backoff=(0.0,))
    with pytest.raises(OllamaError):
        asyncio.run(provider.generate(MESSAGES, Action, make_config(retries=0)))

    assert calls["n"] == 1


# ----------------------------------------------------------------------
# The transport the default poster uses
# ----------------------------------------------------------------------

CHAT_URL = "http://localhost:11434/api/chat"

# What a server that drops close-announced requests sent back instead of
# an answer. `urllib.request.urlopen` announces `Connection: close` on
# every request and cannot be told not to, which is why the poster opens
# its own connection instead.
DROPPED = http.client.RemoteDisconnected(
    "Remote end closed connection without response"
)

OK_RESPONSE = json.dumps(OK_BODY).encode("utf-8")


@pytest.fixture(autouse=True)
def _empty_connection_pool():
    """A pooled socket outlives the test that opened it, so clear it."""
    ollama_module.reset_connections()
    yield
    ollama_module.reset_connections()


class _FakeSocket:
    def __init__(self) -> None:
        self.timeout: float | None = None

    def settimeout(self, value: float) -> None:
        self.timeout = value


class _FakeResponse:
    def __init__(self, status: int = 200, body: bytes = OK_RESPONSE) -> None:
        self.status = status
        self._body = body

    def read(self) -> bytes:
        return self._body


class _FakeConnection:
    """Answers, or fails, in the order a test lists the outcomes.

    An exception is raised where a real socket raises it: at the send if
    it is next in line when `request` runs, at the read otherwise. With
    nothing listed it answers 200 and `OK_BODY`.
    """

    def __init__(self, *outcomes: Any) -> None:
        self.requests: list[dict[str, Any]] = []
        self.closed = False
        self.sock: _FakeSocket | None = None
        self._outcomes = list(outcomes)

    def request(
        self,
        method: str,
        url: str,
        body: bytes | None = None,
        headers: Any = None,
    ) -> None:
        self.requests.append(
            {"method": method, "url": url, "headers": dict(headers or {})}
        )
        if self._outcomes and isinstance(self._outcomes[0], BaseException):
            raise self._outcomes.pop(0)
        self.sock = self.sock or _FakeSocket()

    def getresponse(self) -> _FakeResponse:
        outcome = self._outcomes.pop(0) if self._outcomes else _FakeResponse()
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def die(self, error: BaseException = DROPPED) -> None:
        """The peer closes the connection while it sits in the pool."""
        self._outcomes.insert(0, error)

    def close(self) -> None:
        self.closed = True


def _serve(monkeypatch: Any, *connections: _FakeConnection) -> list[str]:
    """Feed these connections to the poster in order, counting the opens."""
    opened: list[str] = []
    remaining = list(connections)

    def factory(scheme: str, host: str, port: int, timeout: float) -> _FakeConnection:
        opened.append(f"{scheme}://{host}:{port}")
        return remaining.pop(0) if len(remaining) > 1 else remaining[0]

    monkeypatch.setattr(ollama_module, "_open_connection", factory)
    return opened


def test_the_default_poster_never_announces_connection_close(monkeypatch) -> None:
    """The header was the whole bug: a real server dropped 1-2 of 8
    requests that carried it, and 8 of 8 that did not."""
    connection = _FakeConnection()
    _serve(monkeypatch, connection)

    ollama_module._http_post(CHAT_URL, {"model": "m"}, 5.0)

    (sent,) = connection.requests
    assert "Connection" not in sent["headers"]
    assert sent["headers"]["Content-Type"] == "application/json"
    assert (sent["method"], sent["url"]) == ("POST", "/api/chat")


def test_the_default_poster_reuses_one_connection_across_calls(monkeypatch) -> None:
    """One connection carried every call in a live check; opening one per
    call is what made the drop rate fatal."""
    connection = _FakeConnection()
    opened = _serve(monkeypatch, connection)

    for _ in range(3):
        assert ollama_module._http_post(CHAT_URL, {}, 5.0) == OK_BODY

    assert opened == ["http://localhost:11434"]
    assert len(connection.requests) == 3


def test_a_pooled_connection_the_server_closed_is_replaced(monkeypatch) -> None:
    """A server closes idle connections, so the next call meets a dead
    socket. That must not reach the caller as a failed call."""
    stale, fresh = _FakeConnection(), _FakeConnection()
    opened = _serve(monkeypatch, stale, fresh)

    assert ollama_module._http_post(CHAT_URL, {}, 5.0) == OK_BODY
    stale.die()
    assert ollama_module._http_post(CHAT_URL, {}, 5.0) == OK_BODY

    assert len(opened) == 2  # the dead socket was replaced, not reused
    assert stale.closed


def test_a_fresh_connection_that_fails_is_not_resent(monkeypatch) -> None:
    """The extra attempt covers a dead pooled socket only. A call failing
    on a fresh connection must not be sent twice here: the provider owns
    retrying, and a silent second send would double the model calls."""
    connection = _FakeConnection(DROPPED)
    opened = _serve(monkeypatch, connection)

    with pytest.raises(OllamaError):
        ollama_module._http_post(CHAT_URL, {}, 5.0)

    assert len(opened) == 1
    assert len(connection.requests) == 1


def test_the_provider_defaults_to_the_keep_alive_poster() -> None:
    assert OllamaProvider()._poster is ollama_module._http_post


@pytest.mark.parametrize(
    ("status", "retryable"),
    [(500, True), (503, True), (429, True), (404, False), (400, False)],
)
def test_default_poster_marks_http_status_retryable_by_code(
    monkeypatch, status: int, retryable: bool
) -> None:
    _serve(monkeypatch, _FakeConnection(_FakeResponse(status, b"nope")))

    with pytest.raises(OllamaError, match=f"HTTP {status}") as raised:
        ollama_module._http_post(CHAT_URL, {}, 1.0)

    assert raised.value.retryable is retryable


def test_default_poster_marks_a_garbled_body_retryable(monkeypatch) -> None:
    _serve(monkeypatch, _FakeConnection(_FakeResponse(200, b"<html>")))

    with pytest.raises(OllamaError, match="non-JSON") as raised:
        ollama_module._http_post(CHAT_URL, {}, 1.0)

    assert raised.value.retryable is True


@pytest.mark.parametrize(
    "error", [TimeoutError("timed out"), ConnectionRefusedError("refused")]
)
def test_default_poster_marks_transient_failures_retryable(
    monkeypatch, error: BaseException
) -> None:
    _serve(monkeypatch, _FakeConnection(error))
    with pytest.raises(OllamaError) as raised:
        ollama_module._http_post(CHAT_URL, {}, 1.0)

    assert raised.value.retryable is True


@pytest.mark.parametrize(
    "error",
    [DROPPED, ConnectionResetError("reset by peer"), BrokenPipeError("broken pipe")],
)
def test_default_poster_marks_dropped_connections_retryable(
    monkeypatch, error: BaseException
) -> None:
    _serve(monkeypatch, _FakeConnection(error))
    with pytest.raises(OllamaError) as raised:
        ollama_module._http_post(CHAT_URL, {}, 1.0)

    assert raised.value.retryable is True


def test_provider_survives_a_dropped_connection() -> None:
    import http.client

    calls = {"n": 0}

    def poster(url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
        calls["n"] += 1
        if calls["n"] < 3:
            raise OllamaError(
                "connection to http://localhost:11434/api/chat failed: "
                "Remote end closed connection without response",
                retryable=True,
            )
        return OK_BODY

    provider = OllamaProvider(poster=poster, backoff=(0.0, 0.0, 0.0))
    response = asyncio.run(
        provider.generate(MESSAGES, Action, make_config(retries=3))
    )
    assert response.content == '{"action": "vote"}'
    assert calls["n"] == 3


def test_default_poster_marks_an_unreachable_host(monkeypatch) -> None:
    """A bare network errno (no route) is the host being gone, not a busy
    server, and the provider waits longer for it."""
    error = OSError(errno.EHOSTUNREACH, "No route to host")
    _serve(monkeypatch, _FakeConnection(error))

    with pytest.raises(OllamaError, match="No route") as raised:
        ollama_module._http_post(CHAT_URL, {}, 1.0)

    assert raised.value.retryable is True
    assert raised.value.unreachable is True


def test_default_poster_does_not_call_a_slow_server_unreachable(monkeypatch) -> None:
    _serve(monkeypatch, _FakeConnection(TimeoutError("slow")))

    with pytest.raises(OllamaError) as raised:
        ollama_module._http_post(CHAT_URL, {}, 1.0)

    assert raised.value.retryable is True
    assert raised.value.unreachable is False


def test_an_unreachable_host_backs_off_for_longer_than_a_busy_server(
    monkeypatch,
) -> None:
    """The pauses differ, so `asyncio.sleep` is intercepted rather than the
    clock measured."""
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(ollama_module.asyncio, "sleep", fake_sleep)

    calls = {"n": 0}

    def poster(url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
        calls["n"] += 1
        if calls["n"] == 1:
            raise OllamaError("busy", retryable=True)
        raise OllamaError("no route", retryable=True, unreachable=True)

    provider = OllamaProvider(
        poster=poster, backoff=(1.0, 2.0, 4.0), unreachable_backoff=(8.0, 9.0, 10.0)
    )
    with pytest.raises(OllamaError) as raised:
        asyncio.run(provider.generate(MESSAGES, Action, make_config(retries=3)))

    # The first pause is the ordinary schedule, the rest the longer one.
    assert sleeps == [1.0, 9.0, 10.0]
    assert raised.value.unreachable is True
    assert raised.value.retries == 3  # attempts - 1: every retry was spent


def test_a_response_reports_the_retries_its_own_call_spent() -> None:
    """The count rides on the response, so a concurrent call's retries
    cannot be attributed to this one."""
    calls = {"n": 0}

    def poster(url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
        calls["n"] += 1
        if calls["n"] < 3:
            raise OllamaError("timed out", retryable=True)
        return OK_BODY

    provider = OllamaProvider(poster=poster, backoff=(0.0, 0.0, 0.0))
    response = asyncio.run(provider.generate(MESSAGES, Action, make_config(retries=3)))

    assert response.retries == 2
    assert provider.retries == 2  # the run-wide counter still tracks the run

    assert provider.retries == 2
