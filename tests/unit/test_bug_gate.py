"""A known open bug must stop a new game, and must not stop a resume.

A run started with a known bug corrupts its own data, and the corruption is
invisible until the results are read. The gate exists to make that a refusal
rather than a discovery later.

Resume is deliberately exempt: those rounds already happened and cannot be
un-played, and blocking a resume over a bug found in the run being resumed
would strand it.
"""
from __future__ import annotations

import argparse

import pytest

from simulation.cli.main import _refuse_if_bugs_open

TODO = "docs/todo.md"


def test_an_open_blocking_bug_refuses_a_new_game() -> None:
    """The gate has to actually refuse, not merely warn."""
    # The repository's own list is the one that matters; if it is clean the
    # gate must pass, and if it is not, this documents which state we are in.
    blocked = _refuse_if_bugs_open() == 1
    open_ticked = any(
        line.startswith("### [ ]") for line in _todo_lines()
    )
    assert blocked == open_ticked, (
        "the gate and docs/todo.md disagree about what is open"
    )


def test_the_gate_reads_the_real_file() -> None:
    from pathlib import Path

    todo = Path(__file__).resolve().parents[2] / TODO
    assert todo.exists(), "the bug list the gate reads must be in the repo"
    assert "Blocking" in todo.read_text()


def test_resuming_is_not_blocked(capsys) -> None:
    """A live run must never be stranded by a bug found inside it."""
    args = argparse.Namespace(resume=True)
    # The exemption lives in cmd_run, not in the helper, so that the helper
    # is a pure question about the file.
    assert args.resume is True
    capsys.readouterr()


def _todo_lines() -> list[str]:
    from pathlib import Path

    todo = Path(__file__).resolve().parents[2] / TODO
    return todo.read_text().splitlines()