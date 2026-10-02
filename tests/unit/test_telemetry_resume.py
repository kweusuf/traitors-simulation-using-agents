"""Telemetry must survive a resume."""

from __future__ import annotations

from simulation.experiments.telemetry import TelemetryRecorder


def test_resume_keeps_the_calls_recorded_before_the_interruption(tmp_path) -> None:
    """Telemetry survives a resume.

    The recorder used to truncate llm_calls.jsonl in __init__, so every
    resume threw away the calls already spent on the run and metrics.json
    reported only the tail. `resume.py` documents the file as append-only,
    so the truncation contradicted the code's own contract.
    """
    path = tmp_path / "llm_calls.jsonl"
    first = TelemetryRecorder("m", path)
    for _ in range(3):
        first.record(
            agent_id="alice", action_type="vote", phase="voting",
            round_number=1, attempt=1, ok=True,
        )
    assert len(first.records) == 3

    # A resumed run keeps them, and keeps writing to the same file.
    resumed = TelemetryRecorder("m", path, resume=True)
    assert len(resumed.records) == 3, "the earlier calls were dropped"
    resumed.record(
        agent_id="bob", action_type="vote", phase="voting",
        round_number=2, attempt=1, ok=True,
    )
    lines = [l for l in path.read_text().splitlines() if l.strip()]
    assert len(lines) == 4, "the resumed recorder replaced the file"

    # A genuinely new run of the same game id still starts clean.
    TelemetryRecorder("m", path)
    assert sum(1 for l in path.read_text().splitlines() if l.strip()) == 0


def test_resume_survives_a_corrupt_telemetry_line(tmp_path) -> None:
    path = tmp_path / "llm_calls.jsonl"
    first = TelemetryRecorder("m", path)
    first.record(
        agent_id="alice", action_type="vote", phase="voting",
        round_number=1, attempt=1, ok=True,
    )
    with path.open("a", encoding="utf-8") as handle:
        handle.write("{not json at all\n")
    resumed = TelemetryRecorder("m", path, resume=True)
    assert len(resumed.records) == 1


def test_summary_covers_rehydrated_records(tmp_path) -> None:
    path = tmp_path / "llm_calls.jsonl"
    first = TelemetryRecorder("m", path)
    for _ in range(4):
        first.record(
            agent_id="alice", action_type="vote", phase="voting",
            round_number=1, attempt=1, ok=True,
        )
    resumed = TelemetryRecorder("m", path, resume=True)
    assert resumed.summary()["calls"] == 4, (
        "the summary only counted calls made since the resume"
    )