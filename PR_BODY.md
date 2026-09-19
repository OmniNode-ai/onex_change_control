OMN-18797

## What broke

OMN-17922's second pass (this repo's #10187, squash `454b013287`) gave a bot OCC evidence
companion a 180-second poll for its own `occ-preflight / eligibility` reading on its
`pull_request: opened` run, and made an exhausted budget defer "to the existing check_suite
path". Under a merge burst both halves fail.

Measured on this repo, 2026-09-19. Companion #10322 (evidence for `OmniNode-ai/omniclaude#2255`):

| event | time (UTC) |
| -- | -- |
| companion opened by the writer App | 01:08:45 |
| Auto-Merge run 35411715476 resolves `arm=true companion=true` | 01:08:49 |
| 18 readings, every one `status=missing conclusion=missing` | 01:08:58 → 01:12:07 |
| budget exhausted, `defer=true`, run ends **SUCCESS** with the PR unarmed | 01:12:07 |
| `occ-preflight / eligibility` check-run CREATED | 01:13:56 |
| that check STARTED | 01:13:59 |
| that check SUCCESS | 01:14:35 |
| merged by hand, `autoMergeRequest` still null, zero arming events in its timeline | 01:38:50 |

The gate the poll waits for was created 112 seconds after the poll gave up. The job was
queued, not slow: `actions/runs/35411720602/jobs` reports `created_at=01:13:56Z` against a
run whose `startedAt` is `01:08:54Z`.

The stated fallback did not arrive. Between 01:02:31Z and 02:03:56Z not one Auto-Merge run
fired on any event, while at least six check suites completed on that head. The check_suite
path is real (runs 35414814097, 35411370615, 35408670382 each resolved a companion) but it
is sparse and not one-per-PR. The companion stayed mergeable and unarmed for 26 minutes.

## What this changes

1. **The companion budget is 900s, and covers `workflow_dispatch`.** The measured worst case
   is 5m05s from open to the eligibility job being created, against a budget that expired at
   3m22s. The ordinary case is unaffected: the poll exits on the first terminal reading in
   ~16s. Covering the dispatch path makes `gh workflow run auto-merge.yml -f pr_number=N` a
   real recovery rather than one more single reading.
2. **An exhausted budget on the companion path fails the run.** An unarmed companion behind
   a green run is indistinguishable from one nothing ever looked at. The error names the PR,
   the head, and the command to arm it. Auto-Merge is not a required context on `dev`
   (read back live: the four are CI Summary, required-check-skip-guard / check-skip-vectors,
   verify / verify, occ-preflight / eligibility) and this job is not in CI Summary's
   `EXPECTED_EXTERNAL_CONTEXTS`, so the red raises an alarm without blocking anything. A
   later check_suite event still arms.
3. **The concurrency group is keyed per PR on every path that carries one.** It was
   `${{ github.workflow }}-${{ github.event.pull_request.number || github.ref }}`, and that
   property is empty on the dispatch and check_suite paths, so every such run for every PR
   collapsed into one `Auto-Merge-refs/heads/dev` group under `cancel-in-progress: true` —
   cancelling the only automatic re-attempt this workflow has.

Nothing about WHAT arms or WHO arms moves: SUCCESS is still the only arming reading, a
concluded FAILURE still exits 1, and the governance-path and OMN-18179 hold gates are
untouched.

## Deliberately not done

Re-dispatching this workflow on exhaustion is the shape that would survive a lost runner.
It needs an identity that can create a `workflow_dispatch`, and there is none.
`secrets.GITHUB_TOKEN` cannot (Actions-recursion prevention drops the run), the
`onexbot-occ-writer` installation 148180820 holds `actions: read` and `onexbot` 123040063
the same — read live from `/orgs/OmniNode-ai/installations` on 2026-09-19 — and the only
remaining candidate is `CROSS_REPO_PAT`, whose reads in this job are ratcheted at exactly
two by OMN-16373. Widening that ratchet or granting the App `Actions: write` is an
authorization decision and not this change's to make. The exact missing permission is
`Actions: write` on installation 148180820.

## Evidence

RED first. `tests/ci/test_auto_merge_companion_budget_omn18797.py` against this branch's
workflow: 16 passed. The same file against `origin/dev`'s workflow (restored path-scoped,
no shared stash): 7 failed, 9 passed — including both behavioural cases, the companion whose
check never starts and the one whose check never concludes.

Local, in the worktree:

```
uv run pytest tests/ci/ -q                    # 336 passed
uv run ruff format / ruff check / mypy --strict   # clean on the new file
pre-commit run --files .github/workflows/auto-merge.yml tests/ci/test_auto_merge_companion_budget_omn18797.py   # all Passed
```

The OMN-17922 poll suite is kept as the regression fence for the unchanged paths and passes
unmodified: a human PR still takes one reading and defers green, and the check_suite path
carries no budget so it cannot reach the new failure.

Lab-first does not apply: this is CI workflow tooling with no runtime surface on any lane.
