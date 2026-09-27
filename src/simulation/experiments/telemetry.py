"""Per-call LLM telemetry for a run (spec section 27, 34).

One JSON line per model call, written inside the run directory as
`llm_calls.jsonl`, plus an in-memory aggregate the runner folds into
`metrics.json`. Together they give a run its own 360 degree view of the
LLM operations: tokens in and out, latency, transport retries, failed
turns, and which agent and action produced each call.

Nothing here talks to a model or to the game: the recorder only
observes calls the runtime already makes.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional


@dataclass
class CallRecord:
    """One attempted model call."""

    sequence: int
    agent_id: str
    action_type: str
    phase: str
    round: int
    attempt: int  # 1-based attempt inside the agent's decide loop
    model: str
    ok: bool
    latency_ms: Optional[float] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    prompt_chars: int = 0
    retries: int = 0  # provider transport retries spent on this call
    error: Optional[str] = None


def _percentile(values: list[float], fraction: float) -> Optional[float]:
    """Nearest-rank percentile; None when there is nothing to rank."""
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))
    return round(ordered[index], 1)


class TelemetryRecorder:
    """Collects every model call of one game run."""

    def __init__(self, model: str, path: Optional[Path] = None) -> None:
        self.model = model
        self.path = Path(path) if path is not None else None
        self.records: list[CallRecord] = []
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # Append-only log of this run only; start clean on a re-run.
            self.path.write_text("", encoding="utf-8")

    def record(
        self,
        *,
        agent_id: str,
        action_type: str,
        phase: str,
        round_number: int,
        attempt: int,
        ok: bool,
        response: Any = None,
        retries: int = 0,
        error: Optional[str] = None,
        prompt_chars: int = 0,
    ) -> CallRecord:
        """Store one call, on disk and in memory."""
        record = CallRecord(
            sequence=len(self.records) + 1,
            agent_id=agent_id,
            action_type=action_type,
            phase=phase,
            round=round_number,
            attempt=attempt,
            model=self.model,
            ok=ok,
            latency_ms=(
                round(float(response.latency_ms), 1)
                if response is not None and response.latency_ms
                else None
            ),
            input_tokens=getattr(response, "input_tokens", None) if response else None,
            output_tokens=getattr(response, "tokens_used", None) if response else None,
            prompt_chars=prompt_chars,
            retries=retries,
            error=error,
        )
        self.records.append(record)
        if self.path is not None:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(asdict(record), sort_keys=True) + "\n")
        return record

    def summary(self) -> dict[str, Any]:
        """Aggregate view merged into `metrics.json` under `llm`."""
        records = self.records
        input_total = sum(r.input_tokens or 0 for r in records)
        output_total = sum(r.output_tokens or 0 for r in records)
        with_tokens = [r for r in records if r.input_tokens is not None]
        latencies = [r.latency_ms for r in records if r.latency_ms is not None]
        failures = [
            {
                "agent_id": r.agent_id,
                "action_type": r.action_type,
                "attempt": r.attempt,
                "error": (r.error or "")[:200],
            }
            for r in records
            if not r.ok
        ]

        by_action: dict[str, dict[str, Any]] = {}
        for record in records:
            bucket = by_action.setdefault(
                record.action_type,
                {"calls": 0, "failed": 0, "input_tokens": 0, "output_tokens": 0},
            )
            bucket["calls"] += 1
            bucket["failed"] += 0 if record.ok else 1
            bucket["input_tokens"] += record.input_tokens or 0
            bucket["output_tokens"] += record.output_tokens or 0

        return {
            "calls": len(records),
            "failed_calls": len(failures),
            "parse_retry_calls": sum(1 for r in records if r.attempt > 1),
            "transport_retries": sum(r.retries for r in records),
            "input_tokens": input_total,
            "output_tokens": output_total,
            "total_tokens": input_total + output_total,
            "avg_input_tokens": (
                round(input_total / len(with_tokens), 1) if with_tokens else None
            ),
            "avg_output_tokens": (
                round(output_total / len(with_tokens), 1) if with_tokens else None
            ),
            # Calls where the provider reported token counts (the fake
            # provider never does, so 0 coverage there is expected).
            "token_reported_calls": len(with_tokens),
            "prompt_chars_total": sum(r.prompt_chars for r in records),
            "latency_ms": {
                "total": round(sum(latencies), 1),
                "avg": round(sum(latencies) / len(latencies), 1) if latencies else None,
                "p50": _percentile(latencies, 0.50),
                "p95": _percentile(latencies, 0.95),
                "max": round(max(latencies), 1) if latencies else None,
            },
            "by_action_type": dict(sorted(by_action.items())),
            "failures": failures[:20],
        }
