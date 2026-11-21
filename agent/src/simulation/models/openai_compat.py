"""OpenAI-compatible provider (Chat Completions).

Talks to any OpenAI-shaped endpoint via `POST {base_url}/chat/completions`
with a bearer token. Structured output is requested with
`response_format: {type: "json_schema"}`; if that particular route rejects
the field, the provider retries the call once without it and remembers that
the route is prompt-only for the rest of the run. No game-logic code
depends on this module: the endpoint, model, and key variable all come from
the config.
"""

from __future__ import annotations

import asyncio
import http.client
import json
import os
import re
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

DEFAULT_API_KEY_ENV = "OPENAI_COMPAT_API_KEY"

RETRY_BACKOFF = (1.0, 2.0, 4.0)

# Plain HTTP is acceptable only for a local model proxy. Anywhere else a
# bearer token would be readable on the wire.
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})

_SECRET_IN_TEXT = re.compile(
    r"(?i)(?:bearer\s+|sk-|sk_live_|sk_test_|ghp_|gho_|ghs_|github_pat_|"
    r"glpat_|glpat-|xox[baprs]-|xoxc-|xoxp-|AKIA|AIza|oc_|npm_|pypi-)"
    r"[A-Za-z0-9_.\-]{6,}"
)
_URL_USERINFO = re.compile(r"(?i)(https?://)[^/@\s]+@")

# One live connection per thread, per origin. The call runs in a worker
# thread from `asyncio.to_thread`, and two threads must not send on one
# socket.
_KEEPALIVE = threading.local()


class OpenAICompatError(RuntimeError):
    """OpenAI-compatible request, transport, or response failure.

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
            raise OpenAICompatError(
                f"connection to {_redact(url)} failed: {_redact(str(exc))}",
                retryable=True,
            ) from exc
        except TimeoutError as exc:
            _drop(url)
            raise OpenAICompatError(
                f"OpenAI-compatible request to {_redact(url)} timed out after "
                f"{timeout}s",
                retryable=True,
            ) from exc
        except OSError as exc:
            _drop(url)
            raise OpenAICompatError(
                f"connection to {_redact(url)} failed: {_redact(str(exc))}",
                retryable=True,
            ) from exc

    raise OpenAICompatError(
        f"connection to {_redact(url)} failed", retryable=True
    )


def _redact(text: str) -> str:
    """Remove credential shapes and URL userinfo from text that may be logged."""
    text = _URL_USERINFO.sub(r"\1[redacted]@", text)
    return _SECRET_IN_TEXT.sub("[redacted]", text)


def _require_secure_transport(url: str, config: ModelConfig) -> None:
    """Refuse to put a bearer token on a plaintext non-loopback connection."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme == "https":
        return
    if parts.scheme == "http" and (parts.hostname or "").lower() in _LOOPBACK_HOSTS:
        return
    if config.options.get("allow_insecure_http") is True:
        return
    raise OpenAICompatError(
        "refusing to send an API key over "
        f"{parts.scheme or 'an unknown scheme'}; use https, a loopback "
        "address, or set llm.options.allow_insecure_http for a trusted local proxy"
    )


def resolve_api_key(config: ModelConfig) -> str:
    """The key named by `config.api_key_env`, defaulting to our own variable.

    Only the configured variable is read. A config that names another
    provider's key variable therefore never falls back to this provider's
    default, so its key cannot be sent to the wrong host.
    """
    name = (config.api_key_env or "").strip() or DEFAULT_API_KEY_ENV
    value = os.environ.get(name, "").strip()
    if value:
        return value
    raise ValueError(
        "the openai_compat provider needs an API key: set "
        f"{name} in .env (copy .env.example to .env) or export it in the shell"
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

    # A local transport permission, not a request field. Some OpenAI-shaped
    # routes reject unknown body arguments, so it must not reach the wire.
    options.pop("allow_insecure_http", None)

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


def _grow_token_budget(payload: dict[str, Any], ceiling: int = 131072) -> None:
    """Double the reply cap after a truncated response, up to `ceiling`."""
    key = _growth_key(payload)
    current = payload.get(key)
    if isinstance(current, int) and current > 0:
        payload[key] = min(current * 2, ceiling)


_HARMONY_TOKEN = re.compile(r"<\|[^|>]*\|>")
_CHANNEL_BLOCK = re.compile(
    r"<\|channel\|>\s*(?P<channel>[a-zA-Z_]+)\s*<\|message\|>"
    r"(?P<body>.*?)(?=<\|end\|>|<\|start\|>|\Z)",
    re.DOTALL,
)


def _first_json_object(text: str) -> str | None:
    """The first balanced `{...}` object in `text`, or None."""
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def _sanitize_content(text: str) -> str:
    """Remove chat-template scaffolding that some routes leak into content.

    A model occasionally emits its Harmony-style channel markers as literal
    text, so a final-channel reply arrives followed by a second analysis
    segment. When a final channel is present, its bodies are the reply;
    otherwise the markers are stripped and, if the leftover tail makes the
    text unparseable, the first balanced JSON object is kept. Both keep the
    visible JSON intact, whereas the raw markers poison message text and
    stored transcripts.
    """
    if "<|" not in text:
        return text
    finals = [
        match.group("body")
        for match in _CHANNEL_BLOCK.finditer(text)
        if match.group("channel").lower() == "final"
    ]
    if finals and any(part.strip() for part in finals):
        return "".join(finals).strip()
    cleaned = _HARMONY_TOKEN.sub("", text).strip()
    try:
        json.loads(cleaned)
    except json.JSONDecodeError:
        candidate = _first_json_object(cleaned)
        if candidate is not None:
            return candidate
    return cleaned


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


class OpenAICompatProvider:
    """Implements the LLMProvider protocol against an OpenAI-compatible endpoint."""

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
        payload = build_payload(messages, response_schema, config)
        self.calls += 1
        url = f"{config.base_url.rstrip('/')}/chat/completions"
        _require_secure_transport(url, config)
        key = resolve_api_key(config)
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "User-Agent": "traitors-simulation/0.1",
            **config.extra_headers,
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
            except OpenAICompatError as exc:
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
                error = OpenAICompatError(
                    f"HTTP {status} from {_redact(url)}: {_redact(detail[:500])}",
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
                error = OpenAICompatError(
                    f"non-JSON response from {_redact(url)}: "
                    f"{_redact(repr(raw[:200]))}",
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
            except OpenAICompatError as exc:
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
            raise OpenAICompatError(
                f"malformed OpenAI-compatible response: {_redact(str(data)[:200])}",
                retryable=True,
            )
        if data.get("error"):
            raise OpenAICompatError(
                f"OpenAI-compatible error: {_redact(str(data['error']))}",
                retryable=False,
            )
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise OpenAICompatError(
                f"OpenAI-compatible response has no choices: "
                f"{_redact(str(data)[:200])}",
                retryable=True,
            )
        choice = choices[0] if isinstance(choices[0], dict) else {}
        finish_reason = choice.get("finish_reason")
        message = choice.get("message")
        content = _sanitize_content(_content_of(message))
        if finish_reason == "length":
            # A cut-off reply is not an answer, even if some content came
            # back; the runtime would only reject the truncated JSON. Retry
            # with a larger cap instead.
            raise OpenAICompatError(
                "OpenAI-compatible reply hit the token cap "
                f"(finish_reason=length, {len(content)} chars)",
                retryable=True,
                truncated=True,
            )
        if not content:
            raise OpenAICompatError(
                "OpenAI-compatible response has no message content: "
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
