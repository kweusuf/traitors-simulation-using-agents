# The Traitors: real mechanics compendium and framework drift audit

Date: 2026-11-19
Scope: how the television game actually works, how this repo implements
it, and where the two diverge. Written to answer one question: why do the
faithful in this simulation "win" at round 5 instead of by catching the
traitors? Short answer at the bottom (`§5`); it is a round-limit default,
not a recruiting artefact.

Sources for Part 1: BBC/Wikipedia (UK series format and endgame), The
Mirror, TheJournal.ie, Peacock, traitorbase.com, the Fandom wiki
(Recruitment, Faithfuls), and a Radio Times endgame explainer. Links are
at the end.

---

## Part 1 — Compendium of the real game

### 1.1 Structure and roles

- Roughly 20–24 contestants arrive at an isolated venue (a Scottish
  castle for the UK series) and live there for the run.
- The host secretly selects **3–4 Traitors**; everyone else is a
  **Faithful**. Selection happens at the first round table (a touch on
  the shoulder in the UK version).
- The audience knows who the Traitors are; the Faithful know neither
  their **identities nor their number**.
- The Traitors take an oath: they may never reveal that they are a
  Traitor, nor expose a fellow Traitor. They *may* accuse a fellow
  Traitor in the guise of being Faithful (throwing a teammate under the
  bus is legal).
- A season runs about **12 episodes**, and each episode is roughly one
  day/night cycle ending in a round table. So a season is ~10–13
  banishment cycles, not a handful.

### 1.2 The daily loop

Each cycle: **mission (day) → round table / banishment (evening) →
murder (night) → breakfast reveal (next morning)**.

### 1.3 Missions

- Daytime challenges (physical, mental, puzzle) that **add money to the
  prize pot** (UK cap £120,000; US up to $250,000).
