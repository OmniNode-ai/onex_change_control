## Summary

This repairs the merged append-only OCC evidence for [OMN-20008](https://linear.app/omninode/issue/OMN-20008) so product receipt-gate validators can consume it.

## What changed

- Removes only the unsupported `artifact_sha256` list emitted by the landing worker from the three OMN-20008 deploy-probe receipts.
- Preserves the merged contract, current contract hash, contract-entry hashes, pinned product heads, probe outputs, PASS statuses, and all other receipt fields byte-for-byte.
- Keeps emergency bypass disabled and preserves the existing acceptance vocabulary.

## How it was verified

- The local `ModelDodReceipt` validator accepts all three repaired receipts (`MODEL_VALIDATE_PASSED 3`).
- The yamlfmt contamination gate passes for the contract and all three repaired receipts.
- `git diff --check` passes, and the diff is exactly three receipt files with nine removed lines.
- Product CI identified the defect after OCC#12746 landed: Dash verify run `37188176996` rejected the landing-worker `artifact_sha256` list as an extra field. The repair does not alter the product implementation evidence.

## Local gates

- Signed commit and normal repository hooks are required before push.
- No gate was bypassed.

## Not in this change

- No product source, migration, dashboard code, or merged contract is rewritten.
- Only the three landing-worker receipt fields described above are removed.
- No PR is merged and no Linear status is changed.

## Failure paths

- A missing or changed symbol at any pinned head makes its corresponding deploy probe exit non-zero.
- Product and infrastructure CI remain responsible for executing the declared behavior checks in their own repositories.
- The evidence is incomplete if any size, base, or live-verdict check cannot be measured; no unresolved check is treated as green.
- A no-answer or unavailable live check is reported as unverified rather than treated as green.
- n/a: no-answer is reported as unverified rather than treated as green.

## Open defects

none

Overlap-Reviewed: #12744 and #12746 — #12744 carries the original generated OMN-20008 contract/receipt entries for OmniMarket #3361; #12746 merged the three deploy probes and current contract. This repair changes only the validator-incompatible landing-worker field in those three receipts.

Evidence-Source: OCC#12746

Evidence-Ticket: OMN-20008
