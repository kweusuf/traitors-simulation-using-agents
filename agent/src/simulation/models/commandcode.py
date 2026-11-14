"""Command Code Provider API (OpenAI-compatible Chat Completions).

Command Code exposes the same models through an OpenAI-shaped endpoint:
`POST {base_url}/chat/completions` with a bearer token. Structured output
is requested with `response_format: {type: "json_schema"}`; if that
particular route rejects the field, the provider retries the call once
without it and remembers that the route is prompt-only for the rest of
the run. No game-logic code depends on this module.

Docs: https://commandcode.ai/docs/provider
"""

from __future__ import annotations

import asyncio
import http.client
import json
import os
import threading
import time
import urllib.parse
from typing import Any, Callable

from simulation.models.base import ChatMessage
from simulation.models.llm import LLMResponse, ModelConfig

# (url, payload, headers, timeout_seconds) -> (status, raw body)
Poster = Callable[
    [str, dict[str, Any], dict[str, str], float], tuple[int, bytes]
]

DEFAULT_API_KEY_ENV = "COMMAND_CODE_API_KEY"
# The Command Code CLI documents `CMD_API_KEY`; accept it as a fallback
# so a shell that already has that exported works without renaming.
FALLBACK_API_KEY_ENVS = ("CMD_API_KEY",)

RETRY_BACKOFF = (1.0, 2.0, 4.0)

# One live connection per thread, per origin. The call runs in a worker
# thread from `asyncio.to_thread`, and two threads must not send on one
# socket.
_KEEPALIVE = threading.local()


