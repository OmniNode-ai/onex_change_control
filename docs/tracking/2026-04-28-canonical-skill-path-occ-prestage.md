# Canonical Skill Path OCC Pre-Stage Matrix

Date: 2026-04-28
Parent: `OMN-10235`
Primary OCC staging ticket: `OMN-10236`
Plan source of truth: `/Users/jonah/Code/omni_home/docs/plans/2026-04-28-canonical-skill-path-consolidation.md`

## Purpose

This document pre-stages the central OCC contract and receipt layout for the
canonical runtime-backed skill path consolidation before downstream repo PRs
are opened.

The consolidation spans multiple repos and touches real execution boundaries:

- `omnibase_infra`: local ingress becomes a thin broker passthrough
- `omnibase_core` + `omnibase_infra`: shared runtime-skill client split across
  package boundaries
- `omnimarket` + `omniclaude`: runtime-backed skill wrappers migrate off
  repo-local or legacy execution paths
- `omnibase_infra` + `omnimarket` + `omniclaude`: validators and manifest
  guard against new non-canonical skill paths

One shared epic ticket is not sufficient for Receipt Gate. Downstream PRs will
reference child tickets, so each child ticket that may be used as a PR driver
needs its own central OCC contract in `onex_change_control/contracts/`.

## Decision

Child-specific contracts are required for:

- `OMN-10236`
- `OMN-10237`
- `OMN-10238`
- `OMN-10239`

The parent `OMN-10235` remains the planning and coordination epic in Linear,
but repo work should bind to the child tickets above so Receipt Gate has a
concrete contract and receipt path.

## Contract Matrix

| Ticket | Repo surface | Contract path | Seam | Interfaces | Expected central receipt roots |
|---|---|---|---|---|---|
| `OMN-10236` | `onex_change_control` | `contracts/OMN-10236.yaml` | yes | none | none; this is the pre-stage contract itself |
| `OMN-10237` | `omnibase_infra` | `contracts/OMN-10237.yaml` | yes | `protocols`, `public_api` | `drift/dod_receipts/OMN-10237/dod-001/` through `dod-004/` |
| `OMN-10238` | `omnibase_core`, `omnibase_infra`, `omnimarket`, `omniclaude` | `contracts/OMN-10238.yaml` | yes | `protocols`, `public_api` | `drift/dod_receipts/OMN-10238/dod-001/` through `dod-003/` |
| `OMN-10239` | `omnibase_infra`, `omnimarket`, `omniclaude` | `contracts/OMN-10239.yaml` | yes | none | `drift/dod_receipts/OMN-10239/dod-001/` through `dod-003/` |

## Receipt Policy

Canonical receipts live under:

```text
drift/dod_receipts/<TICKET>/<DOD-ID>/<run_timestamp>.yaml
```

Do not commit fake or placeholder YAML receipts just to satisfy a path check.
Receipt directories should only receive machine-generated or probe-generated
artifacts once the downstream proof has actually run.

Where the central contract needs to point at future proof, use a command check
that asserts at least one canonical receipt exists under the expected
directory. This keeps the contract aligned with the canonical per-run receipt
location without requiring a guessed timestamp.

## Repo Ticket Guidance

`OMN-10237`
- Use for the `omnibase_infra` local-ingress collapse into broker passthrough.
- Expected proof: local ingress forwards broker-native requests, direct
  handler-dispatch ownership is removed for skill-originated work, and
  `node_alias` compatibility remains only as deprecated translation with
  telemetry.

`OMN-10238`
- Use for the shared runtime-skill client ownership split across core and
  infra, plus downstream wrapper adoption.
- Expected proof: transport-agnostic models and protocols live in
  `omnibase_core`, concrete host-local transport lives in `omnibase_infra`,
  and repo wrappers are limited to payload mapping rather than transport
  ownership.

`OMN-10239`
- Use for the advisory validator and migration-manifest guardrails.
- Expected proof: runtime-backed skills are classified, a migration manifest
  exists, forbidden path scans run in advisory mode, and the blocking flip
  criteria are explicit.

## Merge Discipline

Before opening or merging downstream repo PRs:

1. The corresponding contract file in `onex_change_control/contracts/` must
   exist.
2. The downstream PR should reference the child ticket, not just `OMN-10235`.
3. Once proof exists, write canonical receipts to the ticket's expected
   `drift/dod_receipts/...` directories and rerun the relevant gate.

This is the substrate that prevents a late Receipt Gate scramble.