- Missions occasionally award a **Shield** (immunity from the next
  murder, *not* from banishment) and sometimes a **Dagger** (the
  holder's vote counts twice at the next round table). Either expires
  if unused.
- Some missions have a defensive payoff: e.g. in UK S4 a mission whose
  gold target was met **prevented the night's murder entirely**.
- Missions are strategic because of the *social* information around
  them — who takes control, who protects whom, who wins an advantage —
  not because they expose a Traitor directly. Shield ownership can be
  public or private depending on the season; hiding a shield is common.

### 1.4 The round table and banishment

- The group debates its suspicions, then **each player votes privately**;
  votes are revealed one at a time in turn.
- The player with the most votes is **banished** and leaves immediately,
  **revealing their affiliation** (Faithful or Traitor) — the standard
  rule (recent final episodes sometimes withhold the final reveal).
- A banished Traitor's reveal is the Faithful's main hard information.
  A wrong vote removes an ally and hands the Traitors numerical control.
- **Ties:** a **run-off vote** between the tied players (the tied players
  do not vote); if the run-off also ties, the banishment is decided
  **by chance** (chests of chance).

### 1.5 The murder

- At night the Traitors meet (the turret) and choose **one Faithful to
  murder**. They **cannot murder a Traitor** (though they can help banish
  one at the round table).
- The victim leaves at once; the group only discovers **who** at
  breakfast the next morning. Because Traitors cannot be murdered, a
  murder victim is always a Faithful, so the reveal carries little
  hard information — the drama is in *who* was removed and *why*.

### 1.6 Recruitment

- On the night after a Traitor is banished, the surviving Traitors may
  **choose to recruit (seduce) a Faithful instead of murdering**. The
  offer is usually a letter; the invited Faithful may **decline**, and a
  failed recruitment typically **wastes that night** (no murder).
- If **only one Traitor remains**, the offer becomes an **ultimatum**:
  the chosen Faithful either joins or is **murdered on the spot**.
- Meta note: modern players often *avoid* recruiting unless forced —
  a recruit may betray them, a murder removes a threat now, and
  recruiting resets a comfortable low-suspicion board.

### 1.7 Endgame (the real conclusion)

- There is **no parity / "numbers" win**. The game does not end merely
  because Traitors equal Faithful.
- Normal play stops near the **final five**. The survivors gather at a
  fire (the "Fire of Truth") and each chooses **End the Game** or
  **Banish Again**; the choice is shown publicly (pouches into the fire).
- The game ends only when **everyone chooses End** (unanimous) **or only
  two players remain**. Any "Banish Again" triggers an immediate further
  banishment and the choice repeats.
- Then survivors **reveal their roles**: if **all survivors are Faithful**
  they share the pot; if **any Traitor remains**, the surviving Traitor(s)
  take the pot (a lone Traitor wins alone).
- **Blind finale:** in recent seasons the final round-table banishment's
  role is withheld until the game ends, so the survivors cannot confirm
  whether the board is clean before choosing to end.

### 1.8 Things easy to miss

- Traitors win by **being in the final surviving group at the reveal**,
  not by reaching numerical parity mid-game.
- Faithful can banish every visible Traitor and still **lose to a
  recruit** they did not identify.
- A wasted night (failed recruitment, or a mission that blocked the
  murder) is a real tempo swing.
- Shields create deliberate misinformation: a shielded player may bait a
  murder, or hide that they were protected.

---

## Part 2 — What this repo implements

The engine is config-driven (`GameConfig` in
`src/simulation/experiments/config.py`). A round is a configurable list
of phases run by `PhaseEngine`
(`src/simulation/engine/phase_engine.py`).

### 2.1 Phases present

Default `phases` (config.py:402): `mission, public_discussion,
private_chat, round_table, voting, elimination, traitor_night`. Optional
`end_vote` exists for the finale.

| Show beat | Implementation |
| --- | --- |
| Mission | `MissionPhase` always calls `start_mission` + `complete_mission(success=True)` (phases.py:168). No money, no failure, no social sub-game. |
| Round table | `RoundTablePhase` = debate; optional `nomination_enabled` adds one `ACCUSE` each + `REBUT` from top `nomination_keep` (phases.py:199). |
| Voting | `VotingPhase` collects one `VOTE` each; optional `revote_enabled` runs a restricted run-off (phases.py:348). |
| Elimination | `EliminationPhase` resolves the tally; a banished Traitor may be asked to `RECRUIT` (phases.py:420). |
| Traitor night | `TraitorNightPhase` = council `TRAITOR_MESSAGE`, optional `on_trial` shortlist, majority `TRAITOR_KILL`; optional sequential `council_deliberation`; optional recruit-choice night (phases.py:443). |
| Endgame | `EndVotePhase` (`end`/`banish`) exists and runs only when `finale_*` is configured (phases.py:403). |

### 2.2 Roles, secrecy, reveals

- `Role` = TRAITOR/FAITHFUL (`engine/state.py`). `traitor_names` can pin
  the cast for a replay (`config.py:286`).
- `InformationProjector` gives each agent its own role, its team (if a
  Traitor), and **the roles of every eliminated player by default**
  (`communication/visibility.py:87`). So a banished player's affiliation
  is revealed, matching the show; a murdered player is trivially
  Faithful so this is not extra leakage.

### 2.3 Recruitment (implemented, on by default)

- `recruit_choice: bool = True` (config.py:53) and
  `recruit_window: bool = True` (config.py:63): on the night after a
  Traitor is banished, the Traitors vote **recruit vs murder**
  (`resolve_recruit_choice`, game_engine.py:911); a recruit night offers
  one living Faithful (`resolve_recruit_offer`), who accepts or declines
  (`_resolve_recruit_response`, game_engine.py:954). A lone Traitor's
  declined offer is an **ultimatum** (the player is murdered).
- Guards: `max_recruits: 4` (config.py:43) and a `traitor_capacity`
  ceiling (`game_engine.py:862`) — a recruit only fills a slot a
  banishment opened.
- The legacy path `recruit_on_banish` (the banished Traitor picks a
  convert before leaving) is off when `recruit_choice` is on.

### 2.4 Items and missions (implemented, off by default)

- `shield` / `dagger` / `seer` flags (config.py:279) default **False**.
- When enabled, `complete_mission` awards **one random item per round**
  (cycle shield→dagger→seer, `_award_item`, game_engine.py:1355) — the
  recipient is drawn by seeded RNG, not earned in a mission sub-game.
- Shield blocks one murder (`_night_elimination`, game_engine.py:1108);
  dagger doubles one vote (`tally_votes`, game_engine.py:823); seer
  privately checks a role (phases.py:325).

### 2.5 Win conditions and game length

`check_win` (game_engine.py:1209):

1. No Traitors alive → **Faithful**.
2. No Faithful alive → **Traitor**.
3. Endgame vote on and ≤ 2 alive → **Traitor**.
4. Endgame **not** configured and `traitors >= faithful` → **Traitor**
   ("plain parity win", game_engine.py:1231) — *the show has no such rule.*

Finale trigger (`_check_finale_trigger`, game_engine.py:1256): when
alive ≤ `finale_total`, or the exact `finale_traitors/finale_faithful`
pair is reached, `_start_finale` sets `state.finale` and the phase engine
runs the rapid-fire `FINALE_PHASES` (round_table, private_chat, voting,
elimination) or the `ENDGAME_PHASES` (+ end_vote).

**Round limit** (`PhaseEngine.run`, phase_engine.py:140, 180): the normal
loop runs at most `max_rounds`; if no winner and no finale by then,
`apply_round_limit()` declares `round_limit_winner` — **default
`faithful`** (config.py:31,34; game_engine.py:1299).

### 2.6 Effective defaults for a fresh season config

| Setting | Default | Show equivalent |
| --- | --- | --- |
| `max_rounds` | **5** | ~12 (one per episode) |
| `round_limit_winner` | **faithful** | none — the show has no timeout |
| `finale_total` / `finale_traitors`/`finale_faithful` | **0 / 0** (finale off) | final five, endgame |
| `endgame_vote` | **false** | always |
| `blind_finale_banishments` | **false** | recent seasons |
| `nomination_enabled` / `revote_enabled` | **false / false** | round-table nomination, run-off |
| `recruit_choice` / `recruit_window` | **true / true** | kill-or-recruit (close) |
| `max_recruits` | **4** | none |
| `shield` / `dagger` / `seer` | **false** | shields/daggers present |
| Parity win | **on unless finale configured** | does not exist |

---

## Part 3 — Drift analysis

Severity is about *gameplay consequence*, not code quality.

| # | Mechanic | Show | This repo (default) | Drift | Severity |
| --- | --- | --- | --- | --- | --- |
| D1 | **Game length / timeout** | ~10–13 cycles, no timeout; ends only when all Traitors gone or at the endgame | `max_rounds=5`; on timeout `round_limit_winner=faithful` | A game is cut off after 5 rounds and *awarded* to the faithful | **Critical — root cause** |
| D2 | **Endgame / Fire of Truth** | Always: final ~5, end/banish unanimous vote, reveal | Disabled by default (`finale_*=0`, `endgame_vote=false`); only UK config enables it | Most games never reach the show's real conclusion | **High** |
| D3 | **Parity win** | None — Traitors win only by surviving the reveal | `traitors >= faithful` ends the game as a Traitor win | Games can end mid-arc on a "numbers" rule the show lacks | **High** |
| D4 | **Missions** | Build money; award shields/daggers; can block a murder | **Fixed (abstracted):** successful missions bank `mission_reward` into `prize_pot` and earn the round's item; `mission_success_probability` can fail a mission; `mission_defense_rounds` blocks a murder. No played mini-game. | Money/items now real; challenge itself is seeded, not played | Medium (was High) |
| D5 | **Items (shield/dagger/seer)** | Central to suspicion and protection | Implemented but **off** by default; enabled in season configs | Protection/misdirection present in shipped configs | Medium |
| D6 | **Tie handling** | Run-off, then chance | Default tie ⇒ **nobody banished**; `revote_enabled=false` | Outcomes differ on every tie | Medium |
| D7 | **Round-table nomination/defence** | Nomination, defence, then vote | `nomination_enabled=false` by default | Talk is unstructured; no rebuttal beat | Medium |
| D8 | **Recruitment** | Kill-or-recruit after a banish; letter; decline wastes night; lone-Traitor ultimatum | `recruit_choice`+window on (good); adds `max_recruits=4` and a capacity ceiling; a parse failure can fall through to a murder | Close, but capped and slightly more forgiving | Low–Medium |
| D9 | **Murder reveal timing** | Role not announced for a murder; banishment reveals | **Fixed:** `elimination_method` recorded; projector reveals only `vote` eliminations, hides `night` murders | Aligned with the show | Low (was Low) |
| D10 | **Resume resets the round budget** | n/a (no such concept) | `PhaseEngine.run` gives a fresh `max_rounds` window from the resume point (phase_engine.py:125) | A game that crashes + resumes can exceed `max_rounds` total rounds → length depends on crash history | Medium (correctness/determinism) |

### D10 evidence (why it matters)

`nz-s01-opencode-go` had **no** `max_rounds` (default 5) and its snapshot
`config.yaml` records `max_rounds: 5`, yet its log shows **10**
`ROUND_STARTED` events and a Traitor win at round 10. It resumed twice
(attempts 2 and 3). Each resume hands the loop a fresh 5-round window from
the resume point, so the effective length is not the configured length.

---

## Part 4 — Root cause of the faithful "wins"

The two faithful wins are **not** about recruiting:

| Season | Traitors(start) | Recruits used | Traitors banished | End reason | Rounds |
| --- | --- | --- | --- | --- | --- |
| Australia S02 | 4 | **2** (gloria R2, luke_toki R4) | 1 | `round_limit` | 5 |
| US S02 | 2 | **0** | 0 | `round_limit` | 5 |
| India S01 | 3 | 0 | 0 | `elimination` | 7 |
| UK S01 | 3 | 1 | 1 | `endgame` (finale) | 13 |
| NZ S01 | 3 | 3 | 2 | `elimination` | 10 (resume-extended) |

- Australia S02 recruited twice and still "lost" at the cap; US S02
  recruited zero times and "won" with **both Traitors alive**. Recruiting
  behaviour is opposite; the result is identical.
- Both faithful wins are labelled `reason: round_limit` and fired at the
  **5th** round with Traitors still in the game
  (`surviving_traitors` non-empty). That is the timer firing and the
  default `round_limit_winner: faithful` being applied
  (`game_engine.apply_round_limit`, game_engine.py:1299), not the faithful
  detecting anyone.

**Root cause = D1 (+ D2):** `max_rounds` defaults to **5**, the finale is
**not configured**, so 5 rounds pass without an elimination victory and
the engine awards the game to the faithful by default. The Traitor-win
seasons ran longer only because India/UK set `max_rounds: 18` and NZ S01
was extended by resumes.

---

## Part 5 — Remedy (config-first, matches the show)

**Status: applied 2026-11-20.** The 10 `season_*.opencode_go.yaml`
configs carry the block below plus `mission_reward` and
`mission_success_probability`. D4 (missions/prize pot) and D9 (reveal
timing) are implemented in code, and D10's resume-budget bug is fixed.
The framework's own model defaults stay plain (parity on, finale off,
items off): dozens of tests deliberately pin those library defaults, and
the show-faithful final-five is cast-size specific, so it belongs in the
season configs rather than in every `GameConfig`. A fake-backend US S02 run
now plays 17 rounds, banks a prize pot, reaches the finale at five alive,
and ends `reason: endgame`. The full suite is green (710 tests).

