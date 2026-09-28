# Audit: season replays against UK Series 1 (rounds, conflict, ties)

**Date:** 2026-09-28
**Status:** findings recorded, fix plan approved and in progress
**Ground truth:** `configs/seasons/the-traitors-uk-s01.yaml`
**Runs audited:** game-001 through game-009 (season replays are game-008 and game-009)

---

## 1. Run inventory

| Run | Players | Traitors | Rounds | Winner | Finale | End votes | Ties | Model |
|-----|---------|----------|--------|--------|--------|-----------|------|-------|
| game-001 | 6 | 2 | 2 | traitor | no | 0 | 0 | fake |
| game-003 | 6 | 2 | 2 | faithful | no | 0 | 0 | ollama |
| game-007 | 21 | 3 | 12 | traitor | yes | 0 | 3 | ollama |
| game-008 | 22 | 3 | 12 | traitor | no | 0 | 2 | ollama |
| game-009 | 22 | 3 | 11 | traitor | no | 0 | 3 | ollama |

game-002, game-004, game-005, game-006 are partial or debug runs without
full artifacts. game-008 and game-009 pin the real Series 1 traitors
(wilf, amanda, alyssa) and use the UK S01 cast personas.

Benchmark alignment: game-008 **0.3031**, game-009 **0.4218**. Both score
0.0 on the `outcome` component (weight 0.18) because the real season was
a faithful win and both runs are traitor wins. Neither run reached
`FINALE_STARTED`, so the `finale` component is skipped in both reports.
The parity fix committed after game-009 addresses the finale gap going
forward: parity closed game-009 at three traitors against three faithful
with six alive, one player short of the final five.

Real season facts used as the bar: 22 contestants over 12 episodes,
banishment tallies almost always split (17-1-1, 10-6-1, 7-6-2-1,
7-4-1-1-1, 6-5-2-1, 4-2-1-1), one recruitment declined (alex, episode 7),
one accepted by ultimatum (kieran, episode 11), finale at five with a
2-2 end-vote tie forcing another banishment, then wilf out 3-1, then a
3-0 end vote and a faithful win.

---

## 2. Conflict audit

### Traitor night: concurrent blind turns, no deliberation

Each traitor submits one `TRAITOR_MESSAGE` and one `TRAITOR_KILL`
concurrently (`src/simulation/environments/traitors/phases.py`,
`TraitorNightPhase`). Nobody reads anyone else's council message before
choosing, so disagreement cannot be argued out or even noticed in fiction.

Evidence from game-009 `TRAITOR_KILL` payloads: rounds 2, 3, 4, 6, 7 and
10 show three traitors picking three different victims (1-1-1 splits),
resolved silently by earliest-submission order. Council texts in the
database confirm the cause: all three traitors parrot the same public
transcript talking point (imran is the loudest voice), two of them nearly
verbatim. Three agents summarizing the same room is not three schemers
clashing over candidates. The prompt tells them to argue the kill, but
with concurrent blind turns there is nothing to argue against.

### Round table: bandwagons without confrontation

Votes do concentrate (top share 0.31 to 0.56 across game-009
banishments), so bandwagons form. The talk itself is clash-free: 17 of
318 public messages contain any accusation-family language, and the
samples are hedged meta-commentary (wondering whether volume is being
mistaken for substance) rather than direct accusations with reasons.

Two structural causes. The secrecy hard rule forbids naming anyone as a
traitor in public, which is correct for identity protection but also bans
the show's core move, the direct accusation. And all turns are
concurrent, so nobody can rebut, pile on, or react visibly to what
someone just said.

### Private chats: public echo, not 1-on-1s

186 private messages across 69 pairs looks healthy, but samples show
messages addressed to one player quoting a third player's public post
(for example theo writing to hannah about something maddy said). The
prompt says to continue something the pair actually said, but nothing in
the prompt shows the pair's own history, so the model reaches for the
public transcript instead. No subgroup formation, no bloc coordination,
no private feuds are observable.

### Speech metrics confirm conformity

Phrasing similarity mean 0.84 to 0.86 with a max pair of 0.99;
content-words mean about 0.52. Everyone sounds like everyone.

---

## 3. Tie audit: no tie-breaker except the finale

Every decision point audited:

| Decision | Tie behavior | Location |
|----------|--------------|----------|
| Round-table banishment | Nobody eliminated, round wasted; `VOTE_TIE` emitted, game moves to the night | `game_engine.py`, `resolve_votes` |
| Traitor-night kill | Earliest submission wins silently; 1-1-1 splits never register as ties | `game_engine.py`, `resolve_night` |
| Recruit-or-murder choice | Falls to murder, unrecorded as a disagreement | `game_engine.py`, `resolve_recruit_choice` |
| Recruit target | Earliest submission wins | `game_engine.py`, `resolve_recruit_offer` |
| Rapid-fire finale | Ties loop until `finale_max_votes`, then the `round_limit_winner` fallback | `tests/unit/test_finale.py` |
| Endgame end-or-banish vote | Any single `banish` forces another round (matches the show) | `game_engine.py`, `resolve_end_vote` |

Game-009 had three round-table ties (rounds 4, 6, 9, including a
three-way). Each one burned the day's banishment: the next event is a
night murder, the tied suspects walk free with no revote, no narrowed
shortlist, no recorded suspicion. On the show a tie triggers a revote
between the tied players; Series 1 episode 12's 2-2 fire-pit tie forced
the extra banishment that caught wilf. The engine treats the show's most
dramatic mechanism as a no-op.

The traitor-night earliest-submission rule is worse than random:
submission order is player-id order, so in every 1-1-1 split the
alphabetically first traitor's pick wins deterministically, invisible to
everyone including the metrics.

---

## 4. Further format deviations

- Faithfuls almost never catch a traitor. Game-009: 7 banishments, 1
  traitor (hit rate 0.14 against the season's 0.36). Suspicion never
  compounds because votes carry no reasoning and ties erase suspicion.
- No nomination phase. The show's round table has open nomination and
  debate before voting; the engine votes cold after one polite message
  each.
- Missions are stubs: always succeed, no shield economy, no information
  leak.
- Murder cadence is close (quiet rounds and shields are implemented) but
  traitor kills look pattern-free rather than strategic.

---

## 5. Fix plan (approved 2026-09-28)

Deterministic host actor plus a turn budget. The host is engine code, not
an LLM player: it opens debate, warns when time is short, closes debate,
calls votes and runs tie revotes. An LLM host voice may be added later as
flavor only.

Config additions in `GameSettings` (all default-off except host control
of existing phases, so current configs keep current behavior):
`host_enabled`, `discussion_budget`, `warning_turns`, `revote_enabled`,
`nomination_enabled`.

- Phase 1: host plus debate-clock framework (open turns, host warning,
  one closing turn each, forced vote; `HOST_WARNING` and `DEBATE_CLOSED`
  events; one timer line in prompts).
- Phase 2: round-table nomination, rebuttal, revote (nomination tally,
  defense messages, timed rebuttal, restricted revote on ties; secrecy
  rule carve-out so naming a suspect with reasons is legal while
  claiming role knowledge is not).
- Phase 3: sequential traitor council (round 1 proposals, round 2
  hold-or-switch with reasons, majority of final picks wins).
- Phase 4: private chats with pair history (pair's own recent messages in
  the prompt, per-pair counters in metrics).
- Phase 5: conflict personas plus clash metrics (assertiveness and
  tunnel-vision weights from Series 1 ground truth; accusation rate,
  revote stubbornness, council switch rate, traitor cohesion).
