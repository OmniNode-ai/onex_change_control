# CLAUDE.md

Guidance for Claude Code in **onex_change_control** (OCC) — the governance, drift-detection,
and enforcement hub for the ONEX platform, and the canonical evidence surface every other
repo's receipt gate reads. Workspace-wide rules (worktrees, PR CI gates, merge policy,
prod-promotion grants) live in the root `omni_home/CLAUDE.md`; shared Python/Git/testing
standards in `~/.claude/CLAUDE.md`. Neither is repeated here.

## What lives here

- **Schemas**: `ModelDayClose` (OCC-local) plus the OCC DoD types (`ModelDodCheck`,
  `ModelDodEvidenceItem`, `ModelEmergencyBypass`, `ModelEvidenceRequirement`).
  `ModelTicketContract` is **not defined here** — it lives in
  `omnibase_core.models.ticket.model_ticket_contract` and is re-exported by
  `models/model_ticket_contract.py` so all consumers share one class identity (OMN-10066).
- **CLI validators** downstream repos run in CI — `validate-yaml`, `check-schema-purity`,
  `check-drift`, `check-db-boundary`, `check-hardcoded-topics`, `dev-main-cutover`, etc.
  The authoritative list is `[project.scripts]` in `pyproject.toml` — read it; do not trust
  a hand count in prose.
- **ONEX nodes**: the 4 contract-drift archetypes (compute / reducer / orchestrator /
  effect) under `src/onex_change_control/nodes/`, registered in
  `[project.entry-points."onex.nodes"]`.
- **Overseer models**: `src/onex_change_control/overseer/` is **flat** (`model_*.py` +
  `enum_*.py` side by side, no `models/` / `enums/` subdirs) — unlike the rest of the
  package.
- **Promotion tooling** (`promotion/`), scanners (`scanners/`), boundary rules
  (`boundaries/*.yaml`), wire schemas (`wire_schemas/`), eval framework (`eval/` +
  `eval_suites/`), YAML templates (`templates/*.template.yaml`).
- **Prod-promotion grants**: `grants/prod_promotion_grants.yaml` is the `@main`-fetched
  trust anchor for prod promotion (root CLAUDE.md §2a/§12); `validate-prod-promotion-grants`
  checks it.

## The receipt surface (why this repo is load-bearing)

Canonical DoD receipt location — the only shape the gates accept (see
`docs/RECEIPT_LOCATIONS.md`):

```text
drift/dod_receipts/<TICKET>/<ITEM_ID>/<run_timestamp>.yaml   # omnibase_core.ModelDodReceipt
```

- Downstream repos' `occ-preflight / eligibility` and `Receipt Gate / verify` checks fetch
  THIS repo to resolve `contracts/<TICKET>.yaml` + PASS receipts; OCC fetch failure is a
  hard FAIL.
- **In-repo trap**: OCC's own PRs validate contracts/receipts from the PR's in-tree
  checkout (invariant I3, see `call-occ-preflight.yml` / `call-receipt-gate.yml` comments)
  — an OCC PR carries its own contract + receipts.
- Hash binding: `contract_sha256` = sha256 of the contract file's raw bytes;
  `contract_entry_sha256` = canonical-JSON per-entry hash (OMN-13888). Both are computed by
  `omnibase_core.validation.validator_receipt_gate`. The yamlfmt pre-commit hook reflows
  YAML on first commit — commit, let bytes stabilize, then compute `contract_sha256`.

## No deploy-gate.yml — by design

This repo has no runtime contracts, so it has no `deploy-gate.yml`. Do NOT add one or wire
deploy-gate as a required context here: a required check that never reports wedges every
merge on the branch indefinitely.

## Schema purity (D-008)

`models/` and `enums/` modules must be pure — no env reads, no filesystem access, no
network, no time calls. `check-schema-purity` enforces this (exit 1 on violation;
`--warn-only` for gradual adoption).

## Commands

```bash
uv sync --all-groups
uv run pytest
uv run mypy src/ --strict
uv run ruff check src/ tests/ && uv run ruff format src/ tests/
uv run validate-yaml contracts/OMN-123.yaml
uv run check-schema-purity
pre-commit run --all-files
```

Naming follows `omnibase_core` conventions (`Model<Name>` in `model_<name>.py`,
`Enum<Name>` in `enum_<name>.py`). Package and schema version are 1:1 — current version in
`pyproject.toml`, break rules in `docs/VERSIONING_POLICY.md`.

SPDX MIT headers are required in `src/`, `tests/`, `scripts/` (there is no `examples/`
dir). Stamp: `uv run onex spdx fix src tests scripts`; spec:
`omnibase_core/docs/conventions/FILE_HEADERS.md`.

## Key docs

- `docs/INDEX.md` — doc map
- `docs/design/DESIGN_DRIFT_CONTROL_SYSTEM.md`, `docs/design/DECISION_LOG.md`
- `docs/RECEIPT_LOCATIONS.md`, `docs/VERSIONING_POLICY.md`, `docs/TEMPLATE_GUIDE.md`
