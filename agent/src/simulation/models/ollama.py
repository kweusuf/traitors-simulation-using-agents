"""Ollama provider (spec sections 17, 18).

Talks to a local Ollama server over its HTTP API. Structured output is
requested with a JSON schema so responses parse as actions. No
game-logic code depends on this module.

Model selection and model details come from config (`ModelConfig`,
built from the YAML `llm:` block); nothing is hardcoded here.

One transport decision lives here rather than in config. The POST runs
over a kept-alive `http.client` connection, never
`urllib.request.urlopen`: `urlopen` announces `Connection: close` on
every request, and a server that answers a close-announced request by
dropping the connection turns most calls into `RemoteDisconnected`.
Measured against one such host, 1-2 of 8 requests succeeded with that
header and 8 of 8 without it, the same moment, no load. The provider's
retry loop is not compensation for that - it spends its budget and ends
the run.
"""

from __future__ import annotations

import asyncio
import errno
import http.client
import json
import threading
import time
import urllib.parse
from typing import Any, Callable

from simulation.models.base import ChatMessage
from simulation.models.llm import LLMResponse, ModelConfig

# (url, payload, timeout_seconds) -> decoded JSON response body
Poster = Callable[[str, dict[str, Any], float], dict[str, Any]]

# One live connection per thread, per origin. See `_pool` for why.
_KEEPALIVE = threading.local()


