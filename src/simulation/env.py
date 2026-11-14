"""Minimal `.env` loading for local credentials.

The repository has no runtime dependency that reads dotenv files, and the
only thing needed here is a handful of `KEY=VALUE` lines. Secrets stay in
the gitignored `.env`; tracked config records only the variable *name*
(`llm.api_key_env`).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import MutableMapping, Optional


def parse_env(text: str) -> dict[str, str]:
    """Parse dotenv text into a mapping.

    Blank lines and `#` comments are skipped, an optional `export ` prefix
    is removed, and matching single or double quotes around the value are
    stripped. A line without `=` is ignored rather than failing the run.
    """
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        values[key] = value
    return values


def load_dotenv(
    path: Optional[str | Path] = None,
    env: Optional[MutableMapping[str, str]] = None,
) -> dict[str, str]:
    """Load `path` into `env` without overriding variables already set.

    Defaults to `$SIMULATION_ENV_FILE` or `./.env`. A missing file is a
    no-op, which keeps every command that does not need credentials
    working on a fresh checkout.
    """
    target = env if env is not None else os.environ
    resolved = Path(path or os.environ.get("SIMULATION_ENV_FILE") or ".env")
    try:
        text = resolved.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError:
        return {}

    loaded: dict[str, str] = {}
    for key, value in parse_env(text).items():
        if key in target:
            continue
        target[key] = value
        loaded[key] = value
    return loaded
