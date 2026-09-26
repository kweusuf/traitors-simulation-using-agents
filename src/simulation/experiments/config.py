"""Configuration loading (spec section 22): everything possible is config-driven."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import Field, model_validator

from simulation.engine.state import GamePhase
from simulation.models.base import StrictModel
from simulation.models.llm import ModelConfig

# Phase names that make up the configurable round loop (spec section 7).
# SETUP opens the game and GAME_END closes it, so neither is configurable.
CONFIGURABLE_PHASES = frozenset(
    p.value for p in GamePhase if p not in (GamePhase.SETUP, GamePhase.GAME_END)
)


class GameSettings(StrictModel):
    name: str = "traitors"
    players: int = Field(default=6, ge=3)
    traitors: int = Field(default=2, ge=1)
    faithful: Optional[int] = None
    max_rounds: int = Field(default=5, ge=1)
    allow_self_vote: bool = False
    # Who wins when max_rounds is exhausted without an elimination victory.
    round_limit_winner: str = "faithful"
    player_names: Optional[list[str]] = None
    # Persona names resolved against the configs/personas directory;
    # assigned round-robin when there are fewer names than players.
    personas: Optional[list[str]] = None

    @model_validator(mode="before")
    @classmethod
    def _check_counts(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        players = data.get("players", 6)
        traitors = data.get("traitors", 2)
        if traitors >= players:
            raise ValueError("traitors must be fewer than players")
        faithful = players - traitors
        if data.get("faithful") is not None and data["faithful"] != faithful:
            raise ValueError("faithful must equal players - traitors")
        data["faithful"] = faithful
        if data.get("round_limit_winner", "faithful") not in {"faithful", "traitor"}:
            raise ValueError("round_limit_winner must be 'faithful' or 'traitor'")
        return data


class CommunicationSettings(StrictModel):
    public_messages_per_agent: int = Field(default=1, ge=0)
    private_messages_per_agent: int = Field(default=2, ge=0)


class LLMSettings(StrictModel):
    """The `llm:` YAML block: model selection and model details.

    Everything about which model runs and how (spec section 19, 22)
    lives here so the game config stays the single source of truth.
    """

    provider: str = "ollama"
    model: str = "gpt-oss:20b"
    base_url: str = "http://localhost:11434"
    temperature: float = 0.7
    max_tokens: int = 512
    reasoning_effort: str = "medium"
    timeout_seconds: int = 120
    max_concurrency: int = Field(default=2, ge=1)
    # Provider-specific knobs the normalized fields do not cover.
    options: dict[str, Any] = Field(default_factory=dict)

    def to_model_config(self) -> ModelConfig:
        """Build the provider-facing normalized model config."""
        return ModelConfig(
            provider=self.provider,
            name=self.model,
            base_url=self.base_url,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            timeout_seconds=self.timeout_seconds,
            reasoning_effort=self.reasoning_effort,
            options=dict(self.options),
        )


class ObservabilitySettings(StrictModel):
    enabled: bool = False
    provider: str = "langfuse"


class GameConfig(StrictModel):
    game: GameSettings = Field(default_factory=GameSettings)
    phases: list[str] = Field(
        default_factory=lambda: [
            "mission",
            "public_discussion",
            "private_chat",
            "round_table",
            "voting",
            "elimination",
            "traitor_night",
        ]
    )
    communication: CommunicationSettings = Field(default_factory=CommunicationSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    seed: int = 42
    observability: ObservabilitySettings = Field(default_factory=ObservabilitySettings)

    @model_validator(mode="after")
    def _check_phases(self) -> "GameConfig":
        if not self.phases:
            raise ValueError("phases must not be empty")
        unknown = set(self.phases) - CONFIGURABLE_PHASES
        if unknown:
            raise ValueError(f"unknown phases: {sorted(unknown)}")
        if "voting" not in self.phases:
            raise ValueError("phases must include 'voting'")
        return self


def load_config(path: str | Path) -> GameConfig:
    """Load and validate a YAML game config, failing loudly on bad input."""
    raw = Path(path).read_text(encoding="utf-8")
    data = yaml.safe_load(raw)
    if not isinstance(data, dict):
        raise ValueError(f"config {path} must contain a YAML mapping")
    return GameConfig.model_validate(data)
