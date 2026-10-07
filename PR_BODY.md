## Summary

This adds the append-only OCC contract evidence required for [OMN-20008](https://linear.app/omninode/issue/OMN-20008).

## What changed

- Pins OmniMarket #3361, OmniDash #354, and OmniBase Infra #4547 to their exact implementation heads.
- Binds AC1–AC6 to behavior-running test checks and falsifiable deploy probes.
- Adds deploy probes that read the exact product/infra files through GitHub and fail when the claimed behavior is absent.
- Keeps emergency bypass disabled and preserves the existing acceptance vocabulary.

## How it was verified

- Repository pre-commit validation passed for the contract, including YAML schema, DoD evidence, substance floor, AC binding, receipt hardening, canonical shape, and fail-closed checks.
- The probes are content-bound to the exact product and infrastructure commit SHAs; no receipt or contract self-grep is used as behavior evidence.

## Local gates

- Signed commit and normal repository hooks are required before push.
- No gate was bypassed.

## Not in this change

- No product source, migration, dashboard code, receipt, or merged contract is rewritten.
- No PR is merged and no Linear status is changed.

## Failure paths

- A missing or changed symbol at any pinned head makes its corresponding deploy probe exit non-zero.
- Product and infrastructure CI remain responsible for executing the declared behavior checks in their own repositories.
- The evidence is incomplete if any size, base, or live-verdict check cannot be measured; no unresolved check is treated as green.
- A no-answer or unavailable live check is reported as unverified rather than treated as green.
- n/a: no-answer is reported as unverified rather than treated as green.

## Open defects

none

Overlap-Reviewed: #12744 — its auto-companion already carries generated OMN-20008 contract/receipt entries for OmniMarket #3361; this evidence-only change is the required correction for the missing deploy probes and the two product PRs that the auto-companion does not cover.

Evidence-Ticket: OMN-20008
