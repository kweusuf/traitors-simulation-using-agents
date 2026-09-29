"""CLI (spec section 31): run, batch, replay, inspect, list-games,
snapshot, metrics, compare, diagnose.

The CLI is a thin shell around `experiments.runner`,
`experiments.replay`, `experiments.compare` and
`experiments.diagnosis`: argument parsing, progress printing, and file
lookup only.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

from simulation.experiments.compare import OUTCOME_FIELDS, compare_runs
from simulation.experiments.config import GameConfig, load_config
from simulation.experiments.diagnosis import build_diagnosis
from simulation.experiments.ledger import is_holdout_seed
from simulation.experiments.quality import analyse as analyse_quality
from simulation.experiments.replay import ReplayState, load_events, render_transcript
from simulation.experiments.runner import GameRunner, model_identity
from simulation.experiments.season import (
    benchmark_run,
    load_season,
    render_benchmark,
)
from simulation.persistence.database import Database
from simulation.persistence.event_log import Event, EventType
from simulation.persistence.repositories import SnapshotRepository

DEFAULT_RUNS_DIR = "runs"
DEFAULT_DB = "runs/simulation.db"
DEFAULT_SEASON = "configs/seasons/the-traitors-uk-s01.yaml"


# ----------------------------------------------------------------------
# Progress printing (spec section 43)
# ----------------------------------------------------------------------


def make_progress_printer():
    """Print the spec section 43 style progress lines as events stream."""

    def observe(event: Event) -> None:
        if event.type is EventType.GAME_STARTED:
            print(f"Starting game {event.game_id}")
            print(f"Players: {len(event.payload.get('players', []))}")
            print(f"Traitors: {event.payload.get('traitors')}")
        elif event.type is EventType.ROUND_STARTED:
            print(f"\nRound {event.round}")
        elif event.type is EventType.PHASE_STARTED:
            phase = str(event.payload.get("phase", "")).replace("_", " ").title()
            print(f"  {phase}")
        elif event.type is EventType.PLAYER_ELIMINATED:
            method = event.payload.get("method", "")
            suffix = " (night)" if method == "night" else ""
            print(f"  Eliminated: {event.actor}{suffix}")
        elif event.type is EventType.ROLE_RECRUITED:
            print(f"  Recruited: {event.actor} (by {event.payload.get('by', '?')})")
        elif event.type is EventType.RECRUIT_CHOICE_MADE:
            print(f"  Traitors choose: {event.payload.get('choice', '?')} tonight")
        elif event.type is EventType.RECRUIT_OFFERED:
            print(f"  Recruit offer to {event.payload.get('target', '?')}")
        elif event.type is EventType.RECRUIT_ACCEPTED:
            print(f"  Recruit accepted: {event.actor}")
        elif event.type is EventType.RECRUIT_DECLINED:
            print(f"  Recruit declined: {event.actor}")
        elif event.type is EventType.ULTIMATUM_ISSUED:
            print(f"  Ultimatum: {event.actor} murders {event.payload.get('target', '?')}")
        elif event.type is EventType.FINALE_STARTED:
            traitors = len(event.payload.get("traitors", []))
            faithful = len(event.payload.get("faithful", []))
            print(f"\nFinale: rapid fire voting ({traitors} traitors, {faithful} faithful)")
        elif event.type is EventType.VOTE_TIE:
            print("  Vote tied: nobody eliminated")
        elif event.type is EventType.NOMINATION_TALLY:
            nominees = event.payload.get("nominees", [])
            print(f"  Nominations tallied, facing the room: {', '.join(nominees)}")
        elif event.type is EventType.REVOTE_CALLED:
            targets = event.payload.get("targets", list(event.targets))
            print(f"  Revote: {' and '.join(targets)} must be chosen between")
        elif event.type is EventType.REVOTE_RESOLVED:
            if event.payload.get("tie"):
                print("  Revote tied again: nobody eliminated")
            else:
                target = (event.payload.get("top") or ["?"])[0]
                print(f"  Revote banished: {target}")
        elif event.type is EventType.TRAITOR_KILL:
            target = event.targets[0] if event.targets else "?"
            print(f"  Traitors selected: {target}")
        elif event.type is EventType.ACTION_REJECTED:
            reason = str(event.payload.get("reason", "")).replace("\n", " ")
            if len(reason) > 100:
                reason = reason[:97] + "..."
            print(f"  {event.actor}: action rejected ({reason})")
        elif event.type is EventType.GAME_ENDED:
            print("\nGame complete.")

    return observe


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def personas_dir_for(config_path: str | Path) -> Optional[Path]:
    """configs/traitors/x.yaml -> configs/personas (when it exists)."""
    candidate = Path(config_path).resolve().parent.parent / "personas"
    return candidate if candidate.is_dir() else None


def open_run_dir(game_id: str, runs_dir: str) -> Path:
    path = Path(runs_dir) / game_id
    if not path.is_dir() or not (path / "events.jsonl").exists():
        available = sorted(
            str(p.parent.name) for p in Path(runs_dir).glob("*/events.jsonl")
        )
        raise ValueError(
            f"unknown game '{game_id}' in {runs_dir}/; available: {available}"
        )
    return path


def load_state(game_id: str, runs_dir: str) -> tuple[ReplayState, list[Event]]:
    run_dir = open_run_dir(game_id, runs_dir)
    events = load_events(run_dir / "events.jsonl")
    return ReplayState.from_events(events), events


def load_run_metrics(game_id: str, runs_dir: str) -> dict:
    """Read one finished run's metrics.json (compare and diagnose)."""
    path = Path(runs_dir) / game_id / "metrics.json"
    if not path.is_file():
        raise ValueError(
            f"no metrics.json for '{game_id}' in {runs_dir}/; "
            "the run has not finished yet"
        )
    return json.loads(path.read_text(encoding="utf-8"))


