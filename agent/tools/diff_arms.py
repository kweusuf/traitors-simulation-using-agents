"""Confirm a new arm differs from its parent by only the intended flag.

An experiment is only readable if exactly one setting moved between the two
runs. A text diff cannot tell you that: a value the author assumed was
inherited may not be. This loads both configs through the same loader the
simulator uses and diffs the resulting settings, so the comparison is
between what the run would actually do, not between two files.

Per-machine settings are excluded - the endpoint is always different
between an arm and its local variant and says nothing about the treatment.

Usage:
  python tools/diff_arms.py configs/traitors/parent.yaml configs/traitors/child.yaml
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from simulation.experiments.config import load_config  # noqa: E402

PER_MACHINE = ("base_url", "model_path")


def flatten(cfg) -> dict[str, object]:
    """Every leaf setting, dotted, so a diff shows the real path."""
    out: dict[str, object] = {}
    for name in ("game", "llm", "communication", "observability"):
        section = getattr(cfg, name, None)
        if section is None:
            continue
        for key, value in section.model_dump().items():
            if key in PER_MACHINE:
                continue
            out[f"{name}.{key}"] = value
    out["seed"] = cfg.seed
    return out


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    a_path, b_path = sys.argv[1], sys.argv[2]
    a, b = flatten(load_config(a_path)), flatten(load_config(b_path))
    diffs = [
        (k, a.get(k), b.get(k))
        for k in sorted(set(a) | set(b))
        if a.get(k) != b.get(k)
    ]
    print(f"{Path(a_path).name}  ->  {Path(b_path).name}\n")
    if not diffs:
        print("IDENTICAL: this arm is not an experiment.")
        raise SystemExit(1)
    for key, was, now in diffs:
        print(f"  {key}\n    {was!r} -> {now!r}")
    print(f"\n{len(diffs)} setting(s) differ.")


if __name__ == "__main__":
    main()