# Evidence Directory

This directory holds DoD (Definition of Done) evidence receipts for ONEX tickets.

## Historical Migration

Files in subdirectories predating 2026-04-16 were migrated verbatim from `omni_home/.evidence/` as part of the evidence-rehome initiative (PR #284). They are **historical snapshots** captured at ticket-close time and are not modified by the migration PR. Specifically:

- **`pr_state: "open"` values** in historical receipts reflect the PR state at the moment the DoD was verified, not the current state. Many of these PRs have since merged.
- **Embedded ticket references** (e.g., cross-ticket PR numbers) were accurate at write time and are not retroactively updated.
- **Schema variance across receipts** is expected — the `dod_report.json` schema evolved over 35+ tickets. Older receipts use earlier field sets; newer receipts reflect the current schema. No receipt is malformed; they each conform to the schema version current at their write time.

## In-Session Evidence (2026-04-16+)

Files authored on or after 2026-04-16 reflect in-session DoD verifications. CI status snapshots in these receipts reflect the **branch context at write time**, not main's current state. For example, a receipt capturing `1869 passed` against a branch reflects that branch's test run — not a claim about `main` or any other PR's CI state.

## Dependency-Gated Receipts

Some receipts document work whose downstream CI gates depend on upstream PRs merging first. If a receipt references a PR whose tests currently fail, check whether that PR has an unmerged upstream dependency. The receipt's claim should be evaluated in the context of the dependency chain documented in the ticket, not against the isolated current CI state.