class OllamaError(RuntimeError):
    """Ollama request, transport, or response failure.

    `retryable` marks a transient failure (timeout, unreachable host,
    server-side error) that is worth another attempt; the gateway and
    the agent loop let non-retryable errors end the run.

    `unreachable` narrows that to the case where the *host* could not be
    reached at all - no route, network down - which is worth backing off
    for longer than a slow or busy server is, because the wait there is
    bounded by the network coming back rather than by the model.

    `retries` is how many transport retries this one call spent, filled
    in by the provider when it finally gives up, so a failure is
    attributed to the call that caused it and not to a shared counter a
    concurrent call may also have moved.
    """

    def __init__(
        self, message: str, retryable: bool = False, unreachable: bool = False
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.unreachable = unreachable
        self.retries = 0


def _pool() -> dict[tuple[str, str, int], http.client.HTTPConnection]:
    """This thread's live connections, keyed by origin.

    A connection belongs to the thread that made it: each call runs on a
    worker thread from `asyncio.to_thread`, and two threads must not send
    on one socket. Nothing here is shared between threads.
    """
    connections = getattr(_KEEPALIVE, "connections", None)
    if connections is None:
        connections = {}
        _KEEPALIVE.connections = connections
    return connections


def reset_connections() -> None:
    """Close and forget this thread's pooled connections.

    A long-lived process holds sockets the server may have closed anyway;
    this exists so tests start from an empty pool, and so a caller that
    wants the sockets gone has a way to say so.
    """
    connections = getattr(_KEEPALIVE, "connections", None)
    if not connections:
        return
    for connection in connections.values():
        connection.close()
    connections.clear()


def _open_connection(
    scheme: str, host: str, port: int, timeout: float
) -> http.client.HTTPConnection:
    """Factory for a new connection, injectable so tests open no socket."""
    factory = (
        http.client.HTTPSConnection if scheme == "https" else http.client.HTTPConnection
    )
    return factory(host, port, timeout=timeout)


def _origin(url: str) -> tuple[str, str, int]:
    parts = urllib.parse.urlsplit(url)
    port = parts.port or (443 if parts.scheme == "https" else 80)
    return parts.scheme or "http", parts.hostname or "", port


def _drop(url: str) -> None:
    """Forget the pooled connection for `url`, if any."""
    connection = _pool().pop(_origin(url), None)
    if connection is not None:
        connection.close()


def _connection_for(url: str, timeout: float) -> http.client.HTTPConnection:
    """The pooled connection for `url`, opening one if the pool has none."""
    key = _origin(url)
    connection = _pool().get(key)
    if connection is None:
        connection = _open_connection(key[0], key[1], key[2], timeout)
        _pool()[key] = connection
    return connection


# Failures that mean the socket is gone rather than the server answered
# badly. `RemoteDisconnected` also covers `ConnectionResetError`, and a
# pooled socket the peer closed surfaces at either end of the exchange.
_DEAD_SOCKET = (
    http.client.RemoteDisconnected,
    http.client.BadStatusLine,
    http.client.CannotSendRequest,
    ConnectionResetError,
    BrokenPipeError,
)

# OS errors that mean the *host* is unreachable rather than the model
# being slow or busy: no route, host down, network down, nothing
# listening yet. These are the ones worth a longer backoff, because what
# they are waiting on is the network, not a response. Built from names so
# a platform missing one does not fail to import.
_UNREACHABLE_ERRNOS = frozenset(
    value
    for name in (
        "ENETDOWN",
        "ENETUNREACH",
        "EHOSTDOWN",
        "EHOSTUNREACH",
        "ECONNREFUSED",
        "ETIMEDOUT",
    )
    if (value := getattr(errno, name, None)) is not None
)


def _http_post(url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    """Blocking POST over a kept-alive connection (runs in a worker thread).

    Deliberately not `urllib.request.urlopen`, which always sends
    `Connection: close`. The extra attempt below is not a retry of the
    call - it covers a pooled socket the server closed while it sat idle,
    where the request never reached anybody and the provider's own retry
    budget would otherwise be spent on a dead socket.
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
                headers={"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            raw = response.read()
        except _DEAD_SOCKET as exc:
            _drop(url)
            if reused and not retried:
                continue
            raise OllamaError(
                f"connection to {url} failed: {exc}", retryable=True
            ) from exc
        except TimeoutError as exc:
            _drop(url)
            raise OllamaError(
                f"Ollama request to {url} timed out after {timeout}s",
                retryable=True,
            ) from exc
        except OSError as exc:
            # A host going to sleep looks exactly like this, and it used
            # to kill a whole run because it never reached the retry path.
            # A network-layer errno (no route, host down, network
            # unreachable) means the host itself is gone, which the
            # provider backs off for longer than a busy server.
            _drop(url)
            raise OllamaError(
                f"connection to {url} failed: {exc}",
                retryable=True,
                unreachable=exc.errno in _UNREACHABLE_ERRNOS,
            ) from exc

        if response.status >= 400:
            detail = raw.decode("utf-8", errors="replace")
            raise OllamaError(
                f"HTTP {response.status} from {url}: {detail[:500]}",
                retryable=response.status >= 500 or response.status == 429,
            )
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            # A truncated or garbled body usually means the server hiccupped.
            raise OllamaError(
                f"non-JSON response from {url}: {raw[:200]!r}", retryable=True
            ) from exc

    # Unreachable: the loop either returns, continues once, or raises.
    raise OllamaError(f"connection to {url} failed", retryable=True)


# Extra attempts for retryable failures, and the pause before each
# (exponential, capped by the tuple length).
DEFAULT_RETRIES = 3
RETRY_BACKOFF = (1.0, 2.0, 4.0)

# A host that is unreachable - as opposed to a server that answered badly
# or slowly - is worth waiting on for longer, because what it is waiting
# for is the network coming back rather than a response. Still capped, and
# still only `config.retries` attempts: a host that stays down past this
# is resumed from its log by the supervisor, not waited on forever inside
# one process.
UNREACHABLE_BACKOFF = (5.0, 15.0, 45.0)


class OllamaProvider:
    """Implements the LLMProvider protocol against a local Ollama server.

    `poster` is injectable so unit tests never touch the network.
    Retryable failures (timeouts, 5xx/429, garbage bodies) are retried
    `config.retries` times with `backoff`, which is what keeps a slow
    cold load from killing a whole run. A failure that marks the *host*
    unreachable is retried with `unreachable_backoff` instead: same
    attempt budget, longer pauses, because a bare network outage is
    ridden out over tens of seconds rather than a server hiccup over a
    few.
    """

    def __init__(
        self,
        poster: Poster | None = None,
        backoff: tuple[float, ...] = RETRY_BACKOFF,
        unreachable_backoff: tuple[float, ...] = UNREACHABLE_BACKOFF,
    ) -> None:
        self._poster: Poster = poster or _http_post
        self._backoff = backoff
        self._unreachable_backoff = unreachable_backoff
        self.calls: int = 0
        self.retries: int = 0

    async def generate(
        self,
        messages: list[ChatMessage],
        response_schema: type,
        config: ModelConfig,
    ) -> LLMResponse:
        payload = build_payload(messages, response_schema, config)
        url = f"{config.base_url.rstrip('/')}/api/chat"
        self.calls += 1
        started = time.monotonic()
        attempts = 1 + max(0, int(config.retries))
        spent = 0
        for attempt in range(attempts):
            try:
                data = await asyncio.to_thread(
                    self._poster, url, payload, float(config.timeout_seconds)
                )
            except OllamaError as exc:
                if not exc.retryable or attempt == attempts - 1:
                    # How many retries this call spent is worth recording
                    # even when it finally failed: the run's telemetry
                    # counts them, and the effort belongs to this call.
                    exc.retries = spent
                    raise
                spent += 1
                self.retries += 1
                schedule = (
                    self._unreachable_backoff if exc.unreachable else self._backoff
                )
                delay = schedule[min(attempt, len(schedule) - 1)]
                if delay:
                    await asyncio.sleep(delay)
                continue
            break
        return self._parse(data, config, started, retries=spent)

    def _parse(
        self, data: Any, config: ModelConfig, started: float, retries: int = 0
    ) -> LLMResponse:
        if not isinstance(data, dict):
            raise OllamaError(f"malformed Ollama response: {str(data)[:200]}")
        message = data.get("message")
        if not isinstance(message, dict) or not isinstance(message.get("content"), str):
            raise OllamaError(f"Ollama response has no message content: {str(data)[:200]}")
        if data.get("error"):
            raise OllamaError(f"Ollama error: {data['error']}")
        tokens = data.get("eval_count")
        prompt_tokens = data.get("prompt_eval_count")
        return LLMResponse(
            content=message["content"],
            model=str(data.get("model", config.name)),
            latency_ms=(time.monotonic() - started) * 1000.0,
            tokens_used=int(tokens) if isinstance(tokens, int) else None,
            input_tokens=(
                int(prompt_tokens) if isinstance(prompt_tokens, int) else None
            ),
            retries=retries,
        )


def build_payload(
    messages: list[ChatMessage],
    response_schema: type,
    config: ModelConfig,
) -> dict[str, Any]:
    """Map normalized config onto Ollama's /api/chat request body.

    `config.options` carries provider-specific knobs (spec section 19)
    and overrides the normalized defaults. `reasoning_effort` becomes
    Ollama's `think` flag (hybrid reasoning models such as Qwen).
    """
    options: dict[str, Any] = {
        "temperature": config.temperature,
        "num_predict": config.max_tokens,
        **config.options,
    }
    if hasattr(response_schema, "model_json_schema"):
        schema: Any = response_schema.model_json_schema()
    else:
        schema = "json"
    return {
        "model": config.name,
        "messages": [message.model_dump() for message in messages],
        "stream": False,
        "format": schema,
        "think": think_from_effort(config.reasoning_effort),
        "options": options,
    }


_THINK_OFF = frozenset({"none", "off", "disabled", "false", "0"})
_THINK_ON = frozenset({"low", "medium", "high", "true", "full"})


def think_from_effort(reasoning_effort: str) -> bool:
    """Normalize `reasoning_effort` onto Ollama's boolean `think` flag.

    Thinking tokens count against `max_tokens`, so an effort of `none`
    keeps structured output inside the budget.
    """
    key = reasoning_effort.strip().lower()
    if key in _THINK_OFF:
        return False
    if key in _THINK_ON:
        return True
    accepted = ", ".join(sorted(_THINK_ON | _THINK_OFF))
    raise ValueError(f"unsupported reasoning_effort {reasoning_effort!r}; use {accepted}")