The framework already supports the real rules; the season configs just
don't switch them on. For every `season_*.opencode_go.yaml` that wants to
play like the show:

```yaml
game:
  max_rounds: 18            # the show's ~12 episodes with margin
  finale_total: 5           # endgame at the final five
  finale_traitors: 2        # or the faction split you want
  finale_faithful: 3
  endgame_vote: true        # Fire of Truth: end / banish
  blind_finale_banishments: true
  revote_enabled: true      # tie -> run-off
  nomination_enabled: true  # nomination + defence before the vote
  shield: true              # mission rewards the show actually uses
  dagger: true
  mission_reward: 1000            # money banked per successful mission
  mission_success_probability: 0.85
  mission_defense_rounds: []      # rounds where success blocks the murder
```

Notes:

- With `endgame_vote`/`finale_total` set, `_endgame_configured()` is true
  (game_engine.py:1247), so the plain-parity win no longer ends the game
  early — that closes D3 and lets the real endgame happen.
- `round_limit_winner` only matters if the game still times out; with a
  high `max_rounds` and a configured endgame it almost never fires. If
  you want a hard safety net, set `round_limit_winner: traitor` or leave
  it and rely on the endgame.
- Consider removing `max_recruits` (set `0` = no cap) to fully match the
  show (D8), and decide whether RNG item awards are acceptable or should
  be mission-earned later (D4/D5).
- D10 is a code-level behaviour, not a config fix: the resume path now
  carries the remaining round budget rather than resetting it, so a
  resumed game cannot exceed `max_rounds`. Fixed in `PhaseEngine.run` and
  pinned by `test_resume_never_exceeds_max_rounds`.

Comparing the current 5-round failures: with the block above, a game that
reaches round 5 now keeps playing to the final five and finishes at the
Fire of Truth, which is how every real season resolves.

---

## Sources

- The Traitors (British TV series) — Wikipedia: format, 22 contestants,
  12 episodes, endgame at the fire, blind final banishment.
- TheJournal.ie, "The Traitors has a load of rules…": roles, oath, the
  recruit-vs-murder choice, lone-Traitor ultimatum.
- The Mirror, "What are the rules of BBC The Traitors…": banishment
  reveal, murder death-warrant/breakfast, shields and daggers.
- Peacock blog, "How The Traitors Game Works": mission, round table,
  private vote, recruit on a Traitor's exit.
- traitorbase.com/rules and Fandom (Recruitment, Faithfuls): run-off then
  chance on a tie, seduce vs ultimatum, why players avoid recruiting.
- Radio Times, "How does The Traitors end?": final-five endgame, pouches,
  reveal, prize split.
