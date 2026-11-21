# Traitor-bias reduction: implementation plan

Date: 2026-11-21
Owner: simulation
Analysis: [`2026-11-21-traitor-bias-and-mitigation.md`](2026-11-21-traitor-bias-and-mitigation.md)
Status: in progress (Phase 0 implemented; see the checklist)

This is the execution plan for the bias analysis. It converts the findings
into ordered, testable work. One lever per arm; the live season configs are
not edited while a queue run is in flight — experiments use separate arm
configs and explicit game ids.

## Objective and targets

- **Diagnose and reduce** the traitor faction's win share, without breaking
  fidelity to the television game for replay arms.
- **Replay arms** (pinned cast, blind finale): expected to stay show-like
  (~55–60% traitor). Do not "fix" them to 50%; label them as replays.
- **Balanced arms** (random deal): target **50–55%** traitor.
- **Faithful skill**: banishment precision **≥ 2× chance** (chance =
  traitors / living). Today it is near chance.
- **No regression**: mean rounds and run stability unchanged; every
  experiment reproducible from its config snapshot and game id.

## Constraints

- The running season queue is production. Do not edit `season_*.opencode_go.yaml`
  mid-run. New behaviour goes in **arm configs** and/or **flags defaulting
  off**.
- One lever per arm. Record the lever in the game id (e.g.
  `bias-ledger-r1`).
- Fix the model for an experiment (`--model … --reasoning-effort …`);
  free-model rotation is a separate confound.
- Screening N = 40–60 games/arm; confirmation N ≥ 150/arm. Report Wilson
  95% CIs.

## Phase 0 — Measurement (blocking)

- [x] `src/simulation/experiments/win_rate.py`: load completed runs,
      segment by pinned/ledger/mandate/finale/model, Wilson intervals.
- [x] `tools/win_rate_report.py`: CLI over `runs/`.
- [x] Unit tests for the loader/interval/formatter.
- [x] Baseline report captured (see "Baseline" below).

Acceptance: the tool reproduces the analysis numbers (29/48 = 60% overall;
pinned 63%; random 50%) and prints CIs.

## Baseline (2026-11-21, reproducible with `tools/win_rate_report.py`)

Default scope: completed runs directly under `runs/` (runs archived under
`runs/_archive_*` are nested and excluded). 38 games:

| Slice | Rate (95% CI) | n |
|---|---|---|
| All completed | 66% (50–79%) | 38 |
| Pinned cast | 71% (53–85%) | 28 |
| Random deal | 50% (24–76%) | 10 |
| `suspicion_ledger` on | inconclusive | 2 |
| `traitor_mandate` off | inconclusive | 1 |

Narrower, cleaner slices:

| Slice | Rate (95% CI) | n |
|---|---|---|
| Current OpenCode season set (`-opencode-go`) | 80% (38–96%) | 5 |
| Historical UK random-deal arm (`uk-s01-rand`) | 20% (4–62%) | 5 |
| Historical UK pinned vs random | 83% (44–97%) vs 20% (4–62%) | 6 / 5 |

Caveats: 30 of the 38 default-scope runs used one historical Ollama model
(`hauhau-qwen`), so model and mechanics are confounded in the pooled number.
Experiments must fix `--model` and filter by arm prefix (`--include bias-`).
Casting is the clearest signal (pinned ≫ random) but the samples are small.

## Phase A — Faithful evidence tools

- [x] **A1** Arm `bias_ledger` (`suspicion_ledger: true`). Run pending.
- [x] **A2** Flag `evidence_accusations` implemented in `PromptBuilder`
      (accuse/rebut/vote) + arm `bias_evidence`. Run pending.
- [x] **A3** Flag `contradiction_audit` implemented as an instruction-based
      cross-check ("name a contradiction, or say what you expected"), not a
      computed transcript digest. Arm `bias_audit`. Run pending.

## Phase B — Faithful coordination

- [x] **B1** Flag `vote_signal` implemented: the ballot prompt carries the
      round's public nomination tally so the room can consolidate. Arm
      `bias_votesignal`. Run pending.
- [ ] **B2** Approval / multiple-target voting **not implemented**; the
      existing `revote_enabled` run-off is the current coordination rule.
      Treat `vote_signal` as the B2 lever for now.
