OMN-19054 — the serializer reported `CHANGED` on every run after the first real write, because it compared bytes and Linear does not store the bytes it is given.

Evidence-Ticket: OMN-19054

Opened through the writer-App dispatch path because this change touches `src/`.

## What was wrong

OMN-18270 declared idempotence as an evidence item and asserted it — against a body the serializer itself had produced, where byte equality holds trivially. #10708 wrote a serialized section to a real Linear ticket for the first time, and the assumption the test rested on turned out to be false.

`plan_acceptance_criteria_update` decided `changed` by byte-comparing the planned body against the body it was handed. Every subsequent run therefore reported `CHANGED` for a ticket whose criteria had not moved, and a corpus sweep would have rewritten byte-identical criteria into every serialized ticket on every pass — a Linear revision per ticket per run, each one looking like an edit a person made.

## The exact before and after

Emitted by `onex-serialize-ac-section --emit-body`, applied to OMN-18167, then read straight back from Linear.

| | characters | lines |
| -- | -- | -- |
| emitted | 5,878 | 40 |
| stored by Linear | 5,883 | 40 |

Nine lines differ, in exactly two ways. Line 30, the first criterion, with the first 96 characters of each:

```
emitted: '- **AC1** — The C6 golden leg runs on the dev plane and emits a `key-lifecycle-golden` record de'
stored:  '* **AC1** — The C6 golden leg runs on the dev plane and emits a `key-lifecycle-golden` record de'
```

That is the whole difference on all eight criterion lines: a hyphen bullet becomes an asterisk bullet. Line 28, the provenance line, is the ninth:

```
emitted: "_Serialized from this ticket's `onex_change_control` contract (`requirements[].acceptance[]`) by"
stored:  "*Serialized from this ticket's* `onex_change_control` *contract (*`requirements[].acceptance[]`*"
```

Underscore emphasis becomes asterisk emphasis, re-opened and re-closed around each inline code span, which is where the five extra characters come from. **Not one criterion's text changed.**

## Why the two now compare equal

The bullet character is consumed by the reader's own list-item grammar, `_LIST_ITEM_RE` at `src/onex_change_control/validation/ac_criteria.py:65`:

```python
_LIST_ITEM_RE = re.compile(r"^[ \t]*(?:[-*+]|\d+[.)])[ \t]+(.*)$")
```

`[-*+]` accepts either bullet and the capture group starts after it, so `- **AC1** — …` and `* **AC1** — …` yield the identical item text. That is the same reader the OMN-18236 validator and the `omnibase_infra` evidence closer resolve bindings with, which is why the gate was never affected by any of this and still exits 0 against the stored body.

So the comparison now asks the question that matters: does the body already carry these criteria, **as the reader that resolves bindings reads them**. Measured on the live ticket with this change in place, the serializer prints `UNCHANGED` and the gate exits 0.

The alternative was to emit whatever markdown Linear happens to normalise to. That is the wrong fix: it guesses at an undocumented third-party formatter and breaks the next time that formatter changes.

## Shape of the change

`_managed_span` returns the sentinel-delimited span, `_criteria_as_the_reader_sees_them` resolves any text through `criteria_by_label` plus `normalise_criterion`, and `_already_renders` compares the two.

**It compares an ordered list, not a mapping, and that is load-bearing.** `criteria_from_contract` renders in the contract's declaration order and refuses to sort, on the stated grounds that an author who declares `AC3` before `AC1` has made a choice a renderer should not quietly correct. A comparison keyed only by label would accept a body in the old order forever and leave the two halves of this module disagreeing about whether order means anything. The gate resolves by label and would pass either way, which is exactly why it needs a test rather than an argument. Measured directly: against a reordered model the mapping form returns equal and the ordered form does not.

Equality also holds in **both** directions on membership: a model that shrank does not match a body still rendering the criterion it dropped. A one-directional "every declared criterion is present" check would have left a stale criterion live in Linear under a label the contract no longer binds, and that case has its own test rather than riding on the edited-statement one.

**What this deliberately does not soften.** A criterion somebody retypes in the Linear editor inside the managed span reads back as different text and plans as `CHANGED`, so the next run overwrites it. That is the failure this fix could plausibly have introduced — a comparison loose enough to survive Linear's normaliser could also be loose enough to accept a hand edit, leaving the body and the contract saying different things with the gate green on the label. The direction of authority is unchanged: the contract is the model, the span is generated output, and "idempotent" must not quietly become "stops correcting the body". It has its own test.

No new grammar is added. The serialization module defines no regex at all, and a test asserts that.

## RED first, on this branch

The two defect tests were run against unmodified `origin/dev` before the fix existed and failed there:

```
FAILED test_replanning_a_body_linear_stored_changes_nothing
FAILED test_the_comparison_reuses_the_reader_rather_than_parsing_again
2 failed, 4 passed
```

The four that passed are the controls, and the first of them is the one that makes the rest mean anything: it asserts the premise directly — the stored fixture and the bytes this module renders are **not** byte-equal, and the difference is the bullet. Without it, the idempotence test could pass because the bytes happened to match and would prove nothing about normalisation.

After the change, eight pass: the two defect tests, the premise control, and five behavioural controls covering a rewritten criterion, a shrunk model, a reordered model, a hand-edited criterion inside the span, and a body carrying no span at all. The fixture is the real post-write body of OMN-18167, committed under OMN-19046 and reused here rather than copied, so there is one recorded answer to what Linear actually stores and not two.

## Stated limit

Only criteria are compared. A change confined to the heading or the provenance line no longer forces a rewrite, because neither is a criterion and neither is anything the gate reads. That is deliberate, it is the price of comparing meaning rather than bytes, and it is written where the comparison lives rather than left to be discovered.

## Verification

- 183 passed across the serializer, the OMN-18236 binding gate, the OMN-18333 pair, the OMN-19046 contract suite and schema purity.
- `mypy src/ --strict` clean, 184 files. `ruff check` and `ruff format` clean.
- Live end-to-end against the real ticket: serializer `UNCHANGED` exit 0, gate exit 0.
- `pre-commit` was run scoped to the changed files rather than `--all-files`, which exceeded a ten-minute budget on this host in an earlier pass on this repository; CI is the full gate.
