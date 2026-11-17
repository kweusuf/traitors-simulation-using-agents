"""Unit tests for the Command Code Provider API (OpenAI-compatible).

Every test injects a fake poster, so no network call is made.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

import simulation.models.commandcode as cc
from simulation.actions.actions import Action
from simulation.models.base import ChatMessage
from simulation.models.llm import ModelConfig

MESSAGES = [ChatMessage(role="user", content="pick an action")]

OK = {
    "model": "xiaomi/mimo-v2.6-flash",
    "choices": [
        {
            "message": {
                "role": "assistant",
                "content": '{"action": "public_message", "content": "hello"}',
            },
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 120, "completion_tokens": 12},
}


def make_config(**overrides: Any) -> ModelConfig:
    base: dict[str, Any] = {
        "provider": "commandcode",
        "name": "xiaomi/mimo-v2.6-flash",
        "base_url": "https://api.commandcode.ai/provider/v1",
        "temperature": 0.2,
        "max_tokens": 256,
        "timeout_seconds": 7,
        "retries": 0,
        "reasoning_effort": "none",
    }
    base.update(overrides)
    return ModelConfig(**base)


def reply(data: Any, status: int = 200) -> tuple[int, bytes]:
    return status, json.dumps(data).encode("utf-8")


def run(
    provider: cc.CommandCodeProvider,
    config: ModelConfig,
    schema: type = Action,
) -> cc.LLMResponse:
    return asyncio.run(provider.generate(MESSAGES, schema, config))


# ----------------------------------------------------------------------
# Payload construction
# ----------------------------------------------------------------------


def test_payload_carries_model_and_schema() -> None:
    payload = cc.build_payload(MESSAGES, Action, make_config())

    assert payload["model"] == "xiaomi/mimo-v2.6-flash"
    assert payload["stream"] is False
    assert payload["messages"] == [{"role": "user", "content": "pick an action"}]
    assert payload["temperature"] == 0.2
    assert payload["max_tokens"] == 256
    assert payload["response_format"]["type"] == "json_schema"
    assert payload["response_format"]["json_schema"]["schema"]["title"] == "Action"
    assert "reasoning_effort" not in payload


def test_payload_carries_reasoning_effort_when_asked() -> None:
    payload = cc.build_payload(
        MESSAGES, Action, make_config(reasoning_effort="medium")
    )
    assert payload["reasoning_effort"] == "medium"


def test_json_schema_can_be_disabled() -> None:
    payload = cc.build_payload(
        MESSAGES, Action, make_config(options={"json_schema": False})
    )
    assert "response_format" not in payload


def test_max_completion_tokens_replaces_max_tokens() -> None:
    payload = cc.build_payload(
        MESSAGES, Action, make_config(options={"max_completion_tokens": 64})
    )
    assert "max_tokens" not in payload
    assert payload["max_completion_tokens"] == 64


# ----------------------------------------------------------------------
# Auth
# ----------------------------------------------------------------------


def test_api_key_comes_from_configured_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MY_CMD_KEY", "secret-from-env")
    seen: dict[str, Any] = {}

    def poster(url, payload, headers, timeout):
        seen["url"] = url
        seen["headers"] = headers
        return reply(OK)

    run(
        cc.CommandCodeProvider(poster=poster),
        make_config(api_key_env="MY_CMD_KEY"),
    )

    assert seen["url"] == (
        "https://api.commandcode.ai/provider/v1/chat/completions"
    )
    assert seen["headers"]["Authorization"] == "Bearer secret-from-env"


def test_configured_env_wins_over_the_default_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COMMAND_CODE_API_KEY", "default-key")
    monkeypatch.setenv("MY_CMD_KEY", "configured-key")
    seen: dict[str, Any] = {}

    def poster(url, payload, headers, timeout):
        seen["headers"] = headers
        return reply(OK)

    run(
        cc.CommandCodeProvider(poster=poster),
        make_config(api_key_env="MY_CMD_KEY"),
    )

    assert seen["headers"]["Authorization"] == "Bearer configured-key"


def test_extra_headers_reach_the_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MY_CMD_KEY", "secret-from-env")
    seen: dict[str, Any] = {}

    def poster(url, payload, headers, timeout):
        seen["headers"] = headers
        return reply(OK)

    run(
        cc.CommandCodeProvider(poster=poster),
        make_config(
            api_key_env="MY_CMD_KEY",
            extra_headers={"x-session": "stable-session-id"},
        ),
    )

    assert seen["headers"]["x-session"] == "stable-session-id"
    assert seen["headers"]["Authorization"] == "Bearer secret-from-env"


def test_missing_api_key_is_a_clear_config_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("COMMAND_CODE_API_KEY", "CMD_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    provider = cc.CommandCodeProvider(poster=lambda *a: reply(OK))

    with pytest.raises(ValueError, match="needs an API key"):
        run(provider, make_config())


# ----------------------------------------------------------------------
# Parsing and retries
# ----------------------------------------------------------------------


def test_parses_content_and_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMMAND_CODE_API_KEY", "k")
    response = run(cc.CommandCodeProvider(poster=lambda *a: reply(OK)), make_config())

    assert json.loads(response.content)["content"] == "hello"
    assert response.model == "xiaomi/mimo-v2.6-flash"
    assert response.tokens_used == 12
    assert response.input_tokens == 120


def test_leaked_channel_scaffolding_is_stripped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COMMAND_CODE_API_KEY", "k")
    leaked = (
        '{"action": "public_message", "content": "hello"}'
        "<|end|><|start|>assistant<|channel|>analysis<|message|>"
        "We need output valid JSON only."
    )
    data = json.loads(json.dumps(OK))
    data["choices"][0]["message"]["content"] = leaked

    response = run(
        cc.CommandCodeProvider(poster=lambda *a: reply(data)), make_config()
    )

    assert response.content == '{"action": "public_message", "content": "hello"}'
    assert json.loads(response.content)["content"] == "hello"


def test_final_channel_wins_over_leaked_analysis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COMMAND_CODE_API_KEY", "k")
    leaked = (
        "<|start|>assistant<|channel|>analysis<|message|>"
        "thinking out loud<|end|>"
        "<|start|>assistant<|channel|>final<|message|>"
        '{"action": "public_message", "content": "hello"}'
    )
    data = json.loads(json.dumps(OK))
    data["choices"][0]["message"]["content"] = leaked

    response = run(
        cc.CommandCodeProvider(poster=lambda *a: reply(data)), make_config()
    )

    assert json.loads(response.content)["content"] == "hello"


def test_retries_transient_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMMAND_CODE_API_KEY", "k")
    monkeypatch.setattr(cc, "RETRY_BACKOFF", (0.0, 0.0, 0.0))
    statuses = [500, 429, 200]
    calls: list[dict[str, Any]] = []

    def poster(url, payload, headers, timeout):
        calls.append(payload)
        return reply(OK if statuses[len(calls) - 1] == 200 else {"error": "x"},
                     statuses[len(calls) - 1])

    response = run(
        cc.CommandCodeProvider(poster=poster), make_config(retries=3)
    )

    assert len(calls) == 3
    assert response.retries == 2


def test_provider_counts_retries_for_metrics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COMMAND_CODE_API_KEY", "k")
    monkeypatch.setattr(cc, "RETRY_BACKOFF", (0.0, 0.0, 0.0))
    statuses = [500, 200]

    def poster(url, payload, headers, timeout):
        status = statuses.pop(0)
        return reply(OK if status == 200 else {"error": "x"}, status)

    provider = cc.CommandCodeProvider(poster=poster)
    run(provider, make_config(retries=1))

    assert provider.calls == 1
    assert provider.retries == 1


def test_does_not_retry_auth_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMMAND_CODE_API_KEY", "k")
    calls: list[int] = []

    def poster(url, payload, headers, timeout):
        calls.append(1)
        return 401, b'{"error": "bad key"}'

    with pytest.raises(cc.CommandCodeError, match="HTTP 401"):
        run(cc.CommandCodeProvider(poster=poster), make_config(retries=3))
    assert len(calls) == 1


def test_retries_a_malformed_body(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMMAND_CODE_API_KEY", "k")
    monkeypatch.setattr(cc, "RETRY_BACKOFF", (0.0, 0.0, 0.0))
    bodies = [b"not json", json.dumps(OK).encode("utf-8")]

    def poster(url, payload, headers, timeout):
        return 200, bodies.pop(0)

    response = run(
        cc.CommandCodeProvider(poster=poster), make_config(retries=1)
    )
    assert json.loads(response.content)["content"] == "hello"


def test_truncated_reply_retries_with_a_larger_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COMMAND_CODE_API_KEY", "k")
    monkeypatch.setattr(cc, "RETRY_BACKOFF", (0.0, 0.0, 0.0))
    payloads: list[dict[str, Any]] = []
    replies = [
        {
            "choices": [
                {
                    "message": {"content": '{"action": "public_message", "con'},
                    "finish_reason": "length",
                }
            ]
        },
        OK,
    ]

    def poster(url, payload, headers, timeout):
        payloads.append(payload)
        return reply(replies.pop(0))

    response = run(
        cc.CommandCodeProvider(poster=poster), make_config(retries=1)
    )

    assert json.loads(response.content)["content"] == "hello"
    assert payloads[0]["max_tokens"] == 256
    assert payloads[1]["max_tokens"] == 512


# ----------------------------------------------------------------------
# Structured-output fallback
# ----------------------------------------------------------------------


def test_rejected_json_schema_retries_prompt_only_and_latches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COMMAND_CODE_API_KEY", "k")
    seen: list[dict[str, Any]] = []

    def poster(url, payload, headers, timeout):
        seen.append(payload)
        if "response_format" in payload:
            return 400, b'{"error": "response_format is not supported"}'
        return reply(OK)

    provider = cc.CommandCodeProvider(poster=poster)
    config = make_config()

    first = run(provider, config)
    second = run(provider, config)

    assert json.loads(first.content)["content"] == "hello"
    assert json.loads(second.content)["content"] == "hello"
    assert "response_format" in seen[0]
    assert "response_format" not in seen[1]
    # The capability is remembered: the second call never probes again.
    assert "response_format" not in seen[2]
    assert len(seen) == 3
