# Traitor bias: what it is, why it happens, and a plan to reduce it

Date: 2026-11-21
Status: analysis + plan (no engine/config changes made yet)
Related: `2026-10-10-traitor-mandate.md`, `2026-11-19-traitors-mechanics-and-drift.md`

---

## TL;DR

- **What the bias is.** In this simulation the traitor faction wins more
  often than the faithful. Across all 48 completed runs in `runs/`, traitors
  won **29 (60%)**, faithful 19 (40%). The current season set is small (5
  done so far: 4 traitor, 1 faithful).
- **Is 60% abnormal? No.** The real show is about the same: roughly
  **56–60% traitor wins** across the international seasons (US/UK/AU/NZ/
  Canada/Ireland/India). So the faction-level rate is broadly faithful to the
  source game, and the recent 4-of-5 streak is small-sample noise.
- **Where the extra tilt lives.** The strongest measurable factor is
  **casting**: our season configs pin the real cast, and the real traitors
  are exactly the three personas written as practised deceivers. Runs with a
  **pinned cast** win for traitors **63%**; a **random deal** about **50%**,
  and the dedicated random-deal arm is **20% (1/5)**. The pin is what makes
  replay seasons traitor-favoured.
- **The other half is LLM cognition.** Faithful agents are weak at detecting
  deception and at integrating evidence; they are swayed by accusations and
  flip suspects without new evidence (see literature below). The repo already
  has a `suspicion_ledger` that is **off by default** and explicitly exists
  because "voting tracked chance" without it.
- **Plan in one line.** Formalize measurement, then test a short list of
  levers in controlled arms — faithful evidence tools, accusation
  credibility, casting (pinned vs random), mandate, endgame/recruitment
  knobs — aiming for a balanced arm near 50–55% while keeping replay arms
  honest to the show.

---

## 1. Defining the bias

Two different numbers are easy to conflate:

1. **Faction win share** — what fraction of games the traitor *side* wins.
   This is the natural headline and what the show reports.
2. **Per-player win probability** — each individual traitor's chance vs each
   faithful's. Because there are only 3–4 traitors against ~17–20 faithful,
   this is inherently lopsided even at a 50% faction rate: in the published
   analyses a traitor is roughly **3–4× more likely to win as an individual**
   (≈26% vs ≈7%). This is not a bug; it is what "a few hidden players vs
   many" means.

"Traitor bias" in the rest of this document means **faction win share above a
balanced 50%**, and secondarily the faithful's *ability to detect* traitors
(banishment precision), which is what a design change can move.

---

## 2. Evidence from our own runs

All completed `runs/**/metrics.json` (n = 48), segmented by the run's saved
`config.yaml`:

| Slice | Traitor wins | Rate |
|---|---:|---:|
| All completed | 29 / 48 | **60%** |
| Pinned cast (`traitor_names` set) | 24 / 38 | **63%** |
| Random deal (no pin) | 5 / 10 | **50%** |
| Name contains `rand` (the random-deal arm) | 1 / 5 | **20%** |
| `suspicion_ledger: true` | 2 / 2 | 100% (n too small) |
| `suspicion_ledger` off/absent | 27 / 46 | 59% |
| `traitor_mandate` on (default) | 29 / 47 | 62% |
| `traitor_mandate` off | 0 / 1 | 0% (n too small) |

Read with care: these pool different rules versions, models, and arms, so the
slices are suggestive, not controlled. Rough 95% Wilson intervals:

- All: 60% (≈ 47–74%).
- Pinned 63% (≈ 48–79%); random 50% (≈ 24–76%) — overlapping, so casting is
  a **strong hypothesis, not yet a proven effect**; the `rand` arm (1/5) is
  the sharpest hint but tiny.

Length corroborates the difficulty: traitor wins average ~10.8 rounds,
faithful ~7.9 — faithful wins are short, i.e. they happen when the room
catches traitors early; otherwise the game drifts to a traitor endgame.

**Current fixed-mechanics set (post 2026-11-19 fix):** 5 complete,
4 traitor / 1 faithful. All reached `finale: true` with an accrued prize pot,
so these are real endgames, not the old round-limit artifact. n = 5 is far
too small to conclude anything.

