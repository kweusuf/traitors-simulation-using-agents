#!/usr/bin/env python3
"""Report traitor/faithful win rates across completed runs, with Wilson CIs.

Reads each run's metrics.json and config.yaml snapshot, so runs can be
segmented by the lever that produced them (pinned cast, suspicion ledger,
traitor mandate, finale, model). Used for the traitor-bias experiments.

Usage (from ``agent/``)::

    uv run --no-sync python tools/win_rate_report.py
    uv run --no-sync python tools/win_rate_report.py --include bias- --by model
    uv run --no-sync python tools/win_rate_report.py --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from simulation.experiments.win_rate import (  # noqa: E402
    SEGMENTS,
    format_report,
    group,
    load_outcomes,
    summarise,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--runs-dir", type=Path, default=Path("runs"), help="run artifacts root"
    )
    parser.add_argument(
        "--include",
        action="append",
        default=[],
        help="only run ids containing this substring (repeatable)",
    )
    parser.add_argument(
        "--by",
        action="append",
        default=[],
        choices=sorted(SEGMENTS),
        help="segment the report (repeatable)",
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()

    outcomes = load_outcomes(args.runs_dir, args.include)
    if args.json:
        payload = {
            "completed": len(outcomes),
            "overall": summarise(outcomes).__dict__,
            "segments": {
                segment: {
                    label: summarise(rows).__dict__
                    for label, rows in group(outcomes, segment).items()
                }
                for segment in (args.by or [])
            },
        }
        print(json.dumps(payload, indent=2))
        return

    print(format_report(outcomes, by=args.by or []))


if __name__ == "__main__":
    main()
