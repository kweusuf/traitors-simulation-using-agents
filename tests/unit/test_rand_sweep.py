"""The sweep supervisor: the command an attempt runs, and when it stops.

The first run of this sweep lost all seven arms to one closed socket: a
transport failure the provider's retries cannot absorb propagates out of the
run and ends it, and nothing invoked the resume machinery that exists for
exactly that. The supervisor is the answer, so its decisions are pinned here -
attempt 1 is a new run unless the arm is already on disk, a zero exit is a
finished game rather than a crash, and a crash is resumed from the arm's own
log until the cap says stop.
"""
from __future__ import annotations

import importlib.util
import subprocess
import types
from pathlib import Path
from typing import Any

TOOL = Path(__file__).resolve().parents[2] / "tools" / "run_rand_sweep.py"


def _tool() -> types.ModuleType:
    """Load the sweep tool from its file.

    `tools/` is a directory of scripts rather than a package, and pytest's
    `pythonpath` reaches `src` only, so the module is imported by path.
    Importing it has to stay side-effect free: it reads no config and starts
    no run until `main()` is called.
    """
    spec = importlib.util.spec_from_file_location("run_rand_sweep", TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _args(**overrides: Any) -> types.SimpleNamespace:
    """The arguments one supervisor is launched with."""
    base = dict(
        arms=["ledger"],
        session_name="uk-s01-rand-ledger",
        base_url="http://HOST_A:11434",
        runs_dir="runs",
        log_dir="logs",
        resume=False,
        max_resumes=2,
        resume_backoff=0.0,
    )
    base.update(overrides)
    return types.SimpleNamespace(**base)


def test_first_attempt_of_an_arm_that_is_not_on_disk_is_a_new_run() -> None:
    tool = _tool()
    assert tool.command_for_attempt(
        1, fresh=["new"], resume=["resume"], resume_first=False
    ) == ["new"]


def test_first_attempt_of_an_arm_already_on_disk_resumes() -> None:
    """A folder that exists holds rounds that cannot be replayed away."""
    tool = _tool()
    assert tool.command_for_attempt(
        1, fresh=["new"], resume=["resume"], resume_first=True
    ) == ["resume"]


def test_every_later_attempt_resumes_even_when_the_first_was_new() -> None:
    tool = _tool()
    for attempt in (2, 3, 40):
        assert tool.command_for_attempt(
            attempt, fresh=["new"], resume=["resume"], resume_first=False
        ) == ["resume"]


def test_a_finished_game_is_not_resumed() -> None:
    tool = _tool()
    assert tool.should_retry(0, 1, max_resumes=20) is False


def test_a_crash_is_resumed_up_to_the_cap() -> None:
    tool = _tool()
    assert tool.should_retry(1, 1, max_resumes=2) is True
    assert tool.should_retry(1, 2, max_resumes=2) is True
    # The cap counts resumes. The run that created the folder is not one, so
    # the last attempt allowed is max_resumes + 1.
    assert tool.should_retry(1, 3, max_resumes=2) is False


def test_the_resume_command_lowers_the_idle_guard_inside_the_arms_log() -> None:
    """A crash is recent, and the resume module will not rewrite a recent log.

    `load_point` treats a log that moved recently as one a live run is still
    writing, which a crash always looks like. The sweep hands the resume its
    own, much shorter window: the directory lock, not the clock, is what
    proves the writer is gone.
    """
    from simulation.experiments.resume import DEFAULT_IDLE_SECONDS

    tool = _tool()
    fresh, resume = tool._commands(
        "ledger", "uk-s01-rand-ledger", "http://HOST_A:11434"
    )
    assert fresh[-1] == "--quiet" and "--resume" not in fresh
    assert resume[: len(fresh)] == fresh
    assert resume[len(fresh):] == [
        "--resume", "--idle-seconds", str(tool.RESUME_IDLE_SECONDS)
    ]
    assert 0 < tool.RESUME_IDLE_SECONDS < DEFAULT_IDLE_SECONDS


def test_a_resume_keeps_the_run_identity_and_the_config_it_started_with() -> None:
    """Continuing a run means the same folder, the same config, one writer."""
    tool = _tool()
    fresh, resume = tool._commands(
        "ledger", "uk-s01-rand-ledger", "http://HOST_A:11434"
    )
    for command in (fresh, resume):
        assert command[0] == tool.sys.executable
        assert command[command.index("--game-id") + 1] == "uk-s01-rand-ledger"
        assert command[command.index("run") + 1].endswith(
            "season_uk_s01.ledger.rand.yaml"
        )


class _Proc:
    """Enough of a `Popen` for the supervisor's loop."""

    def __init__(self, code: int) -> None:
        self._code = code
        self.terminated = False

    def wait(self) -> int:
        return self._code

    def poll(self) -> int:
        return self._code

    def terminate(self) -> None:
        self.terminated = True

def _supervise_once(tool, monkeypatch, tmp_path, codes, **overrides):
    """Run `supervise` against fake attempts that exit with `codes` in turn.

    Returns the exit code, the commands that were attempted, and the log, so a
    test can see whether the second attempt resumed or started over.
    """
    seen: list[list[str]] = []

    def popen(command, **_kwargs):
        seen.append(list(command))
        return _Proc(codes[min(len(seen) - 1, len(codes) - 1)])

    monkeypatch.setattr(
        tool, "subprocess",
        types.SimpleNamespace(Popen=popen, STDOUT=subprocess.STDOUT),
    )
    # The real handler would replace this process's SIGTERM behaviour.
    monkeypatch.setattr(
        tool, "signal",
        types.SimpleNamespace(signal=lambda *a: None, SIGTERM=15),
    )
    monkeypatch.setattr(
        tool, "_commands",
        lambda arm, game_id, base_url: (["new", arm], ["resume", arm]),
    )
    monkeypatch.setattr(tool, "ROOT", tmp_path)
    exit_code = tool.supervise(_args(**overrides))
    return exit_code, seen, (tmp_path / "logs" / "ledger.log").read_text()


def test_a_crashed_arm_is_resumed_and_a_finished_one_is_not(
    monkeypatch, tmp_path
) -> None:
    """The whole point: a non-zero exit resumes, a zero exit does not."""
    tool = _tool()
    exit_code, seen, log = _supervise_once(tool, monkeypatch, tmp_path, [1, 0])
    assert exit_code == 0
    assert seen == [["new", "ledger"], ["resume", "ledger"]]
    assert "attempt 1 (new run)" in log
    assert "attempt 2 (resume)" in log
    assert "stopped: exit 0 after 2 attempt(s)" in log


def test_the_supervisor_stops_at_the_cap_instead_of_looping(
    monkeypatch, tmp_path
) -> None:
    """A host that is down fails every attempt in seconds, so the loop is
    bounded: three attempts here, the new run plus `max_resumes` resumes."""
    tool = _tool()
    exit_code, seen, log = _supervise_once(
        tool, monkeypatch, tmp_path, [1, 1, 1, 1]
    )
    assert exit_code == 1
    assert len(seen) == 3
    assert "stopped: exit 1 after 3 attempt(s)" in log


def test_an_arm_already_on_disk_is_resumed_on_its_first_attempt(
    monkeypatch, tmp_path
) -> None:
    tool = _tool()
    exit_code, seen, log = _supervise_once(
        tool, monkeypatch, tmp_path, [0], resume=True
    )
    assert exit_code == 0
    assert seen == [["resume", "ledger"]]
    assert "attempt 1 (resume)" in log