---

## 3. Real-show baseline

Public tallies put traitor wins in the **mid-to-high 50s / ~60%**:

- Fandom wiki: 38 faithful vs **57 traitor** wins (+1 shared, 1 none) ≈ 60%.
- Medium (64 seasons): traitors win **~60%**; per-player ≈ 26% traitor vs 7%
  faithful.
- Pop Heist (16 seasons): traitors **9/16 ≈ 56%**.
- ReadersFusion (23 finished seasons): 12 traitor / 10 faithful / 1 none.
- By edition: UK 2/4 traitor, US 2/4, Australia 2/3 (one "no winner" from the
  steal), NZ 2/3, India 1/2.

So a 60% faction rate is **on-model**. The design question is not "make it
50% because 60% looks unfair"; it is "do we want replay arms to mirror the
show, and do we want the faithful to earn their wins through better play
rather than luck?"

---

## 4. What the LLM research says (why faithful under-perform)

The faithful's job — detect deception and integrate evidence — is exactly
where current LLMs are weakest:

- **WOLF (Werewolf benchmark).** LLMs deceive convincingly but detect poorly:
  ~71–73% precision but ~48–61% recall, ~52% overall accuracy. Suspicion of
  honest roles is also high, so group accuracy is low.
- **Among Us study (1,100 games).** Deception is mostly equivocation, not
  brazen lies; impostor deception style barely predicts winning. Crewmates
  eject a self-incriminating agent only ~34% of the time and reverse their
  stated suspect without new justification in ~42% of rounds.
- **Werewolf belief-shift study (1,224 messages).** Accusations strongly move
  observers' beliefs — they become more suspicious of the accused and *less*
  of the accuser, even when the accuser is wolf-aligned. This is a direct
  exploit: a traitor who accuses first steers the room.
- **Werewolf deception benchmark.** SOTA models were "no better than a random
  baseline" at winning, and adding a second werewolf did not reliably help
  team deception (LLM collaboration on deception is itself weak).

Takeaway for us: faithful agents will default to recency, accusation-chasing
and inconsistent suspicion unless the environment gives them **persistent,
structured evidence** and rewards consistency. The traitors, meanwhile, get a
private channel and a shared objective, so they coordinate far better than
the faithful do.

---

## 5. Ranked causes of the tilt

1. **Pinned casting = the deceiver personas (strongest measurable).**
   `traitor_names` pins the real season traitors, and `config.py:145-161`
   records that the three personas written as deceivers are exactly the ones
   the pinned seasons cast. A replay therefore hands the tower to the best
   liars and the room to trusting personas. Random deals draw traitors from
   the whole cast and the rate falls toward 50%.
2. **Inherent asymmetry + endgame math.** Traitors share perfect information
   and a private channel; any single surviving traitor wins, and
   `blind_finale_banishments` hides the final role. Faithful must banish
   *all* traitors. This is show-faithful but strongly traitor-leaning.
3. **Faithful cognitive limits + ledger off.** `suspicion_ledger` is off
   (`config.py:100`), and its own docstring says voting "tracked chance"
   without it. Memory/relationships exist, but nothing forces a consistent,
   ranked read shown to the model every turn.
4. **Coordination asymmetry.** Traitors vote as a bloc (3–4 aligned votes vs
   ~17 scattered faithful) and can pre-plan in the turret; faithful have no
   equivalent coalition step. Plurality banishment rewards the coordinated
   minority when the majority splits.
5. **The traitor mandate may over-buff.** `traitor_mandate` and
   `traitor_role_goals` are on by default (`config.py:161-168`); the mandate
   tells a drawn traitor that "deception is the job" and scales the method to
   their persona. Effect on outcomes is explicitly **not yet measured**
   (`2026-10-10-traitor-mandate.md`).
6. **Recruitment acceptance is high.** Offers are accepted almost always
   (archived: 12/12; live India: 2/3). Each acceptance refills the tower and
   keeps the bloc large/coordinated; the show's agents often decline or avoid
   recruiting.
