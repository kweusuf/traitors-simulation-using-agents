# Faithful mechanism: research and enhancement

Date: 2026-11-25
Motivation: the random-deal marathon did not lower the traitor rate (86% vs
80% pinned), so casting is not the main cause. The faithful side was
under-specified relative to the traitors. This note surveys what makes
faithful players effective, audits the implementation, and records the
first enhancements.

Related: `2026-11-21-traitor-bias-and-mitigation.md`,
`2026-11-21-traitor-bias-implementation-plan.md`.

---

## TL;DR

- The faithful's strongest real evidence is the **public voting record**,
  not tells or vibes. In the show, 95% of votes for a traitor were cast by
  faithful, traitors almost never vote each other until the night one is
  caught, and a revealed traitor's past votes clear the people they
  targeted. A running score beats chance roughly **2×** after the first
  traitor is revealed (58% vs 25%).
- Group **discussion** improves lie detection (PNAS); structured argument
  and disconfirming evidence help; consistency is a weak cue; the useful
  cue is **correspondence with the record**.
- In our code the faithful had only a thin goal (`survive`,
  `identify_traitors`), no induction brief (the traitors have one), and —
  critically — **votes were never shown to the agents**. The event log
  recorded `VOTE_CAST`, but neither the view nor the prompt carried it.
- Implemented now (default off): a **public voting record** shown to every
  player with revealed-traitor cross-references, and a **faithful
  mandate** briefing. Arms created; effect still needs a real N.

---

## 1. What the show and its analysts say works

- **Round tables are weak.** Across six UK series, only 34% of banishments
  removed a traitor (23% by chance); individual faithful votes hit a
  traitor 28% of the time (22% by chance).
- **Votes are the signal.** 95% of the 76 votes for traitors were cast by
  faithful. A traitor's first vote against a fellow traitor comes on the
  night that traitor goes. A player who keeps voting for a traitor right
  up to the catch is almost certainly faithful; so is anyone a revealed
  traitor voted for on a night neither left.
- **A running score works after the first reveal.** The top suspect was a
  traitor 58% of the time after at least one traitor was revealed, versus
  25% by chance; before any reveal it was no better than guessing.
- **Group discussion, not aggregation.** Groups beat individuals at lie
  detection through discussion/synergy, not by averaging votes; structured
  groups do better; established groups with relational links do better.
- **Argumentation helps.** Discussing "what suggests true / what suggests
  false" improved veracity accuracy; individual reflection alone did not.
- **Consistency is a trap.** Liars repeat a prepared story; truth-tellers
  reconstruct and add detail. So *inconsistency* is not proof of deceit;
  **correspondence** to known facts (the vote record, revealed roles) is
  the better cue.
- **Ask them to talk.** Encouraging a suspect to elaborate (unanticipated
  questions) is a practical way to expose a story that does not
  correspond to the record.
- **Beware in-group bias and herd voting**; a split vote hands the round to
  a coordinated minority. The "traitor angel" (ally with a suspected
  traitor to survive) is a real faithful strategy, but it is a survival
  play, not detection.

## 2. What the LLM literature says

- LLMs detect deception near chance (~52% accuracy) with high precision but
  low recall, and they are swayed by accusations (belief shifts toward the
  accused and away from the accuser), even when the accuser is a traitor.
- They integrate evidence poorly and reverse suspects without new
  justification. Persistent, structured evidence and explicit reasoning are
  the documented mitigations, not "try harder to spot lies."

## 3. Audit: the faithful were under-built

- `ROLE_GOALS[FAITHFUL] = ("survive", ["identify_traitors"])`
  (`goals.py:17`) — no method, no evidence guidance.
- Traitors get an induction mandate (`mandate_lines`, scaled to deception
  aptitude); faithful had no equivalent brief.
- **Votes were invisible.** `VOTE_CAST` was emitted (`game_engine.py`) and
  written to the event log, but `AgentView` did not carry it and the prompt
  never showed it. State kept only the current round's `votes`, no history.
- `suspicion_ledger` exists and is wired (`runtime.py:185`) but is off by
  default. The evidence/audit/vote-signal levers from the bias plan are also
  off by default.
- Net: the faithful were asked to identify traitors with less information
  and less method than the traitors were given to hide.

## 4. Implemented now (default off)

### 4.1 Public voting record (`voting_record`)

