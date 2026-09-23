## Summary
Record the existing [OMN-17374](https://linear.app/omninode/issue/OMN-17374) PR #2259 behavior readback as manual OCC evidence so the contract no longer leaves that proof marked OWED.

## What changed
Added one manual evidence item bound only to AC4 in `contracts/OMN-17374.yaml` and its durable receipt under `drift/dod_receipts/OMN-17374/`. The receipt cites the recorded Sep 1 migration readback and Sep 22 staging run, including positive and negative controls and the migration checksum. No ticket status, application code, migration, or staging state changed.

## How it was verified
- `scripts/validation/check_contract_dod_authoring.py contracts/[OMN-17374](https://linear.app/omninode/issue/OMN-17374).yaml` — PASS, 1 contract checked.
- `validate-yaml contracts/[OMN-17374](https://linear.app/omninode/issue/OMN-17374).yaml` — PASS.
- `scripts/validation/check_receipt_hardening.py <receipt>` — exit 0 against the recorded PR head SHA `3ca5bfe87d22b810ad92bd887b77c4f72ba322af`.
- Receipt field check — PASS; source comment `bd79c6bc-8f0c-4fef-b201-160929fd9928`, run `35738454904`, both controls, and checksum `334e08d6b09a0a7bb94e94a0f9f4496cd262a5c806115ad0fa15b7edf2485d33` match.
- `git diff --check` — PASS.

## Local gates
No gate was bypassed. No `--no-verify`, `SKIP=`, `--no-gpg-sign`, or `core.hooksPath` override was used. The required pre-commit and pre-push hooks passed. Any hook failure will be fixed at the input, not worked around.

## Not in this change
This evidence records the existing readback only. It does not clear M3 C19 or claim [OMN-17374](https://linear.app/omninode/issue/OMN-17374) is fully complete. No Linear or Slack writes were made.