def _load_config(args: argparse.Namespace) -> GameConfig:
    config = load_config(args.config)
    if getattr(args, "provider", None):
        config.llm.provider = args.provider
    if getattr(args, "base_url", None):
        config.llm.base_url = args.base_url
    if getattr(args, "model", None):
        config.llm.model = args.model
    return config


def _open_db(args: argparse.Namespace) -> Database:
    db_path = getattr(args, "db", DEFAULT_DB)
    if db_path != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    return Database(db_path)


# ----------------------------------------------------------------------
# Commands
# ----------------------------------------------------------------------


def cmd_run(args: argparse.Namespace) -> int:
    config = _load_config(args)
    model, _ = model_identity(config.llm)
    print(f"Model: {model}")

    with _open_db(args) as db:
        runner = GameRunner(
            config,
            runs_dir=args.runs_dir,
            db=db,
            personas_dir=personas_dir_for(args.config),
        )
        observer = None if args.quiet else make_progress_printer()
        result = runner.run(
            game_id=args.game_id, seed=args.seed, observer=observer
        )

    print(f"\nWinner: {result.winner} after {result.rounds} round(s)")
    print("Saved:")
    for name in result.artifacts:
        print(f"  {result.run_dir}/{name}")
    return 0


def cmd_batch(args: argparse.Namespace) -> int:
    config = _load_config(args)
    model, _ = model_identity(config.llm)
    print(f"Model: {model}  Games: {args.games}")

    with _open_db(args) as db:
        runner = GameRunner(
            config,
            runs_dir=args.runs_dir,
            db=db,
            personas_dir=personas_dir_for(args.config),
        )
        observer = None if args.quiet else make_progress_printer()
        results = runner.run_batch(
            args.games, base_seed=args.seed, observer=observer
        )

    print("\nBatch summary:")
    for result in results:
        print(
            f"  {result.game_id}  seed={result.seed}  "
            f"rounds={result.rounds}  winner={result.winner}"
        )
    wins: dict[str, int] = {}
    for result in results:
        wins[result.winner or "none"] = wins.get(result.winner or "none", 0) + 1
    print(f"  totals: {wins}")
    return 0


