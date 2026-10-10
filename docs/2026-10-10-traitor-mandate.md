# The persona-blind deal, and the traitor mandate

**Date:** 2026-10-10
**Status:** fix implemented and tested; the effect on outcomes is not yet
measured
**Arm:** `configs/traitors/season_uk_s01.fix_en.rand.yaml` (trio drawn by
seed, `traitor_names` dropped)
**Runs:** `uk-s01-rand-fix_en-{fresh,fresh2,conc4,conc16}`

---

## The symptom

Four runs of one arm, one seed, one set of rules. The configs are
byte-identical except for the endpoint and how many model calls were allowed
in flight at once, because this arm exists to be run several ways at the same
time. They did not agree on who wins, or on how long it took:

| run | winner | rounds | banishments that were traitors |
| --- | --- | --- | --- |
| `uk-s01-rand-fix_en-fresh` | traitor | 12 | 3 of 10 |
| `uk-s01-rand-fix_en-fresh2` | faithful | 10 | 3 of 9 |
| `uk-s01-rand-fix_en-conc4` | faithful | 9 | 3 of 8 |
| `uk-s01-rand-fix_en-conc16` | faithful | 7 | 3 of 7 |

Three traitors in twenty-two players is 14%; the room was finding them at 30%
to 43%, twice to three times chance, and the faithful won four times in a row
and quickly. Every predecessor of this arm had gone to the traitors - fourteen
of the fifteen 22-player runs before it, in fact - so the arm had looked like
a constant: traitors win. It is not a constant. Something about running it
this way flipped the result, and it was not the flag in the arm's name.

## What it was not

- **The rules.** The configs differ only in transport settings, so the game
  being played is the same game.
- **Concurrency, or the endpoint.** Neither orders the outcomes: the two runs
  at one call in flight disagree with each other (traitor on one endpoint,
  faithful on the other), so neither setting can explain the side that won.
  What fell as concurrency rose was the round count, which is a consequence of
  the run ending sooner, not a lever on who ended it.
- **Message volume.** Across the four runs the traitor-to-faithful message
  ratio sat between 0.97 and 1.13 - the room was not talking more or less in
  the runs the faithful won. There is no volume story here.

## What it also is

Two artefacts of the round table inflate how readable everyone is. Both are
real, neither explains the flip, and both are still open:

- **A fixed speaking order.** Turns are handed out by `sorted(alive_players)`
  against a quota, so the first speaker of a round is whoever sorts first
  alphabetically and the same players land in the same slot every round. A
  round is a queue rather than a room, and the reaction to player 1 lands
  before player 22 has spoken.
- **Template echo.** Speech similarity ran 0.779 → 0.850 → 0.850 → 0.857 as
  the runs went on, tracking their banishment accuracy. Each round restates
  the previous round's case in the previous round's words, which makes the
  case already on the table look like consensus.

## The root cause: the deal never consults the persona

`tools/make_rand_arms.py` drops `traitor_names` from the season config so the
engine draws the trio from the seed. That is right for arm comparison - a
pinned trio is a constant across every arm, so a behaviour shared by all of
them can be a property of the three personas rather than of the flag under
test. What the change did not account for is how uneven the cast is.

The draw is uniform, and the cast is not. **Three of the twenty-two personas
are written as deceivers** - wilf (`build_alliances`), amanda
(`be_everyones_confidant`), alyssa (`redirect_suspicion_early`) - and those
three are exactly who the season ground truth pins, so every pinned run ever
made cast them. The other nineteen were written as honest contestants, and
several carry a goal that is an instruction to lose once you are drawn as a
traitor.

Seed 1 draws **imran, kieran, meryl**, every time, because the deal is a
function of the seed and the cast and nothing else:

| drawn | what the persona says | goal | traits |
| --- | --- | --- | --- |
| imran | detached and a little remote; does not work the room | `stay_apart_from_the_herd` | sociability 0.25 |
| kieran | guarded, gives nothing away | `never_be_played_twice` | - |
| meryl | follows the group, backs the people she trusts | `stay_loyal_to_your_read` | analytical 0.25, trust 0.75 |

Then `inject_role_goals` **merged** persona goals with role goals instead of
replacing them, so a drawn traitor was handed both as one task list:

```
stay_apart_from_the_herd, ensure_traitor_team_wins      # imran, drawn
stay_loyal_to_your_read, ensure_traitor_team_wins       # meryl, drawn
```

The first item of each pair is a description of honest play, and in a room
whose standing accusation is that someone has been quiet, it is close to an
instruction to lose. A traitor whose instructions are "stay apart from the
herd" and "win for the traitors" has been told two incompatible things in the
same prompt, and the game punishes the quiet half.

Together: the arm's outcome column was measuring **which three players the
seed dealt**, not the rules under test. A deal that can hand the tower to
three players who cannot lie is a source of variance in every arm at once.

## The direction taken

Brief the role at induction. The deal stays random; the capability goes where
the game puts it - in the role, not in the persona.