7. **Voting rule / plurality split.** Faithful votes fragment across many
   suspects; `revote_enabled` only helps on an exact tie. A coordination or
   approval step could consolidate faithful votes.
8. **Items and model.** Item awards are seeded-RNG, not earned: daggers
   (double votes) can amplify a coordinated bloc; shields on a traitor are
   wasted (traitors cannot be murdered). And the model itself matters — the
   queue currently rotates free models (beam-free, hy4), whose detection
   ability differs.

---

## 6. Measurement protocol (do this first)

Without this, every "fix" is a guess.

- **Canonical arm.** One cast, fixed seed set, 22 players, 3 traitors,
  identical prompts; vary exactly one lever per arm. Keep replay arms
  (pinned) separate from balance arms (random deal).
- **Sample size.** Screening: 40–60 games/arm on a cheap model. Confirmation:
  ≥150 games/arm. With p≈0.6, the 95% CI at n=48 is ±~14 points; you need
  hundreds to resolve a 10-point move.
- **Primary metric.** Traitor faction win share with Wilson CI.
- **Secondary metrics.** Faithful banishment precision (share of banished who
  were traitors) vs the chance baseline (traitors / living); mean traitors
  surviving at end; mean rounds; per-role win probability; recruitment
  acceptance rate; accusation→vote conversion.
- **Segmentation.** By model, by pinned vs random, by finale/blind flags.
- **Logging.** Every run already snapshots `config.yaml`; add the lever
  label to the game id so slices are unambiguous.

Suggested tool: a small `tools/win_rate_report.py` that walks `runs/`,
reads `metrics.json` + `config.yaml`, and prints the table above with CIs and
optional filters.

---

## 7. Mitigation plan (phased, highest-confidence first)

Each row is an A/B arm: control = current season config; treatment = one
change. "Expect" is a hypothesis to be measured, not a promise.

### Phase A — Faithful evidence tools (lowest risk, addresses cause 3/4)

| Lever | Change | Mechanism | Expect |
|---|---|---|---|
| A1 | `suspicion_ledger: true` (and tune `ledger_suspects/allies`) | Forces a ranked, persisted suspect/ally read shown every turn; counters recency and unjustified flips | Higher banishment precision; lower traitor share. Cost: one extra generation/phase |
| A2 | Evidence-weighted accusations (prompt) | Tell agents to discount accusations made without evidence and to weight by track record; counters the belief-shift exploit | Fewer wrong banishes from a single accusation |
| A3 | Neutral contradiction recap (new) | A clerk-style per-round digest of who claimed what and where claims conflict, shown to all | Better evidence integration; faithful recall up |

### Phase B — Coordination (addresses cause 4/7)

| Lever | Change | Mechanism | Expect |
|---|---|---|---|
| B1 | Pre-vote **vote-intent straw poll** (new, public) | Faithful see the room consolidating and can converge; traitors already converge, so the marginal gain is faithful-side | Fewer split ballots; higher faithful hit rate |
| B2 | Approval / majority-with-run-off voting (experiment) | Consolidates the majority behind one suspect | Cuts plurality fragmentation |
| B3 | Structured accusation + mandatory defence (already `nomination_enabled`) — make defence evidence-bearing | Accuser must state evidence; target rebuts | Blunts accusation-driven belief shifts |

### Phase C — Casting and role balance (addresses cause 1/5)

| Lever | Change | Mechanism | Expect |
|---|---|---|---|
| C1 | **Random-deal arm** (`traitor_names` dropped) as the balance benchmark | Removes "best liars are the traitors" | ~50% traitor; establishes the casting effect |
| C2 | `traitor_mandate: false` and `traitor_role_goals: false` arms | Tests whether the deception briefing over-buffs traitors | Lower traitor share if the mandate is doing the work |
| C3 | Persona audit | Ensure drawn traitors can include low-aptitude players; ensure faithful include sceptical personas | Reduces systematic deception skill gap |

### Phase D — Rules knobs (addresses cause 2/6/8)

