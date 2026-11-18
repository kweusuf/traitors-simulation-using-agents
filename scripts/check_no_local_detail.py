#!/usr/bin/env python3
"""Fail when tracked files contain high-confidence local-only detail.

This is a guard, not a full secret scanner. It catches the shapes the
repository's hard rule names: IP addresses (private or public), absolute
user paths, model digests, credential prefixes, JWTs, bearer tokens, and
PEM headers. Placeholders such as ``HOST_A``/``HOST_B`` and loopback
addresses are allowed.

Lock files and the Dockerfile are skipped: their hashes are dependency or
base-image hashes, not local model digests.
"""

from __future__ import annotations

import ipaddress
import re
import subprocess
import sys
from pathlib import Path

SKIP_NAMES = {
    "Dockerfile",
    "uv.lock",
    "pnpm-lock.yaml",
    "requirements.lock",
    # These files contain the detector patterns themselves.
    "export_site_data.py",
    "validate-data.mjs",
    "check_no_local_detail.py",
}
SKIP_SUFFIXES = {".lock", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".woff", ".woff2"}

IPV4_ANY = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
IPV6_CANDIDATE = re.compile(
    r"(?<![0-9A-Za-z])([0-9A-Fa-f:]{2,})(?![0-9A-Za-z])"
)
ABSOLUTE_USER_PATH = re.compile(
    r"(?:/Users/|/Volumes/|/home/[A-Za-z0-9._-]+/|[A-Za-z]:\\Users\\)"
)
DIGEST = re.compile(r"\b(?:sha256:)?[0-9a-f]{64}\b", re.IGNORECASE)
KEY_PREFIX = re.compile(
    r"\b(?:sk-|sk_live_|sk_test_|ghp_|gho_|ghs_|github_pat_|glpat_|glpat-|"
    r"xox[baprs]-|xoxc-|xoxp-|AKIA|AIza|oc_|npm_|pypi-)"
    r"[A-Za-z0-9_\-]{8,}\b"
)
JWT = re.compile(
    r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"
)
BEARER = re.compile(r"\bBearer\s+[A-Za-z0-9_.\-]{12,}\b", re.IGNORECASE)
PEM = re.compile(r"-----BEGIN [A-Z ]+-----")

PATTERNS = (
    ("absolute-user-path", ABSOLUTE_USER_PATH),
    ("digest", DIGEST),
    ("key-prefix", KEY_PREFIX),
    ("jwt", JWT),
    ("bearer", BEARER),
    ("pem", PEM),
)


def address_failures(path: Path, line_number: int, line: str) -> list[str]:
    """Flag any IPv4/IPv6 literal except loopback and unspecified."""
    failures: list[str] = []
    for match in IPV4_ANY.finditer(line):
        try:
            address = ipaddress.ip_address(match.group(0))
        except ValueError:
            continue
        if address.is_loopback or address.is_unspecified:
            continue
        failures.append(
            f"{path}:{line_number}: ipv4: {match.group(0)[:80]}"
        )
    for match in IPV6_CANDIDATE.finditer(line):
        token = match.group(1)
        if token.count(":") < 2:
            continue
        try:
            address = ipaddress.ip_address(token)
        except ValueError:
            continue
        if address.version == 6 and not (
            address.is_loopback or address.is_unspecified
        ):
            failures.append(f"{path}:{line_number}: ipv6: {token[:80]}")
    return failures


def tracked_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        check=True,
        capture_output=True,
    )
    return [
        Path(name)
        for name in result.stdout.decode("utf-8").split("\0")
        if name
    ]


def main() -> int:
    failures: list[str] = []
    for path in tracked_files():
        if path.name in SKIP_NAMES or path.suffix.lower() in SKIP_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        is_test = path.parts[:2] == ("agent", "tests")
        for line_number, line in enumerate(text.splitlines(), start=1):
            if not is_test:
                failures.extend(address_failures(path, line_number, line))
            for label, pattern in PATTERNS:
                if is_test and label in {"key-prefix", "jwt", "bearer"}:
                    continue
                match = pattern.search(line)
                if match:
                    failures.append(
                        f"{path}:{line_number}: {label}: {match.group(0)[:80]}"
                    )

    if failures:
        print("Local-detail scan failed:", file=sys.stderr)
        for failure in failures[:100]:
            print(f"  {failure}", file=sys.stderr)
        return 1
    print("Local-detail scan passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
