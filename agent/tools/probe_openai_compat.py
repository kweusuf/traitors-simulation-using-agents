"""Measure how much of a reply MiMo spends on hidden reasoning.

The season run hit the token cap because a public message that reads as
~1,000 characters reported 5,000-7,000 output tokens. This sends one real
prompt from a run through OpenAI-compatible and reports the output-token count
and the visible reply length, so `reasoning_effort` can be compared on the
same input instead of guessed at.

Usage:
  uv run --no-sync python tools/probe_openai_compat.py \
      --run runs/india-s01-mimo --effort low
  uv run --no-sync python tools/probe_openai_compat.py --run ... --effort none
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from prompt_composition import build_view  # noqa: E402
from simulation.actions.actions import ActionType, action_schema  # noqa: E402
from simulation.agents.goals import Goals  # noqa: E402
from simulation.agents.prompts import PromptBuilder  # noqa: E402
from simulation.agents.persona import Persona  # noqa: E402
from simulation.env import load_dotenv  # noqa: E402
from simulation.engine.state import Role  # noqa: E402
from simulation.models.base import ChatMessage  # noqa: E402
from simulation.models.openai_compat import (  # noqa: E402
    OpenAICompatError,
    OpenAICompatProvider,
)
from simulation.models.llm import ModelConfig  # noqa: E402

DEFAULT_MODEL = "deepseek-v4.1-flash"

def real_messages(run: Path, target_round: int) -> list[ChatMessage]:
    events = [json.loads(line) for line in open(run / "events.jsonl")]
    view = build_view(events, target_round)
    builder = PromptBuilder(
        transcript_limit=40,
        anti_echo_instructions=True,
        want_gist=False,
        gist_required=False,
    )
    system = builder.build_system(
        view.agent_id,
        Role.FAITHFUL,
        Persona(description="A careful player."),
        Goals(),
    )
    user = builder.build_user(view, ActionType.PUBLIC_MESSAGE, [])
    return [
        ChatMessage(role="system", content=system),
        ChatMessage(role="user", content=user),
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--round", type=int, default=None)
    parser.add_argument("--effort", default="none")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--max-tokens", type=int, default=8192)
    parser.add_argument(
        "--force-effort",
        action="store_true",
        help="send reasoning_effort even when it is 'none'",
    )
    args = parser.parse_args()

    load_dotenv()
    events = [json.loads(line) for line in open(args.run / "events.jsonl")]
    rounds = sorted({e["round"] for e in events if e.get("type") == "PUBLIC_MESSAGE"})
    target = args.round or rounds[-1]
    messages = real_messages(args.run, target)
    chars = sum(len(m.content) for m in messages)
    print(f"round:  {target}")
    print(f"prompt: {chars:,} chars")
    print(f"effort: {args.effort}")
    print(f"max_tokens: {args.max_tokens}")

    base_url = args.base_url or os.environ.get("OPENAI_COMPAT_BASE_URL")
    if not base_url:
        parser.error("set OPENAI_COMPAT_BASE_URL or pass --base-url")

    config = ModelConfig(
        provider="openai_compat",
        name=args.model,
        base_url=base_url,
        temperature=0.7,
        max_tokens=args.max_tokens,
        timeout_seconds=600,
        retries=0,
        reasoning_effort=args.effort,
        options={"send_reasoning_effort": True} if args.force_effort else {},
    )
    provider = OpenAICompatProvider()
    start = time.monotonic()
    try:
        response = asyncio.run(
            provider.generate(
                messages,
                action_schema(ActionType.PUBLIC_MESSAGE),
                config,
            )
        )
    except ValueError as exc:
        print(f"config error: {exc}")
        return
    except OpenAICompatError as exc:
        print(f"failed after {time.monotonic() - start:.0f}s: {exc}")
        return

    elapsed = time.monotonic() - start
    print(f"elapsed: {elapsed:.1f}s")
    print(f"output tokens: {response.tokens_used}")
    print(f"visible chars: {len(response.content):,}")
    if response.tokens_used:
        print(f"tokens per visible char: {response.tokens_used / max(len(response.content), 1):.2f}")
    try:
        action = json.loads(response.content)
    except json.JSONDecodeError as exc:
        print(f"NOT VALID JSON: {exc}")
        return
    print(f"content chars: {len(action.get('content') or ''):,}")


if __name__ == "__main__":
    main()
