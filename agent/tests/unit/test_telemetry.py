"""Unit tests for the per-call LLM telemetry of a run."""

from __future__ import annotations

import json
from pathlib import Path

from simulation.experiments.telemetry import CallRecord, TelemetryRecorder
from simulation.models.llm import LLMResponse


def response(input_tokens=100, output_tokens=40, latency=12.5):
    return LLMResponse(
        content="{}",
        model="test-model",
        latency_ms=latency,
        tokens_used=output_tokens,
        input_tokens=input_tokens,
    )


def record(recorder: TelemetryRecorder, **overrides) -> CallRecord:
    """Record one call; token and latency overrides build the response."""
    latency = overrides.pop("latency", 12.5)
    input_tokens = overrides.pop("input_tokens", 100)
    output_tokens = overrides.pop("output_tokens", 40)
    params = dict(
        agent_id="alice",
        action_type="public_message",
        phase="public_discussion",
        round_number=1,
        attempt=1,
        ok=True,
        response=response(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency=latency,
        ),
    )
    params.update(overrides)
    return recorder.record(**params)


def test_records_one_json_line_per_call(tmp_path: Path) -> None:
    path = tmp_path / "llm_calls.jsonl"
    recorder = TelemetryRecorder("test-model", path)
    record(recorder)
    record(recorder, agent_id="bob", action_type="vote", ok=False, error="bad json")

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["agent_id"] == "alice"
    assert first["action_type"] == "public_message"
    assert first["input_tokens"] == 100
    assert first["output_tokens"] == 40
    assert first["latency_ms"] == 12.5
    assert first["ok"] is True

    second = json.loads(lines[1])
    assert second["ok"] is False
    assert second["error"] == "bad json"
    assert second["attempt"] == 1


def test_recorder_starts_the_file_clean(tmp_path: Path) -> None:
    path = tmp_path / "llm_calls.jsonl"
    path.write_text("stale line from a previous attempt\n", encoding="utf-8")
    TelemetryRecorder("test-model", path)
    assert path.read_text(encoding="utf-8") == ""


def test_summary_totals_tokens_latency_and_actions() -> None:
    recorder = TelemetryRecorder("test-model")
    for index in range(4):
        record(
            recorder,
            agent_id=f"p{index}",
            input_tokens=100 * (index + 1),
            output_tokens=10 * (index + 1),
            latency=10.0 * (index + 1),
        )
    record(
        recorder,
        action_type="vote",
        input_tokens=50,
        output_tokens=5,
        latency=99.0,
        ok=False,
        error="schema mismatch",
    )

    summary = recorder.summary()
    assert summary["calls"] == 5
    assert summary["failed_calls"] == 1
    assert summary["input_tokens"] == 100 + 200 + 300 + 400 + 50
    assert summary["output_tokens"] == 10 + 20 + 30 + 40 + 5
    assert summary["total_tokens"] == summary["input_tokens"] + summary["output_tokens"]
    assert summary["token_reported_calls"] == 5
    assert summary["avg_input_tokens"] == round(1050 / 5, 1)
    assert summary["latency_ms"]["max"] == 99.0
    assert summary["latency_ms"]["total"] == round(10 + 20 + 30 + 40 + 99, 1)
    assert summary["latency_ms"]["p50"] == 30.0
    assert summary["by_action_type"]["public_message"]["calls"] == 4
    assert summary["by_action_type"]["vote"] == {
        "calls": 1,
        "failed": 1,
        "input_tokens": 50,
        "output_tokens": 5,
    }
    assert summary["failures"] == [
        {
            "agent_id": "alice",  # the failing record kept the default agent
            "action_type": "vote",
            "attempt": 1,
            "error": "schema mismatch",
        }
    ]


def test_summary_handles_calls_without_token_reporting() -> None:
    recorder = TelemetryRecorder("test-model")
    record(recorder, response=LLMResponse(content="{}", latency_ms=None))
    summary = recorder.summary()
    assert summary["calls"] == 1
    assert summary["token_reported_calls"] == 0
    assert summary["input_tokens"] == 0
    assert summary["avg_input_tokens"] is None
    assert summary["latency_ms"]["avg"] is None
    assert summary["latency_ms"]["p95"] is None


def test_percentiles_rank_across_many_calls() -> None:
    recorder = TelemetryRecorder("test-model")
    for latency in range(1, 101):
        record(recorder, latency=float(latency))
    latency = recorder.summary()["latency_ms"]
    assert 45.0 <= latency["p50"] <= 55.0
    assert 90.0 <= latency["p95"] <= 100.0
    assert latency["max"] == 100.0
    assert latency["avg"] == 50.5


def test_transport_retries_and_parse_attempts_are_counted() -> None:
    recorder = TelemetryRecorder("test-model")
    record(recorder, attempt=1, retries=0)
    record(recorder, attempt=2, retries=1)  # correction retry after bad output
    record(recorder, attempt=1, retries=2, ok=False, error="OllamaError: timed out")
    summary = recorder.summary()
    assert summary["parse_retry_calls"] == 1
    assert summary["transport_retries"] == 3
    assert summary["failed_calls"] == 1