| Lever | Change | Mechanism | Expect |
|---|---|---|---|
| D1 | `blind_finale_banishments: false` | Revealing the final banishment lets survivors correct before End | Fewer traitor endgame steals |
| D2 | Recruitment calibration (prompt: add cost/risk of accepting) | Fewer accepted recruits → smaller traitor bloc | Lower traitor share; more show-like decline rate |
| D3 | Items off vs on; dagger on/off | Measures whether double votes amplify the bloc | Isolate item contribution |
| D4 | Starting traitors 3 vs 4 (synthetic casts) | Smaller bloc, less coordination | Lower traitor share at 3 |
| D5 | Reveal **traitor count** (not identities) — variant | Lets faithful calibrate how many remain | Higher endgame accuracy (non-show variant) |

### Phase E — Model sweep (addresses cause 8)

Run the canonical arm on 3–4 models and report per-model rates. If bias is
model-specific (some models detect deception better), prefer/average models
rather than tuning the game to one. This also protects against overfitting to
the free models currently in rotation.

---

## 8. Sequencing and success criteria

Order: **Phase 0 (measure)** → **A1–A3** (cheapest, most direct) → **C1/C2**
(explain the casting/mandate contribution) → **B1–B2** → **D1/D2/D4** →
**E** (confirm across models).

Proposed targets:

- **Replay arms (pinned, blind finale):** stay show-faithful; expect ~55–60%
  traitor and say so explicitly. Do not "fix" a faithful-to-the-show replay
  into a 50/50 game.
- **Balanced arm (random deal):** target **50–55%** traitor.
- **Faithful skill:** banishment precision **≥ 2× chance** (currently
  chance-level per the ledger docstring). This is the real quality signal.
- **No regression:** mean rounds and run stability unchanged; experiments
  labelled and reproducible from the config snapshot.

Guardrails: one lever per arm; keep show-faithful defaults for the shipped
season set; never tune to a single model; keep pinned replays pinned; record
the arm label in the game id.

---

## 9. Risks

- **Small samples / noise.** The recent 4-of-5 is not evidence; the 60% pool
  is ±14 points. Confirm before shipping a lever as default.
- **Model confound.** Free-model rotation changes the player, not just the
  game; segment or fix the model for experiments.
- **Overfitting.** A prompt tweak that lifts one model can hurt another; the
  Phase E sweep exists to catch this.
- **Fidelity vs fairness.** Random deals and traitor-count reveals are not
  the show. Keep them as balance arms, not the default replay.
- **Cost/latency.** `suspicion_ledger` adds a generation per phase; budget it.

---

## Update 2026-11-25 — the random-deal marathon

The full random-traitors marathon (`traitor_names` removed, same rules, same
ten seasons) reached 7 completed games: **6 traitor / 1 faithful = 86%
(95% CI 49-97%)**, against the pinned v2 marathon's **8/10 = 80% (49-94%)**.
At this sample the two are indistinguishable, so **casting is not supported
as the dominant cause**: randomising the traitors did not lower the rate.
The one faithful-looking pilot result was a single game. The bias looks more
structural — information asymmetry, faithful detection/evidence weaknesses,
traitor coordination, and the endgame math — than a property of pinned
season casts. The gameplay levers still need a real N to test; a single game
per arm cannot resolve a 10-point move.

## Sources

- Winners by season (ReadersFusion): traitors 12 / faithful 10 across 23
  finished seasons.
- Fandom "List of Winners": 38 faithful / 57 traitor / 1 shared / 1 none.
- Medium (M. Reppion), "The Traitors: Who will win?": ~60% traitor across 64
  seasons; per-player ≈26% vs 7%.
- Pop Heist, "Heist Guide: The Traitors": traitors 9/16 ≈ 56%.
- WOLF (arXiv 2512.09187): LLM deception detection ~52% accuracy,
  71–73% precision / 48–61% recall.
- "Deception and Communication … Among Us" (arXiv 2603.26635): equivocation
  dominates; confessions ejected ~34%; ~42% unjustified suspect reversals.
- "Do LLMs Trust the Accuser or the Accusation?" (EMNLP 2026): accusations
  shift belief toward the accused and away from the accuser, even for
  wolf-aligned accusers.
- Werewolf deception benchmark: SOTA ≈ random baseline; two werewolves did
  not reliably beat one.
