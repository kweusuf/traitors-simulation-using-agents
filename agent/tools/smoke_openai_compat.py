"""One real OpenAI-compatible call: auth, model id, and JSON output.

A full game is an expensive way to discover a typo in the key or that the
route rejects `response_format`. This sends one action-shaped prompt
through the same provider a run uses and reports what came back.

Usage:
  uv run --no-sync python tools/smoke_openai_compat.py
  uv run --no-sync python tools/smoke_openai_compat.py --model deepseek-v4.1-flash

Reads OPENAI_COMPAT_API_KEY from the gitignored .env.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from simulation.actions.actions import ActionType, action_schema  # noqa: E402
from simulation.env import load_dotenv  # noqa: E402
from simulation.models.base import ChatMessage  # noqa: E402
from simulation.models.openai_compat import (  # noqa: E402
    OpenAICompatError,
    OpenAICompatProvider,
)
from simulation.models.llm import ModelConfig  # noqa: E402

DEFAULT_MODEL = "deepseek-v4.1-flash"

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--key-env", default="OPENAI_COMPAT_API_KEY")
    parser.add_argument(
        "--reasoning-effort",
        default="none",
        help="reasoning_effort to send; 'none' omits the field",
    )
    args = parser.parse_args()

    load_dotenv()
    base_url = args.base_url or os.environ.get("OPENAI_COMPAT_BASE_URL")
    if not base_url:
        parser.error("set OPENAI_COMPAT_BASE_URL or pass --base-url")

    schema = action_schema(ActionType.PUBLIC_MESSAGE)
    messages = [
        ChatMessage(
            role="system",
            content=(
                "You are a player in a social deduction game. "
                "Reply with JSON only, no other text."
            ),
        ),
        ChatMessage(
            role="user",
            content=(
                'Say one short public message to the table. Respond with '
                'JSON only, e.g. {"action": "public_message", '
                '"target": "?", "content": "I am watching the quiet ones.", '
                '"confidence": 0.5}. Include every field.'
            ),
        ),
    ]
    config = ModelConfig(
        provider="openai_compat",
        name=args.model,
        base_url=base_url,
        temperature=0.7,
        max_tokens=256,
        timeout_seconds=60,
        retries=0,
        reasoning_effort=args.reasoning_effort,
        api_key_env=args.key_env,
    )

    print(f"model:  {args.model}")
    print(f"host:   {base_url}")
    provider = OpenAICompatProvider()
    started = time.monotonic()
    try:
        response = asyncio.run(provider.generate(messages, schema, config))
    except ValueError as exc:
        print(f"config error: {exc}")
        return
    except OpenAICompatError as exc:
        print(f"failed after {time.monotonic() - started:.0f}s: {exc}")
        return
    except Exception as exc:  # noqa: BLE001 - a smoke test reports, it does not crash
        print(f"failed after {time.monotonic() - started:.0f}s: {exc!r}")
        return

    elapsed = time.monotonic() - started
    print(f"elapsed: {elapsed:.1f}s   output tokens: {response.tokens_used}")
    print(f"json_schema accepted: {provider.supports_json_schema}")
    print(f"raw reply: {response.content[:300]!r}\n")

    try:
        action = json.loads(response.content)
    except json.JSONDecodeError as exc:
        print(f"NOT VALID JSON: {exc}")
        print("The prompt asked for JSON. If the route has no schema support, "
              "this is the failure a run would re-ask on.")
        return

    print(f"fields: {sorted(action)}")
    print("parsed cleanly")


if __name__ == "__main__":
    main()
