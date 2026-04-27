# Codex Runtime OCC Pre-Stage Matrix

Date: 2026-04-27
Parent: `OMN-10079`
Primary OCC staging ticket: `OMN-10080`
Plan source of truth: `/Users/jonah/Code/omni_home/docs/plans/2026-04-27-emit-daemon-omnimarket-cutover-and-runtime-standardization.md`

## Purpose

This document pre-stages the central OCC contract and receipt layout for the
Codex runtime local-ingress cutover before downstream repo PRs are opened.

The cutover spans multiple repos and touches a real execution boundary:

- `omnibase_infra`: runtime-owned local ingress and health wiring
- `omnimarket`: market-skill client path cutover
- `omniclaude`: launcher standardization and emit-daemon cleanup

One shared parent contract is not sufficient for Receipt Gate. Downstream PRs
will reference child tickets, so each child ticket that may be used as a PR
driver needs its own central OCC contract in `onex_change_control/contracts/`.

## Decision

Child-specific contracts are required for:

- `OMN-10080`
- `OMN-10081`
- `OMN-10082`
- `OMN-10083`
- `OMN-10084`
- `OMN-10085`

The parent `OMN-10079` remains the planning/coordination ticket in Linear, but
repo work should bind to the child tickets above so Receipt Gate has a concrete
contract and receipt path.

## Contract Matrix

| Ticket | Repo surface | Contract path | Seam | Interfaces | Expected central receipt roots |
|---|---|---|---|---|---|
| `OMN-10080` | `onex_change_control` | `contracts/OMN-10080.yaml` | yes | none | none; this is the pre-stage contract itself |
| `OMN-10081` | `omnibase_infra` | `contracts/OMN-10081.yaml` | yes | `protocols`, `envelopes`, `public_api` | `drift/dod_receipts/OMN-10081/dod-001/` through `dod-003/` |
| `OMN-10082` | `omnibase_infra` | `contracts/OMN-10082.yaml` | yes | `protocols`, `envelopes`, `public_api` | `drift/dod_receipts/OMN-10082/dod-001/` through `dod-003/` |
| `OMN-10083` | `omnimarket` | `contracts/OMN-10083.yaml` | yes | `protocols`, `public_api` | `drift/dod_receipts/OMN-10083/dod-001/` through `dod-003/` |
| `OMN-10084` | `omniclaude`, `omnimarket` | `contracts/OMN-10084.yaml` | no | none | `drift/dod_receipts/OMN-10084/dod-001/` through `dod-003/` |
| `OMN-10085` | `omnibase_infra`, `omnimarket` | `contracts/OMN-10085.yaml` | yes | `protocols`, `envelopes`, `public_api` | `drift/dod_receipts/OMN-10085/dod-001/` through `dod-005/` |

## Receipt Policy

Canonical receipts live under:

```text
drift/dod_receipts/<TICKET>/<DOD-ID>/<run_timestamp>.yaml
```

Do not commit fake or placeholder YAML receipts just to satisfy a path check.
Receipt directories should only receive machine-generated or probe-generated
artifacts once the downstream proof has actually run.

Where the central contract needs to point at future proof, use a command check
that asserts at least one canonical receipt exists under the expected directory.
This keeps the contract aligned with the canonical per-run receipt location
without requiring a guessed timestamp.

## Repo Ticket Guidance

`OMN-10081`
- Use for the runtime transport/lifecycle change in `omnibase_infra`.
- Expected proof: socket ingress lifecycle, dispatch routing, socket cleanup.

`OMN-10082`
- Use for typed protocol models and ingress-specific health/observability.
- Expected proof: request validation, timeout/error handling, health exposure.

`OMN-10083`
- Use for the omnimarket client/shim cutover.
- Expected proof: supported client path replaces direct Kafka/direct CLI as the
  default skill execution path.

`OMN-10084`
- Use for launcher standardization and emit-daemon cleanup.
- Expected proof: one-Python pinning, `env -u PYTHONPATH`, legacy publisher
  retirement, watchdog/logging behavior preserved.

`OMN-10085`
- Use for the end-to-end proof gate after the implementation tickets land.
- Expected proof: each current market skill round-trips through the local
  runtime ingress, and direct CLI fallback is no longer the supported path.

## Merge Discipline

Before opening or merging downstream repo PRs:

1. The corresponding contract file in `onex_change_control/contracts/` must
   exist.
2. The downstream PR should reference the child ticket, not just `OMN-10079`.
3. Once proof exists, write canonical receipts to the ticket's expected
   `drift/dod_receipts/...` directories and rerun the relevant gate.

This is the substrate that prevents a late Receipt Gate scramble.
