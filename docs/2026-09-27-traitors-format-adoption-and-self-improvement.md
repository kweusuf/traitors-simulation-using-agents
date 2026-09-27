# Plan: adopting the real Traitors format, and a self-improving loop

**Date:** 2026-09-27
**Status:** proposed, awaiting approval on wave order
**Research:** two `docs-researcher` briefs, format worldwide and metric-driven
prompt loops. Sources are linked inline; summaries are condensed from their
reports.

---

## 1. What the real show does that we do not

Condensed from the format research. Our engine is text-only, 21 players,
3 traitors, abstracted daily mission, round table, night murder, recruitment
on banishment, rapid-fire finale at 3v3.

### Directly portable mechanics

| Mechanic | How it works on the show | Why it matters in text |
|---|---|---|
| **Shield** | Blocks the next murder; attempt is not refunded; holder chooses whether to disclose; expires after one round. Australia S1 let it block banishment too. | One-shot item with a disclosure decision, pure information game. |
| **Seer** | One-shot private identity check, won by mission performance or a pot auction. Target must confirm their role, does not learn who asked, both may lie afterwards. | Strongest faithful information tool and the standard counter is a tearful "the Seer is lying" split. |
| **Dagger** | Vote counts double at the next banishment, single use. | Single-use power that swings a round table. |
| **On Trial / Death List** | Traitors nominate 3 or 4 players; only those can be murdered that night. UK S3 replaced it with the Death Match (four picked, one card decides who dies face to face). | Constrains the murder pool, so traitors must telegraph intent in public. |
| **Recruit or murder** | The night after a banishment the survivors choose recruit *instead of* murder. Declining can cost the night (UK, NL, US) or not. Solo traitor gets an ultimatum: accept or be murdered. | We currently recruit *in addition* to murdering. The real rule is a choice with a real cost. |
| **Mission as information leak** | The "Chess" mission: traitors privately answer opinion questions about the group, faithful must guess the group's answer. Traitors must pretend not to know answers they demonstrably know. | The one mission design that converts to pure text with no loss. |
| **Journal of Suspicions** | A murdered faithful bequeaths their written suspicions to someone. Used by a traitor in Canada to frame an ally. | Dead players keep influencing the game, which we currently do not model. |
| **Endgame banish-again loop** | After the final table, survivors vote pouches into a fire: green ends, red banishes again. Unanimous end, or auto-stop at two players. Since 2024 the last banished keep their roles secret, so the final vote is blind. | Our rapid-fire finale is close, but lacks the explicit end/banish choice and blind reveals. |
| **Economy levers** | Pot bonuses per traitor banished (US $15k, Canada C$10k), bids for shields, bribes. | Gives everyone a shared resource to argue about. Optional. |

### Roles the show does *not* have

Research found no Doctor, Bodyguard, Mystic, or "smoked glass" in any
edition. The closest real analogues are the Shield (murder protection) and
the Seer (one-shot alignment check). Adding a doctor would be invention, not
adaptation, and should be flagged as such if we do it.

### Innovations the show kept versus dropped

Kept and spread: Seer, Dagger, Murder in Plain Sight, blind endgame reveals,
Death Match, Secret Traitor (8 versions in two years), economic stakes.
Dropped after one run: the Armoury (a social pact made it inert), the
original Prisoner's Dilemma finale (controversial shared pot), publicly
announced shields.

**Pattern to copy:** every retained twist is either an information item with
a single use, a constrained murder, or an economy lever. Twists that only add
spectacle, or that a group neutralises with a pact, get dropped.

### Documented strategies

Faithful: vote-pattern analysis beats vibes (who never votes for a confirmed
traitor); stay central, too quiet gets you murdered, too loud gets you
banished; shield holders stay silent so the whole armoury group is safe; a
Seer reveal is pre-built for credibility and expects a counter-accusation;
bloc voting so the traitors cannot split the vote; in the endgame keep
banishing until confidence all traitors are gone is high.

Traitors: vote with the faithful and never be the last holdout; the "hero
play" of quietly dropping a doomed ally when more than two traitors remain;
frame jobs that amplify a faithful's small inconsistency rather than
originate an accusation; pooling equilibrium, act like a bad nervous faithful
so guilt and incompetence look the same; murder analytical faithful and
alliance coordinators, spare shield holders, avoid a pattern; two hard
disqualification rules everywhere, never reveal yourself, never expose a
fellow traitor.

---

## 2. Self improvement: what the research says to do

Condensed from the second brief, ranked by cost versus payoff for a local 9B
model with 30 to 60 minute runs.

1. **Paired-seed regression gate on the deterministic quality block.** Change
   one thing, run the same 5 to 10 seeds as the baseline, promote only if
   paired deltas pass thresholds on every gate metric. Nearly free, because
   we already have seeds, logs, and the block. This is the foundation.
2. **Post-run diagnosis, proposal, human approval, in the Reflexion style.**
   After each run, generate a diagnosis of metric deltas plus 1 to 3 concrete
   prompt-edit hypotheses; a human picks one; the next iteration tests it.
   One summarisation call per run plus a human glance.
