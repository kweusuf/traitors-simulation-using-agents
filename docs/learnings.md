# Learnings

Findings, not decisions. Each is something measured or observed about how
this codebase and these models behave, with the evidence attached so it can
be re-checked rather than trusted.

Decisions and their reasoning live in [decisions.md](decisions.md). Code
flow lives in [flow.md](flow.md).

---

## Measurement

### Word overlap cannot separate copying from discussion in this game

Replaying `uk-s01-ptr-mem` (509 public messages), scoring each against
everything said before it:

| Threshold | Would reject |
| --- | --- |
| 0.6 | 48.1% |
| 0.8 | 34.0% |
| 1.0 (exact) | 17.5% |

Nearly half of a real run's messages overlap something said earlier. That
is not copying - it is players legitimately returning to the same few
claims about the same few players. **Any paraphrase threshold discards
half a real game.**

Reproduce: `python tools/repetition_rates.py runs/<run>`

### The template collapse is cross-player, not self-repetition

Measured on `uk-s01-rep` (gate on, `scope: self`) against `uk-s01-ptr-mem`:

| | `ptr_mem` | `uk-s01-rep` |
| --- | --- | --- |
| Public messages | 509 | 247 |
| Self-repeats (same player twice) | 32 | **0** |
| Cross-player duplicates | 50 | 15 |

Self-repetition went to zero - the gate does exactly what `scope: self`
asks. The surviving duplicates are *different* players emitting identical
text:

```
n=5  meryl, amos, andrea, maddy, rayan  "Theo, your silence since..."
n=4  aaron, amos, andrea, hannah        "Alex, your defense of Ivan's..."
```

So the gate was pointed at the smaller half of the problem. `scope: room`
is the untested setting and is where the remaining 6.5% lives.

### A single swapped word is invisible to word-overlap scoring

"Iris is lying" and "Iris is truthful" score **identically** (0.667) - one
token differs, in either direction, so the union grows the same amount. A
player changing their mind looks exactly like a restatement. Pinned in
`tests/unit/test_repetition.py::test_word_overlap_cannot_see_a_single_word_flip`.

### Pooling public and private messages inflated repetition by ~1.6x

An early write-up reported 38% of messages repeating. Split by channel:
public 23%, private 11%. A private DM echoing text it received is not the
room speaking in one voice. Every rate since is per-channel.

---

## Models and hosts

### Prompt size is the context problem, and it is not the instructions

`tools/prompt_composition.py` differences real builder output:

| Section | Chars | Share |
| --- | --- | --- |
| system prompt (fixed) | 1,180 | 2% |
| public transcript | 33,744 | 49% |
| private threads | 30,543 | 44% |
| instructions + memory | 2,782 | 4% |

The fixed parts are under 3%. Re-prompting for brevity cannot help; the
transcript window is the lever.

### Weights-on-disk is not resident memory

`hauhau-qwen-27b` reports 17.5 GB on disk (Q4_K_M) but the server held
**26.6 GB** resident, `size_vram` 15.3 GB, `quantization_level: unknown`
against `Q4_K_M` on the tag. It timed out at 300s on a **3,721-character**
prompt - the shortest the game produces - through all 6 retries, in round
1. Never emitted a token. Reasoning about "does it fit in 24 GB" from the
file size on disk was wrong; the resident footprint is the number that
matters, and it is only visible from `/api/ps` while loaded.

### The host's context size is server state, not config

A run died at 4,229 tokens against `n_ctx: 4096`, while the same host and
model had served `ptr_mem` prompts of 43,039 characters (~10k tokens)
without complaint. The model reports `context_length: 262144`; Ollama was
serving it at 4096. **Verify `/api/ps` before a long run** - the setting
lives in the server process, not the config.

### Concurrency past a host's capacity queues into the timeout

16 concurrent finished a season but badly degraded (p95 674s, 23% failed).
4 per run, two runs at once, is the level these configs record as always
clean. Past what a host serves in parallel, extra requests do not error -
they wait, and then fail on `timeout_seconds`.
---

## Prompting and schema

### A worked example decides closed-choice actions

The end-vote example held `"content": "end"` and every season run ended its
finale with all five players answering `end`. **A unanimous end stops the
game**, so a surviving traitor takes the prize. That was the mechanism
behind a run of traitor wins.

Measured on the real final five: example `end` gave `end` 6/6, example
`banish` gave `banish` 4/4. The fix was pinning `content` to an enum in
the schema and showing `?` in the example.

### Redeclaring a field as required does nothing if the schema hook rewrites `required`

`Action.__get_pydantic_json_schema__` replaces the `required` list
wholesale, so `gist_required` and `co_generate_gist` arms were silently
identical - the provider was handed an optional field. Found by diffing the
arms, not by reading them. Now covered by a test.

### A structured-output schema requires `target` on every response

A public message arrives carrying a copied player name or a `'none'`
placeholder. Neither means anything for an action without a target, and
both fail the legal-target check. Cleared before validation.

### A capitalised token before a comma is not always a player

The phantom detector fires on "However, I think..." - reporting the
adverb `However` as a non-existent player and burning a retry. Observed
live in `uk-s01-rep`. The rule (capitalised, in address position,
matching no player) is right in principle and wrong on sentence-initial
ordinary words.

### Detecting a phantom needs a position rule, not just capitalisation

"Iris, you have been quiet" and "Watching the watchers is exhausting" share
a shape. Position alone is not evidence either - "Round shows us
something" is ordinary. What separates them is the comma: the suspect is a
token *immediately before* one. First-word-of-sentence was tried and
rejected - it flagged every ordinary capital.

---

## What we got wrong, and withdrew

Recorded because a withdrawn claim in the history is still a claim someone
will read.

- **"Pointer memory flattened the prompt curve."** It did not.
  `transcript_messages_per_prompt: 40` is the cap, and pointer memory never
  touched the window.
- **"The pointer arm ran four times faster with no failures."** Drawn from
  51 calls in round 1. Over the full 1,007 calls it matched the baseline.
- **"38% of messages repeat."** Wrong denominator, corrected above.
- **"A 27B model will fit in 24 GB."** Arithmetic on the wrong number, and
  never verified before recommending a run on it.
- **"A paraphrase gate will catch the collapse."** It would reject half the
  messages instead.

---

## Still open

- `scope: room` untested. Targets the 15 surviving cross-player duplicates.
- Phantom detector false positive on sentence-initial words.
- Whether a paraphrased collapse ever occurs, which is the only condition
  under which lowering `repetition_threshold` is right.
- Latency cost of rejections: p50 rose 24.9s to 70.5s in `uk-s01-rep`, part
  of which is 21 extra generations on already-large prompts.