- `GameState.vote_history: list[VoteRecord]` records every ballot
  (`round_number`, `voter_id`, `target_id`, `ballot`), appended on vote and
  rebuilt on resume from `VOTE_CAST`.
- `agents/voting_record.py` renders, per viewer:
  - each living player's vote history (`a voted: r1->x, r2->y`);
  - for each **revealed** traitor: who voted for them (a clearing signal)
    and who they voted for (players a revealed traitor targeted are usually
    cleared).
  Only public facts are used (votes are public; roles only once revealed),
  so it is safe for every player and respects the blind finale.
- `PromptBuilder` shows it on speech/vote actions under "Public voting
  record so far"; the runtime injects the per-viewer lines on a builder
  copy.
- Why this is the top lever: it is the exact signal the real-game analysis
  finds predictive, and it directly counters the LLM failures (poor recall,
  recency, accusation-chasing) with a persistent, structured record.

### 4.2 Faithful mandate (`faithful_mandate`)

A short induction brief for faithful, mirroring the traitor mandate:
use the record; treat a suspicion as a hypothesis and seek disconfirming
evidence; ask suspects to elaborate; consistency is weak, correspondence
matters; converge a split vote with credible allies; do not brand yourself
a traitor hunter.

### 4.3 Arms

- `season_uk_s01.bias_voterecord.yaml` — `voting_record: true`
- `season_uk_s01.bias_faithfulmandate.yaml` — `faithful_mandate: true`
- `season_uk_s01.bias_faithfulpkg.yaml` — both

All default off, so existing runs are unchanged. 726 tests pass, including
new tests for the record rendering and the mandate gating.

## 5. Next enhancements, ranked

1. **Test the voting record at scale** (the main one). Fixed model,
   N=40–60, paired seeds, measure banishment precision (share of banished
   who were traitors) and traitor share. Expect a measurable rise in
   precision *after the first traitor reveal*.
2. **Vote consolidation** — approval / majority-with-run-off voting (B2 in
   the bias plan) or a public ballot signal. The research is clear that a
   split faithful vote loses to a coordinated bloc.
3. **Probe action** (`PROBE`): a faithful asks a suspect a specific,
   unanticipated question; the answer is public. Operationalises the
   "give them rope" technique.
4. **Recruit caveat in the record**: a recruit carries a faithful voting
   record, so clearing votes only count *before* a possible recruitment.
   Mark the recruitment round in the record.
5. **Correspondence tracker**: store claims and check new claims against the
   record ("you said X in round 2; tonight you said not-X"). Stronger than
   the current instruction-only `contradiction_audit`.
6. **Calibration**: require a confidence and a fallback ("if X is faithful,
   then Y is my next suspect"), which reduces overconfident bandwagons.
7. **Blind finale off** for a faithful-correction arm; the endgame reveal
   lets survivors correct before End.
8. **Faithful coalition/pact**: an explicit voting pact among credible
   faithful, with a defection check.

## 6. Experiment protocol

- Fixed model (hy4-flash-free, the one both marathons proved), concurrency
  1–2, paired seeds, one lever per arm.
- Screening N=40–60; confirm N≥150 for a 10-point move. Report Wilson CIs.
- Primary: faithful banishment precision vs chance (traitors / living);
  secondary: traitor faction share, mean rounds, votes-for-traitors rate.
- Segment: before vs after the first traitor reveal (the record should help
  most after reveal), and by model.

## Sources

- The Traitors analysis: Will Ball, "Can a Faithful spot a Traitor from the
  votes alone?" (768 votes, 61 round tables; 34% banishment hit rate; 58%
  scorecard after first reveal).
- The Conversation, "Celebrity Traitors: voting behaviour could help
  identify faithfuls" (95% of votes for traitors cast by faithful).
- New Scientist, forensic psychology / evolutionary biology on The Traitors
  (altruism, in-group bias, consistency vs correspondence, unanticipated
  questions).
- Fandom "Faithfuls"; traitorbot "Theoretical Winning Strategy" and
  "Loyalist" (alliances, blocs, traitor angel).
- PNAS 2015 "Group discussion improves lie detection"; structured-groups
  2021; Frontiers 2016 "Detecting deception within small groups" (repeat vs
  reconstruct; correspondence vs coherence).
- WOLF and Among Us LLM studies; accusation belief-shift (EMNLP 2026).