- [x] **B3** Covered by `evidence_accusations` (accusation demands evidence;
      rebuttal answers it; voters discount unsupported claims). Arm
      `bias_evidence`.

## Phase C — Casting and role balance

- [x] **C1** Arm `bias_rand` (random deal). Run pending.
- [x] **C2** Arm `bias_nomandate` (`traitor_mandate: false`,
      `traitor_role_goals: false`). Run pending.
- [x] **C3** Persona audit (from `2026-10-10-traitor-mandate.md`): pinned
      season traitors are the three deceiver personas; a random deal draws
      from the whole cast. Casting bias is structural to replays, not a
      prompt defect.

## Phase D — Rules knobs

- [x] **D1** Arm `bias_finale_open`. Run pending.
- [x] **D2** Flag `recruitment_caution` implemented in the recruit prompts;
      arm `bias_recruit_caution`. Run pending.
- [x] **D3** Arm `bias_items_off` (shield and dagger off). Run pending.
- [x] **D4** Arms `bias_rand` (3 traitors) and `bias_rand4` (4 traitors).
      Run pending.
- [x] **D5** Flag `reveal_traitor_count` implemented; arm
      `bias_reveal_count`. Run pending.

## Phase E — Model sweep and confirmation

- [ ] Run the canonical arm on 3–4 models; report per-model rates.
- [ ] Confirm the winning levers at N ≥ 150/arm; avoid single-model
      overfitting.

## Arm configs (created in `configs/traitors/`)

One lever each, copied from `season_uk_s01.opencode_go.yaml`:

| File | Lever | Hypothesis |
|---|---|---|
| `season_uk_s01.bias_ledger.yaml` | `suspicion_ledger: true` | Faithful vote consistency up |
| `season_uk_s01.bias_nomandate.yaml` | `traitor_mandate: false`, `traitor_role_goals: false` | Less traitor buff |
| `season_uk_s01.bias_rand.yaml` | `traitor_names` removed | Random deal lowers traitor share |
| `season_uk_s01.bias_finale_open.yaml` | `blind_finale_banishments: false` | Fewer endgame steals |
| `season_uk_s01.bias_evidence.yaml` | `evidence_accusations: true` | Evidence-backed cases |
| `season_uk_s01.bias_audit.yaml` | `contradiction_audit: true` | Cross-round contradictions surface |
| `season_uk_s01.bias_votesignal.yaml` | `vote_signal: true` | Ballot consolidates |
| `season_uk_s01.bias_recruit_caution.yaml` | `recruitment_caution: true` | Fewer accepted recruits |
| `season_uk_s01.bias_reveal_count.yaml` | `reveal_traitor_count: true` | Faithful calibrate the count |
| `season_uk_s01.bias_items_off.yaml` | shield/dagger off | Isolate double-vote amplification |
| `season_uk_s01.bias_rand4.yaml` | random deal, 4 traitors | Larger bloc effect |

Run template (from `agent/`):

```bash
uv run --no-sync python -m simulation run \
  configs/traitors/season_uk_s01.bias_ledger.yaml \
  --provider openai_compat --model <fixed-model> --reasoning-effort low \
  --game-id bias-ledger-r1 --seed 42
```

Then compare with
`uv run --no-sync python tools/win_rate_report.py --include bias- --by model`.

## Definition of done

- Measurement tooling merged and used for every experiment.
- Each arm run at screening N, then confirmation N for the levers that
  survive.
- Balanced arm within 50–55% traitor; banishment precision ≥ 2× chance.
- Replay arms documented as show-faithful (not "fixed" to 50%).
- Findings and the chosen defaults written back into the analysis doc and
  `progress.md`.

## Status log

- 2026-11-21: Phase 0 implemented — `win_rate.py`, `tools/win_rate_report.py`,
  tests, and the baseline above.
- 2026-11-21: Levers implemented behind default-off flags in `config.py`,
  `PromptBuilder` (evidence accusations, contradiction audit, recruitment
  caution, reveal traitor count), and `VotingPhase` (vote signal). All 11
  one-lever arm configs created and validated; fake-provider smoke runs
  complete. 721 tests green. B2 (approval voting) deferred. Experiment runs
  pending until the season queue frees the model/quota.