def cmd_replay(args: argparse.Namespace) -> int:
    state, events = load_state(args.game_id, args.runs_dir)
    if args.json:
        print(
            json.dumps(
                {
                    "game_id": state.game_id,
                    "players": state.players,
                    "roles": {pid: role.value for pid, role in state.roles.items()},
                    "alive": sorted(state.alive),
                    "eliminated": [
                        {"round": e.round, "player": e.player, "method": e.method}
                        for e in state.eliminated
                    ],
                    "winner": state.winner,
                    "rounds": state.rounds,
                    "finale": state.finale,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    print(f"Game {state.game_id}")
    print(f"Players: {', '.join(state.players)}")
    print(f"Rounds played: {state.rounds}")
    if state.eliminated:
        print("Eliminations:")
        for record in state.eliminated:
            print(f"  {record.round}) {record.player} ({record.method})")
    print(f"Winner: {state.winner}")
    if args.transcript:
        print("\n" + render_transcript(events).rstrip())
    return 0


def cmd_inspect(args: argparse.Namespace) -> int:
    state, _ = load_state(args.game_id, args.runs_dir)
    view = state.project(args.agent)
    print(view.render())
    if view.public_transcript:
        print("\nPublic transcript:")
        for message in view.public_transcript:
            print(f"  [{message.round_number}] {message.sender_id}: {message.content}")
    if view.private_conversations:
        print("\nPrivate conversations:")
        for message in view.private_conversations:
            peers = ", ".join(
                "you" if r == view.agent_id else r for r in message.recipients
            )
            print(
                f"  [{message.round_number}] {message.sender_id} -> {peers}: "
                f"{message.content}"
            )
    return 0


def cmd_snapshot(args: argparse.Namespace) -> int:
    open_run_dir(args.game_id, args.runs_dir)  # fail early on unknown game
    with _open_db(args) as db:
        repo = SnapshotRepository(db)
        rounds = [r for r, _ in repo.list_rounds(args.game_id)]
        if args.round not in rounds:
            raise ValueError(
                f"no snapshot for round {args.round} in '{args.game_id}'; "
                f"available rounds: {sorted(set(rounds))}"
            )
        state = None
        phases = [p for r, p in repo.list_rounds(args.game_id) if r == args.round]
        wanted = [args.phase] if args.phase else phases
        for phase in wanted:
            state = repo.get(args.game_id, args.round, phase)
            if state is not None:
                print(f"Snapshot {args.game_id} round {args.round} phase {phase}")
                break
        if state is None:
            raise ValueError(
                f"no snapshot for round {args.round} phase '{args.phase}' "
                f"in '{args.game_id}'; available phases: {phases}"
            )
    print(f"  Phase: {state.phase.value}")
    print(f"  Alive: {', '.join(sorted(state.alive_players))}")
    print(f"  Eliminated: {', '.join(sorted(state.eliminated_players)) or 'none'}")
    print(f"  Votes: {state.votes or '{}'}")
    print(f"  Winner: {state.winner or 'none'}")
    return 0


def cmd_metrics(args: argparse.Namespace) -> int:
    """The360 view of one run: LLM operations plus quality signals."""
    run_dir = open_run_dir(args.game_id, args.runs_dir)
    metrics_path = run_dir / "metrics.json"

    if not metrics_path.exists():
        # An unfinished run has no metrics.json; never invent one, but
        # its quality signals can still be read straight from events.
        if not args.recompute:
            raise ValueError(
                f"no metrics.json in {run_dir}/; the run has not finished yet"
            )
        quality = analyse_quality(load_events(run_dir / "events.jsonl"))
        print(f"{args.game_id} never finished, so metrics.json is missing.")
        print("Quality computed from events.jsonl (not stored):")
        print(json.dumps(quality, indent=2, sort_keys=True))
        return 0

    if args.recompute:
        events = load_events(run_dir / "events.jsonl")
        metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
        metrics["quality"] = analyse_quality(events)
        metrics_path.write_text(
            json.dumps(metrics, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(f"recomputed quality in {metrics_path}")

    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))

    if args.json:
        print(json.dumps(metrics, indent=2, sort_keys=True))
        return 0

    llm = metrics.get("llm", {})
    quality = metrics.get("quality", {})
    print(f"Game {args.game_id}  experiment {metrics.get('experiment_id', '?')}")
    print(
        f"Model: {metrics.get('model', '?')}  seed {metrics.get('random_seed', '?')}"
    )
    print(
        f"Outcome: winner={metrics.get('winner')} rounds={metrics.get('rounds')} "
        f"finale={metrics.get('finale')} solo={metrics.get('solo_traitor_win')} "
        f"duration={metrics.get('duration_seconds')}s"
    )

    print("LLM operations:")
    print(
        f"  calls: {llm.get('calls')} (provider ok: {llm.get('provider_calls')}), "
        f"failed: {llm.get('failed_calls')}, transport retries: {llm.get('transport_retries')}, "
        f"parse retry calls: {llm.get('parse_retry_calls')}"
    )
    print(
        f"  tokens: in={llm.get('input_tokens')} out={llm.get('output_tokens')} "
        f"total={llm.get('total_tokens')} "
        f"(reported on {llm.get('token_reported_calls')} of {llm.get('calls')} calls, "
        f"avg in={llm.get('avg_input_tokens')} avg out={llm.get('avg_output_tokens')})"
    )
    latency = llm.get("latency_ms", {})
    print(
        "  latency ms: "
        f"avg={latency.get('avg')} p50={latency.get('p50')} "
        f"p95={latency.get('p95')} max={latency.get('max')} "
        f"total={latency.get('total')}"
    )
    for action, stats in (llm.get("by_action_type") or {}).items():
        print(
            f"    {action}: calls={stats['calls']} failed={stats['failed']} "
            f"in={stats['input_tokens']} out={stats['output_tokens']}"
        )
    for failure in (llm.get("failures") or [])[:5]:
        print(f"    failed turn: {failure['agent_id']} {failure['action_type']}: {failure['error'][:80]}")

    if not quality:
        print("Quality: not recorded for this run (recompute with --recompute)")
        return 0
    hallucination = quality.get("hallucination", {})
    secrecy = quality.get("secrecy", {})
    diversity = quality.get("diversity", {})
    parsing = quality.get("parsing", {})
    print(
        f"Quality: hallucination_score={quality.get('hallucination_score')} "
        f"over {quality.get('messages_checked')} messages"
    )
    print(
        f"  hallucination: contradictions={hallucination.get('messages_with_contradictions')} "
        f"(fake eliminations={hallucination.get('fabricated_eliminations')}, "
        f"alive after elim={hallucination.get('alive_after_elimination')}, "
        f"invented rounds={hallucination.get('invented_rounds')})"
    )
    print(
        f"  secrecy: traitor declarations={secrecy.get('traitor_public_declarations')} "
        f"affiliation phrases={secrecy.get('traitor_affiliation_phrases')} "
        f"named teammates={secrecy.get('traitor_named_teammates')} "
        f"faithful claiming traitor={secrecy.get('false_role_claims')}"
    )
    print(
        f"  diversity: duplicates={diversity.get('duplicate_messages')} "
        f"rate={diversity.get('duplicate_rate')} "
        f"duplication_score={diversity.get('duplication_score')} "
        f"distinct={diversity.get('distinct_texts')}"
    )
    similarity = quality.get("speech_similarity", {})
    if similarity:
        content = similarity.get("content_words", {})
        phrasing = similarity.get("phrasing", {})
        pair = content.get("max_pair") or ["-", "-"]
        print(
            f"  speech similarity: mean content={content.get('mean')} "
            f"phrasing={phrasing.get('mean')} over {similarity.get('pairs')} pairs; "
            f"most alike: {pair[0]} ~ {pair[1]} "
            f"(content {content.get('max')}, phrasing {phrasing.get('max')})"
        )
    print(
        f"  parsing: rejected={parsing.get('rejected_actions')} "
        f"unparseable={parsing.get('unparseable_actions')}"
    )
    return 0


def _fmt(value) -> str:
    """Gate table cell: '-' for a missing value, else a fixed width."""
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def _fmt_delta(value) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:+.4f}"
    return f"{value:+d}"


def cmd_compare(args: argparse.Namespace) -> int:
    """Gate a candidate run against a baseline run, one dimension each."""
    baseline = load_run_metrics(args.baseline_game, args.runs_dir)
    candidate = load_run_metrics(args.candidate_game, args.runs_dir)
    result = compare_runs(baseline, candidate)
    seed = candidate.get("random_seed")
    holdout = isinstance(seed, int) and is_holdout_seed(seed)
    payload = {
        "baseline": {"game_id": args.baseline_game,
                     "seed": baseline.get("random_seed")},
        "candidate": {"game_id": args.candidate_game, "seed": seed},
        "holdout_candidate": holdout,
        **result,
    }

    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0 if result["passed"] else 1

    print(
        f"Comparing {args.baseline_game} (baseline) vs "
        f"{args.candidate_game} (candidate)\n"
    )
    print(
        f"  {'metric':<36}{'baseline':>10} {'candidate':>10} {'delta':>10}  "
        f"{'rule':<22}result"
    )
    for gate in result["gates"]:
        if gate["skipped"]:
            verdict = "SKIP"
        else:
            verdict = "PASS" if gate["passed"] else "FAIL"
        note = f" ({gate['note']})" if gate["note"] else ""
        print(
            f"  {gate['metric']:<36}{_fmt(gate['baseline']):>10} "
            f"{_fmt(gate['candidate']):>10} {_fmt_delta(gate['delta']):>10}  "
            f"{gate['rule']:<22}{verdict}{note}"
        )

    print("\nOutcome (information only, never gated):")
    for field in OUTCOME_FIELDS:
        entry = result["outcome"][field]
        print(f"  {field}: {entry['baseline']} -> {entry['candidate']}")

    if holdout:
        print(
            f"\nWARNING: candidate seed {seed} is a holdout seed; holdout "
            "runs must not be used to promote a change."
        )
    if result["passed"]:
        print("\nAll gates pass.")
        return 0
    print(f"\n{result['failed']} gate(s) FAILED; do not promote this candidate.")
    return 1


def cmd_diagnose(args: argparse.Namespace) -> int:
    """Write and print a deterministic diagnosis for one finished run."""
    run_dir = open_run_dir(args.game_id, args.runs_dir)
    metrics_path = run_dir / "metrics.json"
    metrics = (
        json.loads(metrics_path.read_text(encoding="utf-8"))
        if metrics_path.exists()
        else {}  # an unfinished run still has events worth diagnosing
    )
    report = build_diagnosis(
        args.game_id, load_events(run_dir / "events.jsonl"), metrics
    )
    (run_dir / "diagnosis.md").write_text(report, encoding="utf-8")
    print(report, end="")
    return 0


def cmd_benchmark(args: argparse.Namespace) -> int:
    """Score one finished run against a real season's ground truth."""
    run_dir = open_run_dir(args.game_id, args.runs_dir)
    season = load_season(args.season)
    events = load_events(run_dir / "events.jsonl")
    result = benchmark_run(events, season)

    if args.json:
        print(
            json.dumps(
                {"game_id": args.game_id, "season_id": season.id, **result},
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    # The file carries the provenance (the season name and its sources);
    # the terminal gets the same table without that block.
    print(render_benchmark(args.game_id, season, result, provenance=False), end="")
    report = render_benchmark(args.game_id, season, result, provenance=True)
    (run_dir / "benchmark.md").write_text(report, encoding="utf-8")
    return 0


def cmd_list_games(args: argparse.Namespace) -> int:
    rows = []
    for events_path in sorted(Path(args.runs_dir).glob("*/events.jsonl")):
        run_dir = events_path.parent
        metrics_path = run_dir / "metrics.json"
        if metrics_path.exists():
            metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
            rows.append(
                (
                    run_dir.name,
                    str(metrics.get("random_seed", "")),
                    str(metrics.get("winner", "")),
                    str(metrics.get("rounds", "")),
                    str(metrics.get("model", "")),
                    str(metrics.get("duration_seconds", "")),
                )
            )
        else:
            events = load_events(events_path)
            state = ReplayState.from_events(events)
            rows.append((run_dir.name, "", str(state.winner), str(state.rounds), "", ""))

    if not rows:
        print(f"No games in {args.runs_dir}/")
        return 0
    headers = ("GAME", "SEED", "WINNER", "ROUNDS", "MODEL", "SECONDS")
    print(
        f"{headers[0]:<14}{headers[1]:>7}{headers[2]:>10}{headers[3]:>8}"
        f"  {headers[4]:<28}{headers[5]:>9}"
    )
    for game_id, seed, winner, rounds, model, seconds in rows:
        print(
            f"{game_id:<14}{seed:>7}{winner:>10}{rounds:>8}  {model:<28}{seconds:>9}"
        )
    return 0


# ----------------------------------------------------------------------
# Parser
# ----------------------------------------------------------------------


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--runs-dir", default=DEFAULT_RUNS_DIR, help="run artifacts root")
    parser.add_argument("--db", default=DEFAULT_DB, help="SQLite path (or :memory:)")
    parser.add_argument("--quiet", action="store_true", help="no live progress output")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="simulation",
        description="Configuration-driven LLM social simulation framework",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="play one game from a config file")
    run.add_argument("config", help="path to a YAML game config")
    run.add_argument("--seed", type=int, default=None, help="override the config seed")
    run.add_argument("--game-id", default=None, help="explicit game id")
    run.add_argument(
        "--provider", default=None, choices=["ollama", "fake"], help="override llm.provider"
    )
    run.add_argument("--model", default=None, help="override llm.model")
    run.add_argument(
        "--base-url",
        default=None,
        help="override llm.base_url (e.g. a remote Ollama at http://10.0.0.5:11434)",
    )
    _add_common(run)
    run.set_defaults(func=cmd_run)

    batch = sub.add_parser("batch", help="play N games from a config file")
    batch.add_argument("config", help="path to a YAML game config")
    batch.add_argument("--games", type=int, required=True, help="number of games")
    batch.add_argument("--seed", type=int, default=None, help="base seed for the batch")
    batch.add_argument(
        "--provider", default=None, choices=["ollama", "fake"], help="override llm.provider"
    )
    batch.add_argument("--model", default=None, help="override llm.model")
    batch.add_argument(
        "--base-url",
        default=None,
        help="override llm.base_url (e.g. a remote Ollama at http://10.0.0.5:11434)",
    )
    _add_common(batch)
    batch.set_defaults(func=cmd_batch)

    replay = sub.add_parser("replay", help="reconstruct a game from its events")
    replay.add_argument("game_id")
    replay.add_argument("--runs-dir", default=DEFAULT_RUNS_DIR)
    replay.add_argument("--json", action="store_true", help="dump reconstructed state")
    replay.add_argument("--transcript", action="store_true", help="also print the narrative")
    replay.set_defaults(func=cmd_replay)

    inspect = sub.add_parser("inspect", help="show one agent's visible slice")
    inspect.add_argument("game_id")
    inspect.add_argument("--agent", required=True, help="player id, e.g. alice")
    inspect.add_argument("--runs-dir", default=DEFAULT_RUNS_DIR)
    inspect.set_defaults(func=cmd_inspect)

    snapshot = sub.add_parser("snapshot", help="show a stored state snapshot")
    snapshot.add_argument("game_id")
    snapshot.add_argument("--round", type=int, required=True)
    snapshot.add_argument("--phase", default=None)
    snapshot.add_argument("--runs-dir", default=DEFAULT_RUNS_DIR)
    snapshot.add_argument("--db", default=DEFAULT_DB)
    snapshot.set_defaults(func=cmd_snapshot)

    metrics = sub.add_parser(
        "metrics", help="show a run's LLM operations and quality signals"
    )
    metrics.add_argument("game_id")
    metrics.add_argument("--runs-dir", default=DEFAULT_RUNS_DIR)
    metrics.add_argument(
        "--recompute",
        action="store_true",
        help="rebuild the quality block from events.jsonl (for older runs)",
    )
    metrics.add_argument("--json", action="store_true", help="dump raw metrics.json")
    metrics.set_defaults(func=cmd_metrics)

    listing = sub.add_parser("list-games", help="list games under the runs directory")
    listing.add_argument("--runs-dir", default=DEFAULT_RUNS_DIR)
    listing.set_defaults(func=cmd_list_games)

    compare = sub.add_parser(
        "compare", help="gate a candidate run against a baseline run"
    )
    compare.add_argument("baseline_game")
    compare.add_argument("candidate_game")
    compare.add_argument("--runs-dir", default=DEFAULT_RUNS_DIR)
    compare.add_argument(
        "--json", action="store_true", help="dump the structured result"
    )
    compare.set_defaults(func=cmd_compare)

    diagnose = sub.add_parser(
        "diagnose", help="write and print a run's diagnosis.md report"
    )
    diagnose.add_argument("game_id")
    diagnose.add_argument("--runs-dir", default=DEFAULT_RUNS_DIR)
    diagnose.set_defaults(func=cmd_diagnose)

    benchmark = sub.add_parser(
        "benchmark", help="score a run against a real season's ground truth"
    )
    benchmark.add_argument("game_id")
    benchmark.add_argument(
        "--season", default=DEFAULT_SEASON, help="ground-truth season YAML"
    )
    benchmark.add_argument("--runs-dir", default=DEFAULT_RUNS_DIR)
    benchmark.add_argument(
        "--json", action="store_true", help="dump the structured result"
    )
    benchmark.set_defaults(func=cmd_benchmark)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
