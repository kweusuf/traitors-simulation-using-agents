"""Can the candidate model carry one of our real prompts?

A model that loads and answers a two-line question tells us nothing. What
matters is whether it holds the actual shape of the work: a ~26,000
character prompt, a system prompt, and a grammar-constrained reply. This
sends one real prompt through the same path a run would and reports what
came back - valid JSON, the fields present, how long it took.

Usage:
  python tools/probe_model.py hauhau-qwen-27b [runs/uk-s01-ptr-mem]
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from simulation.actions.actions import ActionType, action_schema  # noqa: E402
from simulation.agents.goals import Goals  # noqa: E402
from simulation.agents.prompts import PromptBuilder  # noqa: E402
from simulation.agents.persona import Persona  # noqa: E402
from simulation.engine.state import GamePhase, Role  # noqa: E402

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

    body = json.dumps({
        "model": model,
        "messages": messages,
        "format": action_schema(
            ActionType.PUBLIC_MESSAGE,
            want_gist=want_gist, gist_required=want_gist,
        ).model_json_schema(),
        "stream": False,
        "options": {"num_predict": 768, "temperature": 0.7},
    }).encode()

    start = time.monotonic()
    try:
        request = urllib.request.Request(
            f"{base}/api/chat", data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=900) as response:
            raw = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        print(f"HTTP {exc.code}: {exc.read()[:300]!r}")
        return
    except Exception as exc:  # noqa: BLE001 - reported, not swallowed
        print(f"failed after {time.monotonic() - start:.0f}s: {exc!r}")
        return

    elapsed = time.monotonic() - start
    text = (raw.get("message") or {}).get("content", "")
    tokens = raw.get("eval_count") or 0
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