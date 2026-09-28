"""Typed game state models (spec section 6).

The GameState is owned exclusively by the game engine. Agents never
mutate it directly; all changes go through engine commands/actions.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import Field

from simulation.models.base import StrictModel


class Role(str, Enum):
    TRAITOR = "traitor"
    FAITHFUL = "faithful"


class GamePhase(str, Enum):
    SETUP = "setup"
    MISSION = "mission"
    PUBLIC_DISCUSSION = "public_discussion"
    PRIVATE_CHAT = "private_chat"
    ROUND_TABLE = "round_table"
    VOTING = "voting"
    ELIMINATION = "elimination"
    TRAITOR_NIGHT = "traitor_night"
    # Finale-only end-or-banish vote (phase 25). Driven by the finale
    # loop, never listed in a config's phase ordering.
    END_VOTE = "end_vote"
    GAME_END = "game_end"


class PlayerState(StrictModel):
    player_id: str
    name: str
    alive: bool = True


class MissionState(StrictModel):
    round_number: int
    completed: bool = False
    outcome: Optional[bool] = None  # True = success, False = failed


class GameState(StrictModel):
    game_id: str
    round_number: int = 0
    phase: GamePhase = GamePhase.SETUP
    players: dict[str, PlayerState] = Field(default_factory=dict)
    alive_players: set[str] = Field(default_factory=set)
    eliminated_players: set[str] = Field(default_factory=set)
    roles: dict[str, Role] = Field(default_factory=dict)
    votes: dict[str, str] = Field(default_factory=dict)  # voter_id -> target_id
    missions: list[MissionState] = Field(default_factory=list)
    winner: Optional[str] = None  # winning player/team label, e.g. "faithful"
    winning_team: Optional[Role] = None
    # Rapid-fire finale is running (config `finale_traitors`/`finale_faithful`).
    finale: bool = False
    # Players eliminated after the finale started. With the blind finale
    # flag on, their roles stay hidden until the game has a winner.
    finale_eliminated: set[str] = Field(default_factory=set)
    # Seeded per-player disposition: "solo" wants to be the last traitor
    # standing, "team" wants the traitor faction to win together. Hidden
    # information: it reaches prompts through Goals, never through views.
    ambitions: dict[str, str] = Field(default_factory=dict)
    # Items held per player, e.g. {"alice": ["shield"]}. Private
    # knowledge: a view exposes only the viewer's own list, and the
    # holder decides whether to disclose it in discussion.
    items: dict[str, list[str]] = Field(default_factory=dict)