3. **Rubric-anchored scoring only where the event log cannot decide**, with a
   one-time human calibration of about 20 samples. Our 9B model judging its
   own output is the known worst case for self-preference bias, so judge
   aggregates with a separate rubric, never bare preference.
4. **Diversity and balance as hard gates, not one blended score.** We already
   compute duplication and speech similarity; add faction outcome
   distribution across seeds as a guardrail so a candidate cannot win on
   style while flattening dialogue or skewing solo versus team wins.
5. **Cheap proxy shortlisting** if we want more automation: generate several
   prompt variants, score them on replayed logs, run full games only for the
   top one or two. This is EvoPrompt made affordable by never running the
   whole population through a game.
6. **Do not adopt DSPy or OPRO now.** Small local models are documented as
   weak optimisers, and the refactor is large. Borrow the ideas: metrics as
   code, keep prompt text separate from program structure.

Practical numbers: 5 to 10 paired runs is a workable confirmation bar for
continuous quality metrics; binary outcomes such as win rate need dozens of
runs, so treat them as a guardrail, not the objective. Hold 20 to 30 percent
of seeds out permanently so the optimiser never sees them.

Known risks: judge overfitting and reward hacking, drift from intended
balance, silent regressions. Mitigations: holdout seeds, per-dimension gates
instead of a blended score, versioned prompt and config with the last N runs
as baseline, and a human approving every promotion.

---

## 3. Our assets, so we build on them rather than reinvent

Already in the repo: per-call telemetry with tokens, latency, retries and
failures in `llm_calls.jsonl`; a deterministic quality block in
`metrics.json` (hallucination, secrecy, duplication, speech similarity,
parsing); outcome fields (winner, solo versus team, finale, per-player
outcomes); seeded runs and `prompt_version`, `persona_version`,
`game_rules_version` in the experiment identity; a `simulation metrics`
command with `--recompute`; and a phase-per-commit discipline with tests.

What is missing for the loop: a ledger of scores across runs, a comparison
and gate command, and a diagnosis step that turns deltas into proposals.

---

## 4. Proposed waves

Each wave is one phase, with its own verification and its own commit, in line
with `docs/progress.md` conventions.

### Wave A: the self improvement loop (build first, it measures everything else)

- **A1: score ledger.** Append every finished run's quality and outcome
  summary to a single file under `runs/`, keyed by `prompt_version` and seed.
- **A2: `simulation compare <baseline> <candidate>`** on paired seeds: per
  metric delta, pass or fail against per-dimension gates, plus a written
  diagnosis of what moved.
- **A3: post-run diagnosis report** generated automatically at run end:
  which metrics breached gates, worst offending messages with excerpts, and
  1 to 3 prompt-edit hypotheses. Human approves one.
- **A4: holdout seed set**, 20 to 30 percent of seeds, excluded from
  candidate evaluation and reserved for final validation.
- Verification: two fake runs with a deliberate prompt change show a correct
  delta, gate breach, and diagnosis; holdout seeds provably excluded.

### Wave B: format mechanics, wave 1 (highest portable value)

- Shield (blocked murder, optional disclosure, one round expiry).
- Seer one-shot private check, won by mission performance, both sides may lie
  about it afterwards.
- Dagger double vote, single use.
- On Trial style murder shortlist, so traitors must signal in public.
- Recruit *or* murder as a choice, a declined offer costing the night.
- Chess style mission: traitors privately answer group opinion questions,
  faithful must guess the group answer.
- Verification: unit tests per item, a fake full game where each item fires
  at least once, and secrecy scoring still clean.

### Wave C: format mechanics, wave 2

- Journal of Suspicions (a murdered faithful bequeaths written suspicions).
- Endgame explicit choice: end now versus banish again, blind final reveals.
- Economy lever on the pot as an argument surface for discussion.
- Optional: Murder in Plain Sight as a public channel with a hidden action.

### Wave D: strategy prompt packs (prompt text only, cheap, feeds Wave A gates)

- Faithful pack: vote-pattern reasoning, bloc discipline, shield silence,
  credibility before a Seer reveal, keep banning until confidence is high.
- Traitor pack: absorb the majority target, hero play conditions, amplify
  rather than originate accusations, pooling equilibrium, murder selection
  criteria, the two disqualification rules.
- Each pack is a separate `prompt_version` so Wave A can gate it.

### Wave E: optional, only if A to D are not enough

- Rubric-anchored LLM judge for what the event log cannot compute, with the
  20-sample human calibration first. Explicitly not the same model grading
  itself without calibration.

---

## 5. Decisions needed from the user

1. Wave order: A first (measurement before change), or B first (features
   first, measurement already partly exists)?
2. Whether recruitment should become a recruit-or-murder choice, which is a
   game rule change and contradicts our current `recruit_on_banish` behaviour.
3. Whether to add an economy/pot to the simulation, which touches every
   discussion prompt.
4. How many runs per iteration to fund given 30 to 60 minutes each.
