"""Print the pointer flags a local arm actually loads with.

A run folder records the config it ran with, but only after it finishes.
Before spending six hours of model time it is worth confirming the run is
the arm it is named after - a mistyped key in a generated config does not
fail loudly, it loads with the default and the results are then attributed
to a flag that was never set.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from simulation.experiments.config import load_config  # noqa: E402

path = Path(sys.argv[1] if len(sys.argv) > 1
            else "configs/traitors/season_uk_s01.ptr_mem.local.yaml")
cfg = load_config(str(path))

print(path.name)
print(f"  co_generate_gist  {cfg.game.co_generate_gist}")
print(f"  gist_required     {cfg.game.gist_required}")
print(f"  pointer_memory    {cfg.game.pointer_memory}")
print("-- must match the rest of the treatment run --")
print(f"  anti_echo         {cfg.game.anti_echo_instructions}")
print(f"  phantom gate      {cfg.game.reject_invented_players}")
print(f"  agent_memory      {cfg.game.agent_memory}")
print(f"  transcript_limit  {cfg.communication.transcript_messages_per_prompt}")
print(f"  max_tokens        {cfg.llm.max_tokens}")
print(f"  max_concurrency   {cfg.llm.max_concurrency}")
print(f"  seed              {cfg.seed}")
print(f"  model             {cfg.llm.model}")
print(f"  base_url          {cfg.llm.base_url}")

want = {
    "co_generate_gist": True,
    "gist_required": True,
    "pointer_memory": True,
    "anti_echo_instructions": True,
    "reject_invented_players": True,
}
wrong = [k for k, v in want.items() if getattr(cfg.game, k) is not v]
if wrong:
    raise SystemExit(f"WRONG ARM: {wrong}")
print("\nflags match the intended arm")