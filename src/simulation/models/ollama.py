"""Ollama provider (spec sections 17, 18).

Talks to a local Ollama server over its HTTP API. Structured output is
requested with a JSON schema so responses parse as actions. No
game-logic code depends on this module.

Model selection and model details come from config (`ModelConfig`,
built from the YAML `llm:` block); nothing is hardcoded here.
"""

from __future__ import annotations

import asyncio
import json
import time
import urllib.error
import urllib.request
from typing import Any, Callable

from simulation.models.base import ChatMessage
from simulation.models.llm import LLMResponse, ModelConfig

# (url, payload, timeout_seconds) -> decoded JSON response body
Poster = Callable[[str, dict[str, Any], float], dict[str, Any]]


class OllamaError(RuntimeError):
    """Ollama request, transport, or response failure.

    `retryable` marks a transient failure (timeout, unreachable host,
    server-side error) that is worth another attempt; the gateway and
    the agent loop let non-retryable errors end the run.
    """

    def __init__(self, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


def _urllib_post(url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    """Blocking POST used as the default poster (run in a worker thread)."""
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise OllamaError(
            f"HTTP {exc.code} from {url}: {detail[:500]}",
            retryable=exc.code >= 500 or exc.code == 429,
        ) from exc
    except urllib.error.URLError as exc:
        raise OllamaError(
            f"cannot reach Ollama at {url}: {exc.reason}", retryable=True
        ) from exc
    except TimeoutError as exc:
        raise OllamaError(
            f"Ollama request to {url} timed out after {timeout}s", retryable=True
        ) from exc
    except OSError as exc:
        # Connection dropped or reset mid-request. A host going to
        # sleep looks exactly like this, and it used to kill a whole
        # run because it never reached the retry path.
        raise OllamaError(
            f"connection to {url} failed: {exc}", retryable=True
        ) from exc

    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        # A truncated or garbled body usually means the server hiccupped.
        raise OllamaError(
            f"non-JSON response from {url}: {body[:200]}", retryable=True
        ) from exc


# Extra attempts for retryable failures, and the pause before each
# (exponential, capped by the tuple length).
DEFAULT_RETRIES = 3
RETRY_BACKOFF = (1.0, 2.0, 4.0)


class OllamaProvider:
    """Implements the LLMProvider protocol against a local Ollama server.

    `poster` is injectable so unit tests never touch the network.
    Retryable failures (timeouts, unreachable host, 5xx/429, garbage
    bodies) are retried `config.retries` times with backoff, which is
    what keeps a slow cold load from killing a whole run.
    """

    def __init__(
        self,
        poster: Poster | None = None,
        backoff: tuple[float, ...] = RETRY_BACKOFF,
    ) -> None:
        self._poster: Poster = poster or _urllib_post
        self._backoff = backoff
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
        for attempt in range(attempts):
            try:
                data = await asyncio.to_thread(
                    self._poster, url, payload, float(config.timeout_seconds)
                )
            except OllamaError as exc:
                if not exc.retryable or attempt == attempts - 1:
                    raise
                self.retries += 1
                delay = self._backoff[min(attempt, len(self._backoff) - 1)]
                if delay:
                    await asyncio.sleep(delay)
                continue
            break
        return self._parse(data, config, started)

    def _parse(
        self, data: Any, config: ModelConfig, started: float
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
