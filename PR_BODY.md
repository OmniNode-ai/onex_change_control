Part of OMN-19046. The contract data batches are separate pull requests; this one is the producer only.

## What is missing

OMN-18270 built the writer that renders a contract's `requirements[].acceptance[]` into its ticket's acceptance-criteria section. OMN-19038 built the producer at the other end, so every contract the autobinder mints from here on carries its criteria. Neither reaches the contracts that already exist.

Measured on `origin/dev` 2026-09-21:

| contracts | binding a criterion | carrying `requirements` |
|---|---|---|
| 9,300 | 374 | 1 |

Those 374 bindings name criterion text that exists nowhere in the contract corpus. This adds the one-time backfill.

## What it does

Reads each bound ticket once, writes `requirements[].acceptance[]` keyed to exactly the labels `binds_ac` names, in binding order, and **splices** the block in rather than re-serializing the file. Round-tripping 9,300 contracts through a YAML dumper would reformat files this change has nothing to say about, and a diff nobody can read is a diff nobody reviews. A test asserts every other byte is returned unchanged.

Dry run is the default; `--write` is the only thing that touches a file. The command takes the same `{ticket_id: body}` JSON the sibling gate CLIs take, because this package's imperative-contract guard blocks raw HTTP from `src/`.

Idempotence follows OMN-19054's rule: compare through the reader, never by bytes. A body whose bullets Linear renormalised is not a change, and a re-run writes nothing.

## The refusals are the substance

A backfill's failure mode is writing something plausible for every ticket and reporting a big number. Measured across all 374 bound contracts against live ticket bodies:

| outcome | count |
|---|---|
| would fill | 361 |
| already agree | 4 |
| refused | 8 |

All 8 refusals are real defects, not noise, and each names its ticket:

- **`ac_requirements_stale_pin`, 4.** The binding pins a hash the criterion's current text does not produce, so the text was edited after acceptance. Writing the live text would make the model and the pin describe different sentences *while looking like a repair*. This is the sharpest rule: the backfill can produce text, the text is real, and writing it would still be wrong. A stale binding is re-accepted by a person.
- **`ac_requirements_duplicate_label`, 2.** Two criteria resolve to one label and nothing recorded which was accepted.
- **`ac_requirements_unknown_criterion`, 2.** A bound label the body does not carry. The binding is already unresolvable; inventing text would hide that.
- **`ac_requirements_ticket_unreadable`, 0.** A bound ticket with no body supplied. "I could not read the ticket" must not resolve to "so it needs no criteria".

## Two corrections the corpus forced, both in the code with their measurements

**The duplicate detector had to obey the reader's precedence.** A first version treated a whole-body fallback line as a competing variant for a label the criteria section had already resolved. It refused **48 of 374**, and on inspection almost none were ambiguous: the bodies carry lane verdict annotations written elsewhere in the ticket, `AC2 MET 2026-09-15T17:48Z on the worker` and similar, which is the OMN-18404 habit. The reader skips the fallback for a resolved label, so a writer that called those ambiguous would refuse the corpus over a disagreement with a reader that does not exist.

**The pin arbitrates a genuine duplicate.** `criterion_hash` on a binding record is the digest of the criterion at the moment a person accepted it. When it matches the text the reader resolves, the author's own recorded acceptance has answered which variant was meant, and taking that text is evidence rather than a guess. That took the remaining refusals from 8 duplicates to 2 — the two where nothing arbitrates.

Both are pinned by tests, including the positive control that a single-criterion body is still accepted, so the duplicate rule discriminates instead of refusing everything.

## RED and GREEN

RED against unmodified `origin/dev` in a throwaway worktree, not inherited: collection error, `No module named 'onex_change_control.serialization.ac_requirements'`. That is the honest RED for a module that does not exist, and it is reported as that rather than dressed up as behavioural failures.

GREEN: 16 on the new file; 153 across the serializer, binding-acceptance, idempotence, C6 and `binds_ac` gate suites; `mypy --strict` clean over 186 files; `ruff check` and `ruff format` clean.

## Scope and what is owed

No contract data lands here. The batches follow as their own pull requests under child tickets, sized from the first batch's measured wall clock.

This lane wrote the producer, so it is not the actor to author the evidence for it: a second actor adds the `dod_evidence` item and its receipt. The existing contract and receipts on this ticket cover the earlier C6 work and do not cover this change.
