## Summary

Add current-head PASS receipts for [OMN-15359](https://linear.app/omninode/issue/OMN-15359) product PRs [omnimarket #3042](https://github.com/OmniNode-ai/omnimarket/pull/3042) and [omnibase_infra #4240](https://github.com/OmniNode-ai/omnibase_infra/pull/4240). Their receipt gates currently report `pr_ticket_mismatch` because merged OCC #11708 predates these product heads.

## What changed

- Append `dod-OmniNode-ai-omnimarket-pr-3042` to `contracts/OMN-15359.yaml` and add its exact-head command receipt.
- Append `dod-OmniNode-ai-omnibase_infra-pr-4240` to the same contract and add its exact-head command receipt.
- Bind each receipt to its product PR number, full head SHA, branch, and a content predicate that is absent at that PR's base.
- Preserve every existing OMN-15359 contract item and receipt unchanged.

## How it was verified

- omnimarket head `d4e46ac64bbe0121aca42076c30bb4e5f200c9c6`: the GitHub Contents API probe requires the new `provision-target` action and returned `true`, exit 0. The same predicate at base `7352e78592985400851cf4793466130b7e5e0106` exited 1.
- omnibase_infra head `d801a500586d345d2d877aaa9e0caa400d31af66`: the probe requires the new `projection-delegation-writer` service and returned `true`, exit 0. The same predicate at base `cd2cc37bd48852d195eb18d3d6b08b04d973f064` exited 1.
- The two receipt check values are byte-identical to their contract entries. Recomputed per-entry hashes are `sha256:0cddd6daf30bb3def72a202893bd7e8b240473b1d3d9eb3e89e1020586bab305` and `sha256:90b24d673c2d118303d87ab7ba7ef3085f378fc63227b8f4202831e2dc4497b3`.
- The repository pre-commit path passed after rebasing the diff onto current `origin/dev`, including receipt honesty, receipt hardening, append-only contract verification, exact commit-SHA existence, acceptance-criterion binding, normalization symmetry, and corpus ratchets.

## Failure paths

- Head moves: each probe pins the full product head SHA, and the receipt records the same SHA and PR number. A changed product head needs a new receipt.
- Missing product change: each base-SHA negative control exits 1, so the predicate is not true before the feature exists.
- No answer: GitHub API failure makes the command nonzero; it cannot produce PASS.
- Incomplete or malformed receipt: the receipt honesty, schema, hash, repository-authority, and exact-SHA gates all passed and fail closed on those defects.
- Size: each probe returns one boolean line; no large output or transport truncation path is involved.

## Open defects

- These receipts become available to the product gates only after this OCC follow-up merges. The product PR bodies must cite this follow-up PR as their `Evidence-Source`.
- AC3's sanctioned live two-lane replay remains outstanding and is not claimed by these product-binding receipts.
- AC2 remains owned by [OMN-17886](https://linear.app/omninode/issue/OMN-17886).

## Local gates

- [x] No gate was bypassed — no `--no-verify`, no `SKIP=`, no `--no-gpg-sign`, and no hooks override.
- [x] Any hook that failed was fixed at the input and rerun through the normal path.

## Not in this change

No product source, runtime, database, tenant, ACL, infrastructure, synthetic event, ticket status, or existing OCC evidence is changed.
