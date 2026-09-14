# onex_change_control — Documentation Index

> **Start here.** This index is the canonical docs map for `onex_change_control`.
> All other docs in this directory link back here or are reachable from this page.

---

## Start Here

New to this repo? Read in this order:

1. [Root README](../README.md) — what this repo is, who uses it, core workflows.
2. [CLAUDE.md](../CLAUDE.md) — developer context, naming conventions, development commands.
3. [Design: Drift Control System](design/DESIGN_DRIFT_CONTROL_SYSTEM.md) — the enforcement model.
4. [Template Guide](TEMPLATE_GUIDE.md) — how to author YAML artifacts.

---

## Governance Workflow Index

### Daily Use

| Workflow | Command | Doc |
|----------|---------|-----|
| Author a day-close report | `cp templates/day_close.template.yaml drift/day_close/$(date +%Y-%m-%d).yaml` | [Template Guide](TEMPLATE_GUIDE.md#day-close-template) |
| Validate a day-close report | `uv run validate-day-close drift/day_close/YYYY-MM-DD.yaml` | [Template Guide](TEMPLATE_GUIDE.md#validation) |
| Author a ticket contract | `cp templates/ticket_contract.template.yaml contracts/OMN-XXXX.yaml` | [Template Guide](TEMPLATE_GUIDE.md#ticket-contract-template) |
| Validate a ticket contract | `uv run validate-yaml contracts/OMN-XXXX.yaml` | [Template Guide](TEMPLATE_GUIDE.md#validation) |
| Validate all contracts | `uv run validate-yaml contracts/*.yaml` | — |
| Check schema purity | `uv run check-schema-purity` | [Design: D-008](design/DESIGN_DRIFT_CONTROL_SYSTEM.md) |

### Incident/Drift Response

| Workflow | Command | Doc |
|----------|---------|-----|
| Run general drift check | `uv run check-drift` | [Design](design/DESIGN_DRIFT_CONTROL_SYSTEM.md) |
| Check DB boundary violations | `uv run check-db-boundary` | [DB Boundary Policy](policy/db-boundary-policy.md) |
| Check hardcoded Kafka topics | `uv run check-hardcoded-topics` | — |
| Scan contract dependencies | `uv run scan-contract-dependencies` | — |
| Check for bare feature flags | `uv run check-bare-feature-flags` | — |
| Check migration conflicts | `uv run check-migration-conflicts` | — |
| Check env-var contract | `uv run check-env-var-contract` | — |
| Check ANTHROPIC_API_KEY guard | `uv run check-anthropic-key-guard` | — |
| Check omnidash health | `uv run check-omnidash-health` | — |

### Policy Authoring

To add a new enforcement rule:

1. Add a script under `src/onex_change_control/scripts/`.
2. Register it in `pyproject.toml` `[project.scripts]`.
3. Wire it as a CI gate in `.github/workflows/ci.yml`.
4. Add a pre-commit hook entry if local enforcement is needed.
5. Document it in this index.

See [Design: Drift Control System](design/DESIGN_DRIFT_CONTROL_SYSTEM.md) for enforcement model and staged rollout.

### Generated Ticket Review

When automation creates Linear tickets from drift findings:

1. Review tickets labeled `doc-freshness` or `drift-alert` in the Active Sprint.
2. Validate the referenced contract: `uv run validate-yaml contracts/OMN-XXXX.yaml`.
3. Cross-check against day-close report: `drift/day_close/YYYY-MM-DD.yaml`.
4. Close the ticket with a DoD receipt under `drift/dod_receipts/<TICKET>/`.

---

## Current Architecture

| Doc | Purpose |
|-----|---------|
| [design/DESIGN_DRIFT_CONTROL_SYSTEM.md](design/DESIGN_DRIFT_CONTROL_SYSTEM.md) | Full enforcement model, invariants, staged rollout phases |
| [design/DECISION_LOG.md](design/DECISION_LOG.md) | Architectural decisions D-001 through D-008+ |
| [policy/db-boundary-policy.md](policy/db-boundary-policy.md) | Database boundary enforcement policy |
| [policy/typed-metadata-policy.md](policy/typed-metadata-policy.md) | Typed metadata enforcement policy |
| [governance/2026-04-27-required-gates-rollout.md](governance/2026-04-27-required-gates-rollout.md) | Required gates rollout plan (April 2026) |

---

## Reference

| Doc | Purpose |
|-----|---------|
| [VERSIONING_POLICY.md](VERSIONING_POLICY.md) | Schema SemVer, immutability rules, breaking-change definition |
| [TEMPLATE_GUIDE.md](TEMPLATE_GUIDE.md) | Field-by-field reference for `day_close.template.yaml` and `ticket_contract.template.yaml` |
| [EVAL_FRAMEWORK.md](EVAL_FRAMEWORK.md) | A/B evaluation framework: eval suites, comparator, metrics, verdicts |
| [RECEIPT_LOCATIONS.md](RECEIPT_LOCATIONS.md) | Canonical DoD receipt location (`drift/dod_receipts/`) and migration from legacy path |
| [wire-schema-contract-spec.md](wire-schema-contract-spec.md) | Wire schema contract specification |

---

## Runbooks

| Runbook | Purpose |
|---------|---------|
| [runbooks/session-template.md](runbooks/session-template.md) | Template for daily governance sessions |
| [runbooks/verify-recipes.md](runbooks/verify-recipes.md) | Verification recipes for common checks |
| [runbooks/cron-tick-prompt.md](runbooks/cron-tick-prompt.md) | Cron tick prompt for automated governance |

---

## Governance Policy

| Doc | Purpose |
|-----|---------|
| [runbooks/2026-04-26-session.md](runbooks/2026-04-26-session.md) | April 26 2026 governance session record |

---

## Migrations

No active migrations. Breaking schema changes require a major version bump per [VERSIONING_POLICY.md](VERSIONING_POLICY.md).

---

## Decisions

- [design/DECISION_LOG.md](design/DECISION_LOG.md) — D-001 through D-008+ (canonical ownership, schema location, purity, downstream pinning).

---

## Testing and Validation

```bash
# Full test suite (required — no -k filter)
uv run pytest tests/ -v

# Type check
uv run mypy src/ --strict

# Lint
uv run ruff check src/ tests/

# Pre-commit
pre-commit run --all-files

# Schema purity (D-008 enforcement)
uv run check-schema-purity

# Validate all contracts
uv run validate-yaml contracts/*.yaml
```

CI enforces all of the above on every PR. See `.github/workflows/ci.yml` for the full gate list.

---

## Doc Freshness Sweep (Planned)

> **Status**: Planned, not yet implemented in this repo.

The `doc_freshness_sweep` is a planned governance capability that will:

- Scan all `.md` files across ONEX repos and extract code references (file paths, class names, commands, env vars, URLs).
- Detect stale references (code changed after the doc was last updated).
- Detect broken references (referenced file or function no longer exists).
- Emit `onex.evt.onex-change-control.doc-freshness-swept.v1` Kafka events for dashboard consumption.
- Auto-create Linear tickets for broken or stale docs.

**Ownership**: `onex_change_control` owns the models (`ModelDocFreshnessResult`, `ModelDocFreshnessSweepReport`, `ModelDocReference`) and the scanner/resolver library. `omniclaude` owns the `doc_freshness_sweep` skill definition. `omnidash` owns the dashboard card.

**Design reference**: See `omni_home/docs/plans/archive/2026-Q1/2026-03-27-doc-freshness-sweep.md` — 15-task plan covering scanner, cross-reference checker, staleness detection, skill definition, and Kafka integration.

**Integration with OmniClaude**: The skill calls into `onex_change_control` scanners, then emits results through the ONEX event bus.

**Integration with OmniDash**: The `/status` page will display a "Doc Freshness" card consuming the `onex.evt.onex-change-control.doc-freshness-swept.v1` event.

---

## Historical Context

| Doc | Status |
|-----|--------|
| [tracking/2026-04-26-adversarial-receipt-proof-of-life.md](tracking/2026-04-26-adversarial-receipt-proof-of-life.md) | Evidence artifact |
| [tracking/2026-04-26-contract-centralization-inventory.md](tracking/2026-04-26-contract-centralization-inventory.md) | Contract centralization inventory |
| [tracking/2026-04-27-omn-10041-required-gates-baseline.md](tracking/2026-04-27-omn-10041-required-gates-baseline.md) | Required gates baseline (OMN-10041) |
| [REVIEW_READINESS.md](REVIEW_READINESS.md) | Historical readiness review (pre-0.5.0) |
