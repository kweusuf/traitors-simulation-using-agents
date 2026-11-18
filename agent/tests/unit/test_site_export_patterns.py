"""The site exporter and the JS validator must agree on forbidden shapes."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "web" / "tools"))

import export_site_data as export  # noqa: E402


def test_compressed_ipv6_is_sanitized() -> None:
    assert export.sanitize_text("2606:4700::1111") == "HOST_A"
    assert export.sanitize_text("2a00:1450:4001:80f::200e") == "HOST_A"
    assert export.sanitize_text("fd00::1") == "HOST_A"


def test_loopback_and_prose_colons_are_not_rewritten() -> None:
    assert export.sanitize_text("::1") == "::1"
    assert export.sanitize_text("12:34:56") == "12:34:56"
    assert export.sanitize_text("slice[start:stop]") == "slice[start:stop]"


def test_forbidden_keys_are_dropped() -> None:
    assert export.sanitize({"base_url": "x", "keep": 1}) == {"keep": 1}


def test_js_validator_mentions_every_python_pattern_label() -> None:
    js = (ROOT / "web" / "scripts" / "validate-data.mjs").read_text(
        encoding="utf-8"
    )
    for label, _pattern in export.SECRET_PATTERNS:
        assert f"'{label}'" in js or f'"{label}"' in js, label
    assert "ipv6" in js