class CommandCodeError(RuntimeError):
    """Command Code request, transport, or response failure.

    `retryable` marks a transient failure (timeout, unreachable host,
    HTTP 429/5xx, malformed body). `retries` is how many transport
    retries this call spent before giving up.
    """

    def __init__(
        self, message: str, retryable: bool = False, truncated: bool = False
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        # The response hit the token cap rather than failing cleanly.
        # The caller can retry with a larger budget instead of giving up.
        self.truncated = truncated
        self.retries = 0


def _pool() -> dict[tuple[str, str, int], http.client.HTTPConnection]:
    connections = getattr(_KEEPALIVE, "connections", None)
    if connections is None:
        connections = {}
        _KEEPALIVE.connections = connections
    return connections


def reset_connections() -> None:
    """Close and forget this thread's pooled connections (tests)."""
    connections = getattr(_KEEPALIVE, "connections", None)
    if not connections:
        return
    for connection in connections.values():
        connection.close()
    connections.clear()


def _origin(url: str) -> tuple[str, str, int]:
    parts = urllib.parse.urlsplit(url)
    port = parts.port or (443 if parts.scheme == "https" else 80)
    return parts.scheme or "http", parts.hostname or "", port


def _open_connection(
    scheme: str, host: str, port: int, timeout: float
) -> http.client.HTTPConnection:
    factory = (
        http.client.HTTPSConnection
        if scheme == "https"
        else http.client.HTTPConnection
    )
    return factory(host, port, timeout=timeout)


def _connection_for(url: str, timeout: float) -> http.client.HTTPConnection:
    key = _origin(url)
    connection = _pool().get(key)
    if connection is None:
        connection = _open_connection(key[0], key[1], key[2], timeout)
        _pool()[key] = connection
    return connection


def _drop(url: str) -> None:
    connection = _pool().pop(_origin(url), None)
    if connection is not None:
        connection.close()


_DEAD_SOCKET = (
    http.client.RemoteDisconnected,
    http.client.BadStatusLine,
    http.client.CannotSendRequest,
    ConnectionResetError,
    BrokenPipeError,
)


def _http_post(
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    timeout: float,
) -> tuple[int, bytes]:
    """Blocking POST over a kept-alive connection (runs in a worker thread).

    Deliberately not `urllib.request.urlopen`, which always announces
    `Connection: close`. The extra attempt covers a pooled socket the
    server closed while it sat idle, where the request never reached
    anybody.
    """
    body = json.dumps(payload).encode("utf-8")
    parts = urllib.parse.urlsplit(url)
    target = parts.path or "/"
    if parts.query:
        target = f"{target}?{parts.query}"

    for retried in (False, True):
        connection = _connection_for(url, timeout)
        reused = connection.sock is not None
        try:
            if reused:
                connection.sock.settimeout(timeout)  # type: ignore[union-attr]
            connection.request(
                "POST",
                target,
                body=body,
                headers=headers,
            )
            response = connection.getresponse()
            raw = response.read()
            return response.status, raw
        except _DEAD_SOCKET as exc:
            _drop(url)
            if reused and not retried:
                continue
            raise CommandCodeError(
                f"connection to {url} failed: {exc}", retryable=True
            ) from exc
        except TimeoutError as exc:
            _drop(url)
            raise CommandCodeError(
                f"Command Code request to {url} timed out after {timeout}s",
                retryable=True,
            ) from exc
        except OSError as exc:
            _drop(url)
            raise CommandCodeError(
                f"connection to {url} failed: {exc}", retryable=True
            ) from exc

    raise CommandCodeError(f"connection to {url} failed", retryable=True)


def resolve_api_key(config: ModelConfig) -> str:
    """The key from `config.api_key_env`, then known Command Code names."""
    names: list[str] = []
    if config.api_key_env:
        names.append(config.api_key_env)
    for name in (DEFAULT_API_KEY_ENV, *FALLBACK_API_KEY_ENVS):
        if name not in names:
            names.append(name)

    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    raise ValueError(
        "the commandcode provider needs an API key: set "
        f"{config.api_key_env or DEFAULT_API_KEY_ENV} in .env "
        "(copy .env.example to .env) or export it in the shell"
    )


def build_payload(
    messages: list[ChatMessage],
    response_schema: type,
    config: ModelConfig,
) -> dict[str, Any]:
    """Map normalized config onto an OpenAI Chat Completions request body."""
    payload: dict[str, Any] = {
        "model": config.name,
        "messages": [message.model_dump() for message in messages],
        "stream": False,
        "temperature": config.temperature,
        "max_tokens": config.max_tokens,
    }

    effort = (config.reasoning_effort or "").strip().lower()
    options = dict(config.options)
    # Some routes ignore an omitted field but honour an explicit "none".
    force_effort = bool(options.pop("send_reasoning_effort", False))
    if effort and (force_effort or effort not in {"none", "off", "false", "0"}):
        payload["reasoning_effort"] = config.reasoning_effort

    structured = bool(options.pop("json_schema", True))
    if "max_completion_tokens" in options:
        # Newer OpenAI-shaped routes reject `max_tokens` in favour of this.
        payload.pop("max_tokens", None)
    if structured and hasattr(response_schema, "model_json_schema"):
        schema = response_schema.model_json_schema()
        name = str(schema.get("title") or "response")
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": name, "schema": schema, "strict": False},
        }

    payload.update(options)
    return payload


def _schema_rejected(raw: bytes) -> bool:
    detail = raw.decode("utf-8", errors="replace").lower()
    return (
        "response_format" in detail
        or "json_schema" in detail
        or "json schema" in detail
    )


def _growth_key(payload: dict[str, Any]) -> str:
    return "max_completion_tokens" if "max_completion_tokens" in payload else "max_tokens"


def _grow_token_budget(payload: dict[str, Any], ceiling: int = 32768) -> None:
    """Double the reply cap after a truncated response, up to `ceiling`."""
    key = _growth_key(payload)
    current = payload.get(key)
    if isinstance(current, int) and current > 0:
        payload[key] = min(current * 2, ceiling)


def _content_of(message: Any) -> str:
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        ]
        return "".join(parts)
    return ""


