"""An exclusive lock on a run directory, held for the lifetime of a run.

The idle-time guard in `load_point` cannot tell a dead run from a live
one that is simply slow. UK S01 runs showed a p95 call latency of 558s
and single phases lasting longer than the 300s threshold, so a perfectly
healthy run regularly looks abandoned. Resuming one truncates its log
mid-flight, and the two processes then append with independent sequence
counters: the run that produced `uk-s01-r3` ended up with 344 sequence
numbers carrying two contradictory events each and two divergent
endings in one file.

A lock does not guess. The first process to touch a run directory takes
an exclusive advisory lock on a file inside it and holds it until it
exits, however it exits. A second process - a resume, a re-run, anything
using the same `game_id` - cannot take the lock and is refused before it
reads, truncates or writes a single byte.
"""

from __future__ import annotations

import errno
import os
from pathlib import Path
from typing import Optional

try:  # pragma: no cover - platform guard
    import fcntl
except ImportError:  # pragma: no cover - Windows has no flock
    fcntl = None  # type: ignore[assignment]

LOCK_NAME = ".run.lock"


class RunLockedError(RuntimeError):
    """Another process is already writing this run directory."""


class RunLock:
    """An advisory exclusive lock over one run directory.

    Use as a context manager. A lock that cannot be taken is raised, not
    waited on: the caller is about to rewrite the log, and waiting would
    only make the collision happen later and harder to attribute.
    """

    def __init__(self, run_dir: str | Path) -> None:
        self.run_dir = Path(run_dir)
        self.path = self.run_dir / LOCK_NAME
        self._fd: Optional[int] = None

    def acquire(self) -> "RunLock":
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self._fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        if fcntl is None:  # pragma: no cover - no locking primitive
            return self
        try:
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(self._fd)
            self._fd = None
            if exc.errno in (errno.EACCES, errno.EAGAIN):
                holder = self._holder_pid()
                raise RunLockedError(
                    f"{self.run_dir} is locked by another process"
                    + (f" (pid {holder})" if holder else "")
                    + ". A run is still writing it; resume it only after "
                    "that process has stopped."
                ) from exc
            raise
        os.ftruncate(self._fd, 0)
        os.write(self._fd, f"{os.getpid()}\n".encode())
        os.fsync(self._fd)
        return self

    def _holder_pid(self) -> Optional[int]:
        """Best-effort read of who holds the lock, for the error message."""
        try:
            text = self.path.read_text(encoding="utf-8").strip()
            return int(text) if text else None
        except (OSError, ValueError):
            return None

    def release(self) -> None:
        if self._fd is None:
            return
        if fcntl is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            except OSError:  # pragma: no cover - already gone
                pass
        os.close(self._fd)
        self._fd = None

    @staticmethod
    def check_free(run_dir: str | Path) -> None:
        """Raise if some other process holds the run directory.

        The runner takes the lock itself, so this is a second line of
        defence for any caller that reaches the log directly: it turns
        "a resume rewrote a live run's events" from a silent corruption
        into a refusal, which is the whole point.
        """
        path = Path(run_dir) / LOCK_NAME
        if not path.exists():
            return
        probe = RunLock(run_dir)
        holder = probe._holder_pid()
        if holder == os.getpid():
            # We are the process doing the work. `GameRunner` takes the
            # lock and then calls into the resume path, so refusing our
            # own lock would make every resume impossible.
            return
        probe._fd = os.open(path, os.O_RDWR)
        try:
            if fcntl is None:  # pragma: no cover - no locking primitive
                return
            try:
                fcntl.flock(probe._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                if exc.errno in (errno.EACCES, errno.EAGAIN):
                    raise RunLockedError(
                        f"{run_dir} is locked by another process"
                        + (f" (pid {holder})" if holder else "")
                        + ". A run is still writing it; resume it only "
                        "after that process has stopped."
                    ) from exc
                raise
            # Nobody held it. Let go at once: this probe must never
            # become the lock it was checking for.
            fcntl.flock(probe._fd, fcntl.LOCK_UN)
        finally:
            os.close(probe._fd)
            probe._fd = None

    def __enter__(self) -> "RunLock":
        return self.acquire()

    def __exit__(self, *exc: object) -> None:
        self.release()