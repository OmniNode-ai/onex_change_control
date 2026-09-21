## OMN-19099

The roster reads are batched into GraphQL documents. Measured: **18 requests per sweep against 50**, pinned by a test.

## What failed, measured

This gate carries no `paths:` filter and no `if:`, by design, so it runs on every pull request in this repo. It then re-read identical roster data every time. Measured live against the eight-repo roster on 2026-09-21:

| Call | Requests |
|---|---|
| App token mint | 1 |
| `tags?per_page=100 --paginate` x 8 repos (148/122/113/53/19/36/44/36 tags) | 11 |
| `compare?per_page=1` total probe x 8 | 8 |
| `compare?per_page=100 --paginate` where commits exist (5 of 8) | 6 |
| `commits?sha=dev&path=... --paginate` x 3 packaged paths x 8 repos | 24 |
| **per run** | **50** |

240 runs between 12:00Z and 21:00Z that day is about 12,000 requests, roughly 2,300 in the 18:00Z hour, against an installation ceiling of either 6,050 or 15,000 per hour. That is 22% of every App-authenticated run in the window, from one check.

## What is batched

**Tags.** Eleven paginated REST reads become one GraphQL document, or two when a repo carries more than 100 tags. Semver ordering is still applied locally and still not taken from the API: GraphQL can order refs alphabetically or by tag commit date and neither is semver, since alphabetically `v0.4.99` sorts above `v0.4.183`. Ordering by commit date and trusting the first page would have been cheaper and is exactly the shortcut the module contract forbids.

**Packaged-path history.** Twenty-four REST reads become one document, using `history(path:, since:)`.

Measured together: **18 requests for an eight-repo roster**, pinned by `MAX_REQUESTS_PER_SWEEP = 20` in the test file.

## What is deliberately not batched

`compare` stays on REST, untouched. Its truncation refusal, where a collected commit count short of the API's own `total_commits` refuses rather than under-reporting, is the check that stops a capped range reading as a clean repo. Re-implementing that against a different transport to save eight requests is a bad trade in a gate that blocks the whole org. The existing truncation test passes unmodified.

## The route that was considered and refused

The obvious cheaper step is to read the packaged set off the `compare` payload already in hand. That does not work, and the module docstring already said so under its own heading: `compare`'s `files` array is the **aggregate** diff of the whole range, so it answers "did anything packaged change across these commits" when the question is "**which** commit is the oldest packaged one, and therefore how long has the debt been sitting".

An implementation using it would have reported the range's oldest commit rather than the oldest packaged one. `test_the_oldest_packaged_commit_is_reported_not_the_oldest_commit` is the control for precisely that: a range whose oldest commit is docs-only and whose newest is packaged must read FRESH at 2h, not STALE at 96h.

## Ordering

There are three phases rather than two because each repo's `since` is that repo's own earliest unreleased commit, which only `compare` discovers. So the history document cannot merge into the tag document.

## One dead repo is still one error row

GraphQL answers a partial document with data for the aliases that resolved and an `errors` array naming the ones that did not. Attributing that to the whole document would recreate, at the transport layer, the same one-event-reads-as-many-failures conflation that made the 2026-09-21 exhaustion slow to diagnose. Errors are mapped back per alias, and an error with no usable path is attributed to every alias in the document rather than dropped, because an unattributable failure means we do not know which repos were read.

## Behaviour that is unchanged

The gate decides the same thing on the same inputs: same roster, same `max_age_hours`, same enforced flags, same verdicts. The never-skip contract holds and `test_gate_workflow_runs_unconditionally_on_pull_request` is untouched and green. No workflow file is modified.

## Tests

29 passing. Four are new:

| Test | Asserts |
|---|---|
| `test_a_full_roster_sweep_stays_inside_the_request_budget` | 18 invocations against a pinned budget of 20 |
| `test_the_batch_does_not_scale_with_roster_size` | going from 2 repos to 8 adds only compare reads, never more documents |
| `test_the_oldest_packaged_commit_is_reported_not_the_oldest_commit` | per-commit attribution survives batching |
| `test_one_unreadable_repo_in_a_batch_is_one_error_row` | a partial document still measures the repos that resolved |

The stub now answers both transports and assembles the multi-alias response itself, which is the only way to exercise real batching: one document carries every repo, so a fixture keyed by whole-query substring could never represent it.

## Notes for review

`scripts/validation/**` is excluded from ruff in `pyproject.toml`, so the script is not reformatted and its diff is additions plus the lines actually replaced. The test file is not excluded and is ruff clean and formatted.

`uv run mypy` passes on both files. `pre-commit run --files` on both passes.

## dod_evidence

- `uv run pytest tests/test_check_release_staleness.py` — 29 passed, including the 4 new controls and the unchanged truncation and never-skip tests.
- Budget measured empirically by lowering the constant to 0 and reading the failure: `18 gh invocations for an 8-repo roster`.
- `uv run mypy tests/test_check_release_staleness.py scripts/validation/check_release_staleness.py` — no issues.
- `uv run ruff check tests/test_check_release_staleness.py` — all checks passed.
- `pre-commit run --files scripts/validation/check_release_staleness.py tests/test_check_release_staleness.py` — no failing hooks.