class CommandCodeProvider:
    """Implements the LLMProvider protocol against Command Code."""

    def __init__(self, poster: Poster | None = None) -> None:
        self._poster: Poster = poster or _http_post
        self._supports_json_schema = True
        # Run-level counters, matching the Ollama provider, so metrics can
        # report how many retries the provider spent across the process.
        self.calls = 0
        self.retries = 0

    @property
    def supports_json_schema(self) -> bool:
        """Whether this run's route accepted `response_format`."""
        return self._supports_json_schema

    async def generate(
        self,
        messages: list[ChatMessage],
        response_schema: type,
        config: ModelConfig,
    ) -> LLMResponse:
        key = resolve_api_key(config)
        payload = build_payload(messages, response_schema, config)
        self.calls += 1
        url = f"{config.base_url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "User-Agent": "traitors-simulation/0.1",
        }
        started = time.monotonic()
        max_retries = max(0, int(config.retries))
        spent = 0
        attempt = 0
        structured = "response_format" in payload and self._supports_json_schema

        while True:
            body = dict(payload)
            if not structured:
                body.pop("response_format", None)
            try:
                status, raw = await asyncio.to_thread(
                    self._poster, url, body, headers, float(config.timeout_seconds)
                )
            except CommandCodeError as exc:
                if not exc.retryable or attempt >= max_retries:
                    exc.retries = spent
                    raise
                spent += 1
                self.retries += 1
                attempt += 1
                await asyncio.sleep(
                    RETRY_BACKOFF[min(attempt - 1, len(RETRY_BACKOFF) - 1)]
                )
                continue

            if status == 400 and structured and _schema_rejected(raw):
                # This route cannot serve a JSON schema. Retry the same
                # call prompt-only and stop asking for the rest of the run.
                self._supports_json_schema = False
                structured = False
                continue

            if status >= 400:
                detail = raw.decode("utf-8", errors="replace")
                error = CommandCodeError(
                    f"HTTP {status} from {url}: {detail[:500]}",
                    retryable=status >= 500 or status == 429,
                )
                if not error.retryable or attempt >= max_retries:
                    error.retries = spent
                    raise error
                spent += 1
                self.retries += 1
                attempt += 1
                await asyncio.sleep(
                    RETRY_BACKOFF[min(attempt - 1, len(RETRY_BACKOFF) - 1)]
                )
                continue

            try:
                data = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                error = CommandCodeError(
                    f"non-JSON response from {url}: {raw[:200]!r}",
                    retryable=True,
                )
                if attempt >= max_retries:
                    error.retries = spent
                    raise error from exc
                spent += 1
                self.retries += 1
                attempt += 1
                await asyncio.sleep(
                    RETRY_BACKOFF[min(attempt - 1, len(RETRY_BACKOFF) - 1)]
                )
                continue

            try:
                return self._parse(data, config, started, retries=spent)
            except CommandCodeError as exc:
                if not exc.retryable or attempt >= max_retries:
                    exc.retries = spent
                    raise
                if exc.truncated:
                    _grow_token_budget(payload)
                spent += 1
                self.retries += 1
                attempt += 1
                await asyncio.sleep(
                    RETRY_BACKOFF[min(attempt - 1, len(RETRY_BACKOFF) - 1)]
                )

    def _parse(
        self, data: Any, config: ModelConfig, started: float, retries: int = 0
    ) -> LLMResponse:
        if not isinstance(data, dict):
            raise CommandCodeError(
                f"malformed Command Code response: {str(data)[:200]}",
                retryable=True,
            )
        if data.get("error"):
            raise CommandCodeError(
                f"Command Code error: {data['error']}", retryable=False
            )
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise CommandCodeError(
                f"Command Code response has no choices: {str(data)[:200]}",
                retryable=True,
            )
        choice = choices[0] if isinstance(choices[0], dict) else {}
        finish_reason = choice.get("finish_reason")
        message = choice.get("message")
        content = _content_of(message)
        if finish_reason == "length":
            # A cut-off reply is not an answer, even if some content came
            # back; the runtime would only reject the truncated JSON. Retry
            # with a larger cap instead.
            raise CommandCodeError(
                "Command Code reply hit the token cap "
                f"(finish_reason=length, {len(content)} chars)",
                retryable=True,
                truncated=True,
            )
        if not content:
            raise CommandCodeError(
                "Command Code response has no message content: "
                f"{str(data)[:200]}",
                retryable=True,
            )
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        completion = usage.get("completion_tokens")
        prompt = usage.get("prompt_tokens")
        return LLMResponse(
            content=content,
            model=str(data.get("model", config.name)),
            latency_ms=(time.monotonic() - started) * 1000.0,
            tokens_used=int(completion) if isinstance(completion, int) else None,
            input_tokens=int(prompt) if isinstance(prompt, int) else None,
            retries=retries,
        )
