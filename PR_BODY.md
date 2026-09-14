## OMN-18333 — an absent binding holds, a partial binding is refused

Sequencing correction to the binds-every coverage rule merged at 06:13Z. Ruling
of 2026-09-14, recorded at `docs/tracking/ROLLING_WORK_LEDGER.md:7711`.

### The deadlock, measured

The merged rule refuses a contract that binds NOTHING on the same terms as one
binding three of four. No producer transcribes a binding yet, so every companion
of every falsifier-declaring ticket binds nothing — including the companion of
the omnimarket change that makes the producer transcribe. The gate refused the
fix to the thing it was refusing.

Reproduced on this change's own contract, against the real ticket body:

| Gate | `contracts/OMN-18333.yaml` (binds nothing) |
|---|---|
| merged (`dev`) | exit 1, 5 findings |
| this branch | exit 0, 0 refusals, 5 holds |

And on the real blocked companion's contract, both revisions of it:

| Contract | merged gate | this branch |
|---|---|---|
| autobinder-minted (binds nothing) | exit 1, 12 findings | exit 0, 12 holds |
| same plus a hand-added claim of 6 of 12 | exit 1, 6 findings | exit 1, 6 refusals |

The second row is the positive control: partiality is still refused, and the
same bytes separate on the same run.

### The rule

Both coverage findings carry a severity. A contract binding **no** criterion at
all reports one hold per unbound criterion and does not fail the job. A contract
binding **something** and omitting a declared criterion fails, and an unbindable
suffixed label under a binding contract fails. The discriminator is the
contract's own content, evaluated every run — no cutover date, no allowlist, no
knob, no new input, and nothing to switch off the day transcription starts
working.

The line is drawn at asymmetric harm, not leniency. Partiality is the only shape
that reads as a pass while starving the closer: nothing downstream distinguishes
"bound two of four" from "bound all of them". Absence is held by the evidence
closer already (OMN-18330) and is visible to anyone looking — and it is still
reported here, once per criterion, so it does not become silent.

The unreachable-ticket verdict mirrors the same line. Fail-closed means never
stricter than any outcome the readable path can reach; refusing a binds-nothing
contract because Linear was unreadable would re-create this deadlock on every
fetch failure while protecting nothing. A contract that claims a criterion is
still refused when its ticket cannot be read.

### Evidence

- RED first, behaviourally: 7 failed / 11 passed with the API present and the
  discriminator absent; 100 passed across the three gate test files after.
- `mypy --strict` clean on both changed modules; `pre-commit run --all-files`
  exit 0.
- Two legs of the merged rule's own test file are deliberately REVERSED, each
  carrying the reason in its own docstring rather than being deleted:
  binding-none is now reported-not-refused, and the unreadable-ticket leg is
  split into a claiming half (red) and a binds-nothing half (hold).
- A refusal's rendered line is byte-identical to before, asserted directly, so
  no consumer grepping the job output changes meaning. Only a hold is prefixed.

### Wiring — AC5 of OMN-18333 holds

No new workflow, no new job, no new required status check. The rule stays inside
the existing binding-acceptance job, whose failure `CI Summary` already turns
into a failure of that required context. `required_status_checks` on `dev` is
untouched by this change.

dod_evidence for OMN-18333 is the contract in this repo at
`contracts/OMN-18333.yaml`.
