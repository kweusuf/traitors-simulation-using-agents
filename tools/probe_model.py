"""Can the candidate model carry one of our real prompts?

A model that loads and answers a two-line question tells us nothing. What
matters is whether it holds the actual shape of the work: a ~26,000
character prompt, a system prompt, and a grammar-constrained reply. This
sends one real prompt through the same path a run would and reports what
came back - valid JSON, the fields present, how long it took.

It goes through the provider's transport rather than its own HTTP call,
for the reason the `fix_hl` header records: a probe that opens its own
connection can measure the probe. What it still cannot do is size a host
for concurrency - one request says nothing about how many run at once.

Usage:
  python tools/probe_model.py hauhau-qwen-27b [runs/uk-s01-ptr-mem]
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from simulation.actions.actions import ActionType, action_schema  # noqa: E402
from simulation.agents.goals import Goals  # noqa: E402
from simulation.agents.prompts import PromptBuilder  # noqa: E402
from simulation.agents.persona import Persona  # noqa: E402
from simulation.engine.state import GamePhase, Role  # noqa: E402
from simulation.models.base import ChatMessage  # noqa: E402
from simulation.models.llm import ModelConfig  # noqa: E402
from simulation.models.ollama import OllamaError, OllamaProvider  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from prompt_composition import build_view  # noqa: E402

HOST = "HOST_A"  # placeholder; the endpoint comes from the config below


def real_messages(run: Path, want_gist: bool) -> list[dict]:
    """The prompt a real agent saw, rebuilt from the run folder."""
    events = [json.loads(line) for line in open(run / "events.jsonl")]
    rounds = sorted({e["round"] for e in events if e.get("round")})
    view = build_view(events, rounds[-1])
    builder = PromptBuilder(
        transcript_limit=40, anti_echo_instructions=True,
        want_gist=want_gist, gist_required=want_gist,
    )
    system = builder.build_system(
        view.agent_id, Role.FAITHFUL, Persona(description="A careful player."), Goals(),
    )
    user = builder.build_user(view, ActionType.PUBLIC_MESSAGE, [])
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def main() -> None:
    model = sys.argv[1]
    run = Path(sys.argv[2] if len(sys.argv) > 2 else "runs/uk-s01-ptr-mem")
    cfg_path = run / "config.yaml"
    base = "http://localhost:11434"
    if cfg_path.exists():
        for line in cfg_path.read_text().splitlines():
            if line.strip().startswith("base_url:"):
                base = line.split(":", 1)[1].strip()
    want_gist = len(sys.argv) > 3

    messages = real_messages(run, want_gist)
    chars = sum(len(m["content"]) for m in messages)
    print(f"model: {model}")
    print(f"host:  {base}")
    print(f"prompt: {chars:,} chars across {len(messages)} messages")

    schema = action_schema(
        ActionType.PUBLIC_MESSAGE,
        want_gist=want_gist, gist_required=want_gist,
    )
    config = ModelConfig(
        name=model, base_url=base, temperature=0.7, max_tokens=768,
        timeout_seconds=900, reasoning_effort="none", retries=0,
    )

    start = time.monotonic()
    try:
        # `retries=0` on purpose: this asks what one call does, and a probe
        # that quietly retries cannot answer that.
        reply = asyncio.run(
            OllamaProvider().generate(
                [ChatMessage(**message) for message in messages], schema, config
            )
        )
    except OllamaError as exc:
        print(f"failed after {time.monotonic() - start:.0f}s: {exc}")
        return

    elapsed = time.monotonic() - start
    text = reply.content
    tokens = reply.tokens_used or 0
    print(f"\nelapsed: {elapsed:.0f}s   tokens: {tokens}   "
          f"~{tokens / max(elapsed, 1):.1f} tok/s")
    print(f"raw reply: {text[:300]!r}\n")

    try:
        action = json.loads(text)
    except json.JSONDecodeError as exc:
        print(f"NOT VALID JSON: {exc}")
        print("This is the failure that matters: the grammar should make it "
              "impossible, so a reply that will not parse means the schema "
              "was not enforced.")
        return

    print(f"fields: {sorted(action)}")
    missing = [f for f in ("action", "content") if not action.get(f)]
    if missing:
        print(f"MISSING: {missing}")
    if want_gist:
        gist = action.get("gist")
        content = action.get("content") or ""
        if not gist:
            print("NO GIST: the pointer arm would fall back for this turn")
        else:
            print(f"gist: {gist!r}  ({len(gist)} chars, "
                  f"{len(gist) / max(len(content), 1):.2f}x the message)")
    print("\nparsed cleanly")


main()