"""Optional observability hook (spec section 28).

The MVP ships a no-op tracer so the `observability` config flag is
wired end to end without making Langfuse a hard dependency. Real
Langfuse export lands with Milestone 4 (spec section 36).
"""

from __future__ import annotations

from typing import Any, Optional

from simulation.experiments.config import ObservabilitySettings


class NullTracer:
    """Accepts trace records and does nothing with them."""

    def __init__(self, provider: str = "none", note: str = "") -> None:
        self.provider = provider
        self.note = note
        self.enabled = False

    def record(self, event: str, **fields: Any) -> Optional[dict[str, Any]]:
        """Record one observability event; returns None when disabled."""
        return None


def build_tracer(settings: ObservabilitySettings) -> NullTracer:
    """Build the tracer for an observability config block.

    Disabled: plain no-op. Enabled: still a no-op for now, but says so,
    so nobody assumes traces are being exported.
    """
    if settings.enabled:
        return NullTracer(
            provider=settings.provider,
            note=(
                f"observability.enabled=true but {settings.provider} export is "
                "not implemented yet (Milestone 4); records are discarded"
            ),
        )
    return NullTracer(provider=settings.provider)
