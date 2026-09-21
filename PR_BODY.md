## Summary

This change-control PR adds the executable OCC evidence contract requested by
[OMN-18082](https://linear.app/omninode/issue/OMN-18082). The product fix is
already merged in [omnibase_core#1681](https://github.com/OmniNode-ai/omnibase_core/pull/1681).

## What changed

- Added [`contracts/OMN-18082.yaml`](https://github.com/OmniNode-ai/onex_change_control/blob/dev/contracts/OMN-18082.yaml).
- Bound AC1 to a source check proving the remediation does not rewrite existing merged receipts.
- Bound AC2 to the structural-self-bind remediation and rejection-path tests.
- Bound AC3 to the real append-only receipt-gate test.

## How it was verified

- The complete changed validator test file passed 44/44 in three runs.
- The structural-self-bind selection passed 15/15 in three runs.
- The append-only receipt-gate test passed 1/1 in three runs.
- The merged [omnibase_core#1681](https://github.com/OmniNode-ai/omnibase_core/pull/1681) has green required CI, the 40-way test matrix, integration tests, CodeQL, contract, and OCC gates.

## Local gates

No gate was bypassed. Pre-commit passed the contract validation, substance, AC-binding, receipt-hardening, yamlfmt, and no-AI-attribution gates. CI/lab execution will supply PASS receipts; no receipt is fabricated here.

Evidence-Source: OCC#<this-pr>
Evidence-Ticket: [OMN-18082](https://linear.app/omninode/issue/OMN-18082)

## Not in this change

- No product-repository code, ticket status, merge, or deployment is changed here.
- No manual-close decision is being substituted for the executable OCC checks.
