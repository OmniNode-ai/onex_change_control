## Summary

- onex_change_control dev head 0d170b50bb was red on CI Summary. No merge introduced a defect.
- ci.yml has `cancel-in-progress: true` and its concurrency group was `${{ github.workflow }}-${{ github.event.pull_request.number || github.ref }}`. On a push event github.ref is refs/heads/dev, so every merge to dev shared one cancelling group and cancelled the previous merge's run.
- The push runs for six consecutive dev merges after ce794f5122 were cancelled by the next merge.
- CI Summary keeps `if: always()`, so on cancelled run 38522163882 (0d170b50bb) it read the cancelled gates (Supersession Binding Ratchet, Tests, Schema Purity & Naming Check) and concluded failure at 06:32:40Z. The successor run 38522777064 (8d77350c78) sat pending from 06:32:07Z to 06:42:46Z.
- At 8d77350c78 the gates that were cancelled pass on the lab: supersession corpus, yamlfmt contamination corpus, check-schema-purity and supersession wiring all exited 0 (h202, work unit lw-4039c0f94a0c464c).

## Changes

- .github/workflows/ci.yml: the concurrency group becomes `${{ github.workflow }}-${{ github.event.pull_request.number || (github.event_name == 'push' && github.sha) || github.ref }}`; cancel-in-progress stays true; an added comment cites OMN-20074. A push run is keyed on its own sha. Pull-request, merge-group and dispatch runs keep their existing groups. No gate, job or required context is added, removed or skipped.
- tests/ci/test_cancellation_is_honored_omn18580.py: new test test_ci_push_runs_are_not_superseded_by_the_next_merge pins the push arm. The existing test test_cancel_in_progress_groups_stay_pull_request_scoped only checks for the substring github.ref, which the old group satisfied.
- 2 files plus this PR_BODY.md.

## Proof

Lab: host=h202 lane=omn20074-occdv command=`uv run pytest tests/ci/test_cancellation_is_honored_omn18580.py tests/ci/test_ci_summary_gate.py -q -p no:randomly` plus `pre-commit run --files` on the two changed files at code commit f1a2b67383 observed=150 passed, 1 skipped, pytest exit 0, pre-commit exit 0 (work unit lw-921234e60b68485b, unit exit 0).

- RED before the change: the new test failed with AssertionError on the old group string (1 failed, 5 passed); after the change 6 passed.
