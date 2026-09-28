## Summary

Bind the replay-harness part of [OMN-15359](https://linear.app/omninode/issue/OMN-15359) AC3 to an executable check and its measured PASS receipt. This writer-App PR supersedes the human-authored [OCC #11691](https://github.com/OmniNode-ai/onex_change_control/pull/11691); the live two-lane replay is still owed.

## What changed

- Append `ac3-replay-behaviour-proof` to `contracts/OMN-15359.yaml`, reusing the existing autobind check command for merged [omnimarket #2990](https://github.com/OmniNode-ai/omnimarket/pull/2990).
- Add `drift/dod_receipts/OMN-15359/ac3-replay-behaviour-proof/test_passes.yaml`, attesting the exact check against current omnimarket dev `8db69992fc7a6d68feeb500c836e99a940e998b6`.
- Keep the contract's live lane-replay gap explicit. No existing evidence item is changed.

## How it was verified

- The receipt probe resolved product commit `8db69992fc7a6d68feeb500c836e99a940e998b6` through GitHub, then ran `uv run pytest tests/test_omn15359_ac3_replay_falsifiers.py tests/test_omn15359_ac3_replay_real_postgres.py -q`: **12 passed in 3.22s**, exit 0, with PostgreSQL 16.15 and pytest 9.1.1. The test includes nonempty two-tenant key comparison and incomplete-read falsifiers.
- The same test command separately passed at the current dev checkout: **12 passed in 3.10s**. The shared .201 writer's installed handler SHA-256 `5576f111356a02f1ce70e81ecc9eb5e98bfc286d9728003df87f99cbbeae64e7` equals that dev checkout's handler hash; this is version compatibility only, not a live replay receipt.
- `yamlfmt` reached a fixed point. The signed commit's pre-commit hook passed, including receipt honesty, receipt hardening, acceptance-criterion binding and evidence-commit existence. The first hosted eligibility run found the receipt under the wrong filename; the corrected `test_passes.yaml` is now discovered by the same eligibility CLI locally. Hosted CI on the corrected commit is pending.

## Failure paths

This PR adds a static evidence receipt and no runtime receipt writer. The local mint script checks the exact contract command, clean product head, GitHub commit readback, exit 0 and a positive test summary before writing. A malformed first receipt was caught by the contamination gate, and an unresolvable probe commit was caught by receipt hardening; both were corrected before commit. Size: oversized output was not injected, and the short local output has no transport cap. No answer: a nonresponding process was not injected. Incomplete verdict: the committed receipt carries exit 0, a positive pass summary and the required hashes; receipt hardening passed.

## Open defects

- AC3's live two-lane proof is outstanding. Read-only .201 checks found **0** `public.delegation_events` rows and no delegation projection writer container in the operator lane; the shared dev lane has **2,342** rows and a writer. The operator lane needs an owner-provided writer and sanctioned synthetic-event route before fixture publication. The live replay is not claimed by this PR.
- AC2 remains with [OMN-17886](https://linear.app/omninode/issue/OMN-17886). Jake also identified a hosted `node_dod_verify` AC4/AC5 execution defect on [OMN-15359](https://linear.app/omninode/issue/OMN-15359); this PR does not repair that runner.

## Local gates

- [x] No gate was bypassed — no `--no-verify`, no `SKIP=`, no `--no-gpg-sign`, no `core.hooksPath` override. The normal pre-commit and pre-push hooks ran.
- [x] Any hook that failed was fixed at the input, not worked around.

## Not in this change

No synthetic event or replay copy was written on either .201 lane, no ACL was changed, and no ticket status was changed. Harness key/count parity does not prove full row-value equality or the required live replay.
