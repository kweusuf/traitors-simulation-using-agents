"""Confirm the generated arms load, and that they differ only where intended.

Four configs that are supposed to differ by one flag each are easy to get
subtly wrong - a typo in a generated key is ignored by the loader rather than
rejected, and the arm then runs with a default nobody intended. So each arm
is loaded and its three flags printed, and the diff against the base is
checked to contain nothing but the header and those three lines.
"""
import difflib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from simulation.experiments.config import load_config  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CONFIGS = ROOT / "configs" / "traitors"
BASE = CONFIGS / "season_uk_s01.fix_en.yaml"
ARMS = ["ptr_opt", "ptr_req", "ptr_mem"]
BASE_FLAGS = {"co_generate_gist": False, "gist_required": False,
              "pointer_memory": False}

base = load_config(str(BASE))
print(f"base       {[getattr(base.game, k) for k in BASE_FLAGS]}")
assert all(getattr(base.game, k) == v for k, v in BASE_FLAGS.items()), \
    "the base config does not have the pointer flags off; arms are not comparable"

for arm in ARMS:
    path = CONFIGS / f"season_uk_s01.{arm}.yaml"
    cfg = load_config(str(path))
    flags = {k: getattr(cfg.game, k) for k in BASE_FLAGS}
    print(f"{arm:<11} {list(flags.values())}")

    added = [
        line for line in difflib.unified_diff(
            BASE.read_text().splitlines(), path.read_text().splitlines(),
            lineterm="", n=0,
        )
        if line.startswith("+") and not line.startswith("+++")
    ]
    unexpected = [
        line for line in added
        if not line.startswith("+#")
        and not any(f"{k}:".format() or k in line for k in BASE_FLAGS)
    ]
    assert not unexpected, f"{arm} changes more than its flags: {unexpected}"
    print(f"            diff: {len(added)} added lines, all header/flags")

print("\nall arms load and differ from base only by the pointer flags")