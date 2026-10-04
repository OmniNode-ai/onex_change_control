# Pull Request Description

## Summary
This change resolves OMN-20482 by updating the concurrency group in `.github/workflows/auto-merge.yml` to include `github.event_name`, preventing cross-event cancellations between `check_suite` and `pull_request` runs.

Lab: host=h201 (hostname omninode-pc) lane=automerge-cancel-race command=`uv run --frozen pytest tests/ci/test_auto_merge_concurrency_event_class_omn20482.py tests/ci/test_auto_merge_companion_budget_omn18797.py tests/ci/test_auto_merge_rearm_on_rewrite_omn20042.py tests/ci/test_auto_merge_ready_for_review_omn19216.py tests/ci/test_auto_merge_bot_companion_arming_omn17922.py tests/ci/test_auto_merge_hold_omn18179.py` observed 98 passed; before the change the new test file failed 7 of its 10 tests, after it all 10 pass.

## Cause
A `check_suite` run and a `pull_request` run for the same PR shared the concurrency group `Auto-Merge-<pr>`. Because `cancel-in-progress` was set to true, the `check_suite` run cancelled the `pull_request` run at `actions/checkout`.

**Evidence:**
*   **Annotation:** The cancelled job contained the annotation: "Canceling since a higher priority waiting request for Auto-Merge-12584 exists".
*   **Payload:** The `check_suite` run's logged event payload (`CHECK_SUITE_PRS`) named PR 12584.
*   **SHA Mismatch:** The `check_suite` run executes at the default branch SHA, so its "Enable Auto-Merge" check-run lands on `dev`'s SHA, never on the PR head. The head retains only the cancelled copy, causing CI Summary to fail closed.
*   **Verified Pairs:**
    *   Cancelled `pull_request` run 37153470685 / Cancelling `check_suite` run 37153480335 (PR 12584)
    *   Cancelled `pull_request` run 37153432793 / Cancelling `check_suite` run 37153444180 (PR 12624)
    *   Cancelled `pull_request` run 37155933509 / Cancelling `check_suite` run 37155941023 (PR 12584)

## Fix
*   **Workflow Change:** Updated the concurrency group in `.github/workflows/auto-merge.yml` from `${{ github.workflow }}-${{ <pr number chain> }}` to `${{ github.workflow }}-${{ github.event_name }}-${{ <same chain> }}`. `cancel-in-progress` remains true.
*   **New Test:** Added `tests/ci/test_auto_merge_concurrency_event_class_omn20482.py`. This test renders the group expression and replays three real run pairs through GitHub's concurrency rules (one running and one pending run per group).
*   **Behavior:** After the fix, a newer push cancels the older `pull_request` run, and a newer `check_suite` completion cancels the older `check_suite` run for that PR. No event class cancels another.

## Measurement
Data collected from runs created between 2026-10-03T00:40Z and 2026-10-04T01:30Z for this repo's Auto-Merge workflow:
*   **Cancellations:** 343 of 785 `pull_request` runs by the `onexbot-occ-writer` App were cancelled.
*   **Duration:** Median duration was 10 s for cancelled runs and 60 s for the 411 that succeeded.
*   **Impact:** 21 heads had CI Summary failing only on the cancelled "Enable Auto-Merge" copy (every other non-green check-run on the head belonged to the Auto-Merge workflow itself).
    *   8 of those heads are the current head of their PR (PRs 12624, 12623, 12662, 12631, 12649, 12650, 12655, 12635).
    *   13 were since superseded by a later push.
    *   Total distinct PRs affected: 15.

## Not runtime-affecting
*   Changes are limited to the CI workflow and test files; the `.201` compose lanes are untouched.
*   Surfaces exercised: the workflow concurrency expression, the specified pytest files, and pre-commit hooks (runner routing, divergent automation PR, required-check workflow checks, ruff).
