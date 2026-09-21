## OMN-19098

An API quota refusal now has its own verdict, its own exception and its own machine-readable outcome token. It still fails closed.

## What failed, measured

On 2026-09-21 the `onexbot-occ-writer` installation bucket (id 148180820) emptied at 19:41Z. This gate failed four times, at 19:40:57Z, 19:41:19Z, 19:41:23Z and 19:43:32Z, and nowhere else in the day. Each run emitted seven or eight per-repo ERROR rows, one per roster repo, because every roster read failed together.

Verbatim, from run 35646352206:

```
gh: API rate limit exceeded for installation ID 148180820. ... timestamp 2026-09-21 19:41:22 UTC (HTTP 403)
```

Nothing in the output distinguished that from eight genuinely unreadable repos. The single infrastructure event was diagnosed four separate times while it cascaded into the summary check and blocked unrelated merges. Measured cost: about an hour.

The cause is one branch in `_gh`, which collapsed every non-zero `gh` exit into one shape:

```python
if proc.returncode != 0:
    detail = (proc.stderr or proc.stdout or "").strip().replace("\n", " ")[:400]
    raise ProbeError(f"gh {' '.join(args)}: exit {proc.returncode}: {detail}")
```

There was no rate-limit branch anywhere in the file.

## What this changes

- `QuotaExhaustedError`, raised when stderr carries GitHub's primary or secondary rate-limit signature.
- `QUOTA_EXHAUSTED` verdict, distinct from `ERROR`.
- `OCC_QUOTA_EXHAUSTED` outcome token, printed on its own line in stdout, in the JSON payload and in the step summary, so recognising a breadth event never requires parsing a human sentence or opening a job log.
- The bucket's reset time, read from `/rate_limit`, which GitHub documents as not counting against the limit it reports, so it is free to call at the exact moment the bucket is empty.

## Three things it deliberately does not do

**It does not make quota exhaustion a pass.** Exit code is still 1. A run that could not read the roster has not shown the roster is fresh. Pinned by `test_quota_403_still_fails_closed`.

**It does not retry.** A retry inside an exhausted window adds load to the saturated bucket. A quota row therefore also suppresses the positive control, which is a second full sweep that could not succeed anyway. Pinned by `test_quota_exhaustion_suppresses_the_positive_control`.

**It does not skip the job.** The summary check's L4 layer is success-only and a skipped external context fails closed, so the job still runs unconditionally on every pull request. `test_gate_workflow_runs_unconditionally_on_pull_request` is unchanged and green.

## Why `QuotaExhaustedError` subclasses `ProbeError`

Every existing `except ProbeError` site in this module is a fail-closed site. A sibling class would have required editing each catch to re-establish that, and any one catch missed would silently have become a pass. Subclassing makes fail-closed the default and the distinct handling the explicit opt-in, which is the safe direction for this to be missed in. The type is still distinct, and the `except QuotaExhaustedError` arm in `evaluate` precedes the broader arm on purpose, with a comment saying why.

## Tests

Eight new tests, 34 passing in the file. The pair that matters is the positive and negative control together, because either alone is satisfied by a relabelling rather than a classifier:

| Test | Asserts |
|---|---|
| `test_quota_403_is_its_own_verdict_not_a_generic_error` | the recorded 403 yields the quota verdict and token |
| `test_a_non_quota_gh_failure_is_still_a_generic_error` | a 404 still yields `ERROR` and never the token |
| `test_a_real_staleness_finding_does_not_acquire_the_quota_token` | an ordinary stale row is unchanged |
| `test_quota_403_still_fails_closed` | exit 1, no pass line |
| `test_quota_403_reports_the_reset_time_when_it_can_read_it` | reset parses as a real timestamp |
| `test_unreadable_reset_reports_unknown_never_an_invented_time` | an unreadable reset stays empty |
| `test_quota_error_type_is_distinct_from_probe_error` | distinct type and the subclass relationship both hold |
| `test_secondary_rate_limit_is_also_a_quota_outcome` | the burst limit is covered too |

The 403 fixture is the exact stderr GitHub returned, recorded rather than paraphrased: the classifier keys on the message, so a test that invents its own wording proves nothing about the message we actually receive.

## Notes for review

`scripts/validation/**` is excluded from ruff in `pyproject.toml`. An earlier pass formatted it anyway and rewrapped unrelated pre-existing lines; that was reverted, so the diff on that file is 205 added lines against 4 replaced. The test file is not excluded and is ruff clean.

`pre-commit run --files` on both changed files passes with no failures.

No workflow file is touched, so this does not engage the branch guardrail on the writer-App path.

## dod_evidence

- `uv run pytest tests/test_check_release_staleness.py` — 34 passed, including the 8 new controls and the unchanged never-skip test.
- `uv run ruff check tests/test_check_release_staleness.py` — all checks passed.
- `pre-commit run --files scripts/validation/check_release_staleness.py tests/test_check_release_staleness.py` — no failing hooks.
- The 403 body in the fixture is quoted from a real failed run in this repo on 2026-09-21, not synthesised.