1. **The obligation does not scale. The method does.** A drawn traitor is told
   that the role requires deception and that honesty is not available to them.
   That core is identical for all of them, because the failure is not a matter
   of degree: a traitor who will not lie is not playing badly, they are not
   playing. Separate from it, the *method* is chosen by
   `Persona.deception_aptitude` - a written deceiver is told that invention
   comes easily and to use detail where it buys a banishment; a candid one is
   told to deceive defensively instead: withhold, answer around the part that
   indicts them, redirect onto an inconsistency somebody else really did
   leave, and let others carry the false case. Inventing a specific detail
   they cannot keep straight is caught faster than saying less.
2. **A traitor stops inheriting the persona's objectives.** The injection now
   gives a traitor the role's goals alone. The persona still supplies the
   manner - its description and its traits reach the prompt as before - but
   not the objective.
3. **No persona file is touched.** The cast stays as written.

**Rejected alternatives.** Pinning the season's three traitors restores
exactly the confound the random-deal arms exist to remove. Adding a
`traitor_aptitude` field to the twenty-two persona files writes the answer
into the input the experiment reads, and would make every later result a
function of an annotation we chose.

**The rule this does not break.** `persona.py` says persona instructions
describe tendencies and never force a strategy. That still holds - the
mandate is role-level, the same shape as the converted-player coaching that
already exists for a faithful recruited mid-game. What changed is that the
*role* states its own requirement, instead of depending on the persona having
been written with one.

## The aptitude, and where the cast lands

`deception_aptitude` is half traits and half goal signal, and it is
deliberately coarse - it selects which briefing a traitor is handed and
nothing finer.

- traits: `(analytical + risk_tolerance + (1 - trust)) / 3`, inverted on
  trust because a player who extends trust readily is the least braced for
  being lied to;
- goal signal: `1.0` if the persona's goals intersect `DECEPTION_GOALS`, else
  `0.0`. This is why the score reads the persona's **base** goals: the
  injection drops exactly that signal from a traitor's goal list, so reading
  it after injection would score every traitor the same.

Measured over the real cast:

| player | aptitude | briefing |
| --- | --- | --- |
| wilf | 0.850 | practised |
| amanda | 0.808 | practised |
| alyssa | 0.792 | practised |
| john | 0.333 | candid |
| kieran | 0.300 | candid |
| imran | 0.275 | candid |
| meryl | 0.125 | candid |

The threshold is 0.6, and it sits in an empty band: nothing in the cast scores
between 0.792 and 0.333, so no player is near the boundary and the split is
not sensitive to the exact number. `john` is the highest of the nineteen by a
clear margin and still lands well below it.

## What was implemented

| file | change |
| --- | --- |
| `agents/persona.py` | `DECEPTION_GOALS`, `Persona.deception_aptitude()` |
| `agents/goals.py` | `MANDATE_CORE`, `MANDATE_PRACTISED`, `MANDATE_CANDID`, `mandate_lines(aptitude)`; `inject_role_goals` no longer merges for a traitor |
| `agents/prompts.py` | `PromptBuilder(traitor_mandate=...)`; `build_system(..., deception=...)` renders the mandate for traitors and defaults the aptitude when not told one |
| `agents/runtime.py` | passes `deception=persona.deception_aptitude(base_goals)` |
| `experiments/config.py`, `experiments/runner.py` | `game.traitor_mandate`, default on, threaded to the builder |

The flag is per the project rule that every experimental flag defaults to the
implemented behaviour and is switchable: `traitor_mandate: false` restores the
previous prompts exactly, which is what makes the change measurable rather
than asserted. See
[03-config-reference.md](03-config-reference.md#the-traitor-mandate).

## What is established

- The prompts a drawn traitor receives now require deception and no longer
  contain the contradictory persona objective. Checked by test and by dumping
  a real prompt for each of the six players above.
- `true` and `false` differ only in that text, so the arms stay comparable.
- 649 tests pass, including the ones that had encoded the old behaviour.

## What is not established

- **No effect on outcomes has been measured.** The control (`traitor_mandate:
  false`, same arm, same seed) has not been run. Everything above establishes
  that the prompts changed and why they should matter; the size of the change
  in win rate is open, and could be small.
- Four runs is not a distribution, and one of them (`fresh2`) crossed a
  mid-run code change and resumed, so a round of it re-ran. Its numbers are
  not clean and should not carry the case alone.
- An identical model tag is not identical weights, so the four runs are not a
  controlled comparison of anything except the deal.
- The mandate is a **nudge to the traitor side**, which means it also removes
  an accidental faithful advantage. If the faithful win rate falls as a
  result, that is the intervention working, not the game breaking - but it
  does mean the change cannot be judged on either side's win rate alone.
  Banishment accuracy is the better read.

## What to try next, in order

1. **The control.** Run the same arm and seed with `traitor_mandate: false`,
   then with it on. Same deal in both, so the briefing is the only variable
   and the difference in banishment accuracy is attributable.
2. **Several seeds.** The deal varies with the seed, so one seed gives one
   trio. Three or four deals, mandate on and off, is the cheapest thing that
   turns "the deal decides" into a measured claim.
3. **Then the two round-table artefacts** in "What it also is": the fixed
   speaking order and the template echo. Both were measurable before this
   work and neither is addressed by it.
