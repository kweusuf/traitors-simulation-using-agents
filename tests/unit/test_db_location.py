from __future__ import annotations

import argparse
from pathlib import Path

from simulation.cli.main import run_db_path


class _Args(argparse.Namespace):
    pass


def test_default_db_path_is_inside_the_run_folder():
    args = _Args(runs_dir="runs", db=None)
    assert run_db_path(args, "game-007") == str(Path("runs/game-007/game.db"))


def test_explicit_db_flag_wins():
    args = _Args(runs_dir="runs", db="/tmp/custom.db")
    assert run_db_path(args, "game-007") == "/tmp/custom.db"


def test_explicit_memory_db_is_not_overridden():
    # The fake-backend tests rely on this; a run-folder default that
    # overrode :memory: would silently start writing files during tests.
    args = _Args(runs_dir="runs", db=":memory:")
    assert run_db_path(args, "game-007") == ":memory:"


def test_runs_dir_is_respected():
    args = _Args(runs_dir="elsewhere", db=None)
    assert run_db_path(args, "uk-s01-en5") == str(Path("elsewhere/uk-s01-en5/game.db"))