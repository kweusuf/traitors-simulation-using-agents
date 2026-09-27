"""Unit tests for the Ollama provider (spec sections 17-19, 22).

Every test injects a fake poster, so no Ollama server is required.
"""

from __future__ import annotations

import asyncio
import urllib.error
from typing import Any

import pytest

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


def test_default_poster_maps_http_and_url_errors() -> None:
    def failing_open(url: str, *args: Any, **kwargs: Any):
        raise urllib.error.URLError("connection refused")

    provider = OllamaProvider()
    # Patch the module-level urlopen rather than hitting the network.
    import simulation.models.ollama as ollama_module

    original = ollama_module.urllib.request.urlopen
    ollama_module.urllib.request.urlopen = failing_open  # type: ignore[assignment]
    try:
        with pytest.raises(OllamaError, match="cannot reach Ollama"):
            provider._poster("http://localhost:11434/api/chat", {}, 1.0)
    finally:
        ollama_module.urllib.request.urlopen = original  # type: ignore[assignment]


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


def test_default_poster_marks_transient_failures_retryable() -> None:
    import simulation.models.ollama as ollama_module

    original = ollama_module.urllib.request.urlopen

    def fail_with(error: BaseException):
        def opener(url: str, *args: Any, **kwargs: Any):
            raise error

        return opener

    cases = [
        (TimeoutError("timed out"), True),
        (urllib.error.URLError("connection refused"), True),
        (
            urllib.error.HTTPError(
                "http://localhost:11434/api/chat", 503, "unavailable", {}, None
            ),
            True,
        ),
        (
            urllib.error.HTTPError(
                "http://localhost:11434/api/chat", 404, "not found", {}, None
            ),
            False,
        ),
    ]
    try:
        for error, expected in cases:
            ollama_module.urllib.request.urlopen = fail_with(error)  # type: ignore[assignment]
            try:
                ollama_module._urllib_post("http://localhost:11434/api/chat", {}, 1.0)
            except OllamaError as exc:
                assert exc.retryable is expected, (error, exc.retryable)
            else:
                raise AssertionError(f"{error!r} did not raise")
    finally:
        ollama_module.urllib.request.urlopen = original  # type: ignore[assignment]
