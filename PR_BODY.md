## Summary

OMN-19867: the Auto-Merge workflow's step "Check OCC eligibility preflight status" (`.github/workflows/auto-merge.yml`) reads the PR head once before its poll loop and stops at the first completed eligibility reading. The OCC writer pushes a companion PR as three commits (author; self-bind about 7s later; executed test_passes receipts 45-70s after that). On the self-bind head, eligibility concluded failure (receipts not pushed yet) or cancelled (superseded by the next push); the step exited 1; the final head went green with no event left to arm it. The ticket measured 13 of 15 sampled companions ending their only Auto-Merge run red.

## Change

On a poll (non-zero budget, writer App companion only) the step re-reads the live head via `gh pr view` on every reading, and a non-success completed verdict ends the loop only after the head has held still behind it for `ELIGIBILITY_FAILURE_SETTLE_SECONDS` (180). A new push resets the window. SUCCESS on the current head stays the only arming state. The zero-budget path (human PRs, check_suite) is unchanged and still exits 1 on its first failure reading.

## Tests

- New `tests/ci/test_auto_merge_companion_head_follow.py`: 7 tests, executing the step's own bash against a stub `gh` whose head moves on a script. Without the workflow change, 3 of the 7 fail (`test_settle_window_is_declared_on_the_step`, `test_moved_head_success_arms_after_a_failure_on_the_self_bind_head`, `test_cancelled_then_success_on_the_new_head_arms`); with it, 7 passed.
- Together with `tests/ci/test_auto_merge_companion_poll_omn17922.py`, `test_auto_merge_companion_budget_omn18797.py` and `test_auto_merge_rearm_on_rewrite_omn20042.py`: 49 passed; those three files unmodified.
- `pre-commit run --files` on the two changed files: no failures.

## Lab

Lab: host h101, lane omn19867-tix, command `uv run pytest` on the four files above in the lane worktree; observed 49 passed in 105.57s. The tests run the step against a stub `gh`; the live AC5 check (a companion arming through its own opened Auto-Merge run) cannot be observed before merge and is pending.

## Not covered

- AC5 live verification after merge
- The writer's three-commit push shape
- Who may be armed
- The product-PR heal

Closes OMN-19867
