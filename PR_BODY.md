## Summary
The check-receipt-hardening pre-commit hook previously resolved every receipt commit_sha with `gh api --include repos/<repo>/commits/<sha>`. Inside a lane, the gh shim refuses that read, the resolver records UNAVAILABLE, and the hook exits 2, so no lane could commit a receipt. FRICTION rows 2026-10-11T08:36:53Z and 2026-10-11T20:11:12Z record it (three occurrences).

This change implements the operator ruling from 2026-10-12T05:03:28Z, which requires that each gate added since 2026-10-08 whose replacement path does not work end to end gets a working path. The resolver in `src/onex_change_control/validation/commit_sha_resolver.py` now tries two local sources before falling back to the REST read.

## Behaviour
- The resolver tries two local sources before REST: the canonical clone of the claimed repository under OMNI_HOME, and the PR watcher state file.
- For the canonical clone, the resolver returns REACHABLE_REMOTE when an origin remote-tracking ref of that clone contains the sha.
- For the PR watcher state file (located at $ONEX_PR_WATCHER_STATE, else OMNI_HOME/.claude_scratch/pr-watcher/state.json), the resolver returns REACHABLE_REMOTE when the watcher recorded the sha as that repository's PR head or merge commit.
- Neither local source ever answers MISSING; a sha neither holds falls back to the REST read, which is the only path to MISSING.
- CI sets no OMNI_HOME, so REST stays the only path there.
- Only OmniNode-ai repositories are read locally.
- A local hit spends no REST budget and is answered even after a REST failure halted the session.

## Not changed
- The REST read and its classification.
- The receipt rules themselves.
## Proof

Lab: host=h101 lane=omn20911-f524 command=`check_receipt_hardening.py --paths-file0 <5 receipts from 2026-10-11> --commit-sha-inventory <tmp>/inv.json`, run in the lane (the h101 gh shim refuses the commit read) observed=without OMNI_HOME exit 2, omniclaude cc7d5a55e2b8 UNAVAILABLE and omnimemory 57038a2c8066 NOT_ATTEMPTED, remote_calls 1; with OMNI_HOME exit 0, both REACHABLE_REMOTE, remote_calls 0 (both shas held by the PR watcher state, neither by a canonical clone origin ref).

Tests on h101: `tests/unit/validation/test_commit_sha_resolver.py` 47 passed (11 new; the file failed to collect against dev before the change), `tests/test_check_receipt_hardening.py` 125 passed; ruff and `mypy --strict` clean on the resolver and the test file.

Companion inventory and the shim-side logged fallbacks: OmniNode-ai/omniclaude#2696.
