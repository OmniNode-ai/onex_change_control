# CLAUDE.md

> **Shared standards** (Python, Git, Testing) are in `~/.claude/CLAUDE.md`.

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

<!-- Verified against code on 2026-06-21 refresh (OMN-13459): src/onex_change_control/ layout, models/ (30 model_*.py) + enums/ (25 enum_*.py), flat overseer/ (14 model_*.py + 14 enum_*.py, no models/enums subdirs), nodes/ (4 contract-drift archetypes: compute/reducer/orchestrator/effect), handlers/, eval/, doctrine/loader.py, pyproject [project.scripts] (22) + [project.entry-points."onex.nodes"] (4), templates/ (3), version 0.5.1. -->


## Repository Overview

**onex_change_control** is the canonical governance, drift detection, and enforcement hub for the ONEX platform. It owns versioned Pydantic schemas for ticket contracts and day-close reports, CLI validators that downstream repos run in CI to prove contract compliance, ONEX node implementations (compute/reducer/effect/orchestrator for contract drift), an overseer and orchestration model suite, doc-freshness scanners, promotion tooling, and cosmetic lint tooling. Currently at v0.5.1.

## What This Repo Is

- **Canonical Pydantic schemas** for:
  - `ModelTicketContract` — per-ticket acceptance spec (interfaces touched, evidence requirements, emergency bypass)
  - `ModelDayClose` — daily reconciliation artifact (PRs closed, drift metrics, schema hashes)
- **20+ CLI entry points** including `validate-yaml`, `check-schema-purity`, `check-drift`, `check-db-boundary`, `check-hardcoded-topics`, `generate-promotion-manifest`, `promotion-workflow-evidence`, `dev-main-cutover`, and more (see `pyproject.toml` `[project.scripts]`)
- **ONEX nodes**: compute, reducer, effect, and orchestrator nodes for contract drift detection
- **Overseer module**: 14 models + 14 enums for worker/session/dispatch orchestration
- **Promotion tooling**: manifest generation, workflow evidence, and dev→main cutover automation
- **Scanners**: doc-freshness detection, handler contract compliance, wire schema compliance, and more
- **Validation helpers**: regex patterns, YAML validation logic, schema purity checks
- **Templates**: `templates/ticket_contract.template.yaml`, `templates/day_close.template.yaml`, `templates/overnight_contract.template.yaml`
- **Design docs**: `docs/design/DESIGN_DRIFT_CONTROL_SYSTEM.md`, `docs/design/DECISION_LOG.md`

## Architecture

```text
onex_change_control/
├── src/onex_change_control/
│   ├── boundaries/          # Kafka boundary rules and DB routing rules (kafka_boundaries.yaml, db_routing_rules.yaml)
│   ├── canary/              # Canary schema definitions
│   ├── cosmetic/            # Cosmetic lint tooling (spec.yaml, CLI)
│   ├── dispatch_claims/     # Dispatch claim store and sweeper
│   ├── doctrine/            # Doctrine loader (loader.py) — authoritative policy config
│   ├── enums/               # Enum* types (EnumDriftCategory, EnumEvidenceKind, etc.)
│   ├── eval/                # A/B evaluation framework (suite manager, comparator)
│   ├── handlers/            # Handler implementations (handler_dod_sweep, handler_drift_analysis, handler_dependency_analysis)
│   ├── kafka/               # Governance Kafka topics and event emitter
│   ├── models/              # Model* Pydantic schemas (ModelDayClose, ModelTicketContract, ...)
│   ├── nodes/               # ONEX node implementations (contract drift compute/reducer/effect/orchestrator)
│   ├── overseer/            # Orchestration models (14 models, 14 enums) for worker/session/dispatch
│   ├── promotion/           # Promotion tooling: manifest.py, workflow.py, cutover.py, staleness.py
│   ├── scanners/            # Doc-freshness, handler compliance, wire-schema compliance scanners
│   ├── scripts/             # CLI entry point implementations (20+ scripts)
│   ├── testing/             # Wire schema test generator
│   ├── validation/          # Shared validation helpers (patterns.py, SEMVER_PATTERN)
│   ├── validators/          # Architectural validators (handler contract compliance, cross-schema coherence)
│   └── wire_schemas/        # Wire schema YAML definitions (occ_nightly_promotion_v1.yaml, etc.)
├── schemas/                 # Exported JSON schemas (immutable per version)
├── templates/               # YAML template files for artifact authoring
├── contracts/               # Per-ticket contract YAML files
├── drift/                   # Day-close reports and DoD receipts
│   ├── day_close/           # Historical day_close YAML artifacts
│   └── dod_receipts/        # Per-ticket DoD receipts (canonical receipt location)
├── allowlists/              # Per-repo compliance allowlist YAML files
├── eval_suites/             # Eval suite definitions (standard_v1.yaml)
├── docs/                    # Governance design, policy, and reference docs
└── tests/                   # pytest test suite
```

**Key design principle**: Schema modules are **pure** (no I/O, no env reads, no time calls). `check-schema-purity` enforces this in CI.

## Naming Conventions

All schema modules follow `omnibase_core` conventions:

| Type | Convention | Example |
|------|------------|---------|
| Model classes | `Model<Name>` | `ModelDayClose`, `ModelTicketContract` |
| Model files | `model_<name>.py` | `model_day_close.py`, `model_ticket_contract.py` |
| Enum classes | `Enum<Name>` | `EnumDriftCategory`, `EnumInterfaceSurface` |
| Enum files | `enum_<name>.py` | `enum_drift_category.py`, `enum_interface_surface.py` |

## Development Commands

```bash
# Install dependencies
uv sync --all-groups

# Run all tests
uv run pytest

# Run single test file
uv run pytest tests/path/to/test_file.py -v

# Type checking
uv run mypy src/ --strict

# Lint and format
uv run ruff check src/ tests/
uv run ruff format src/ tests/

# Validate a YAML artifact
uv run validate-yaml contracts/OMN-123.yaml

# Validate multiple YAML files
uv run validate-yaml drift/day_close/*.yaml

# Check schema purity (enforces D-008)
uv run check-schema-purity

# Warn-only mode (for gradual CI adoption)
uv run check-schema-purity --warn-only

# Drift and boundary checks
uv run check-drift
uv run check-db-boundary
uv run check-hardcoded-topics
uv run scan-contract-dependencies

# Promotion tooling
uv run generate-promotion-manifest
uv run promotion-workflow-evidence
uv run dev-main-cutover

# Pre-commit hooks
pre-commit install
pre-commit run --all-files
```

## Schema Purity Rule (D-008)

Schema modules (`models/`, `enums/`) must be free of:
- Environment reads (`os.environ`, `os.getenv`)
- Filesystem access (`open`, `pathlib.Path.read_*`)
- Network calls (`httpx`, `requests`, `urllib`)
- Time reads (`datetime.now()`, `time.time()`)

`check-schema-purity` enforces this and exits with code 1 on violations.

## Versioning Policy

Package version and schema version are **1:1 mapped**:
- `0.1.x` — pre-release, schemas may evolve
- `1.0.0` — first stable release, schemas are immutable under SemVer

See `docs/VERSIONING_POLICY.md` for breaking vs non-breaking change rules.

## Downstream Usage

Other repos consume this package for:
1. **Python validation** — import `ModelTicketContract` / `ModelDayClose` and call `.model_validate()`
2. **CLI validation** — run `validate-yaml <file>` in CI
3. **Pre-commit hooks** — validate contract files on commit

See `README.md` for full integration examples.

## SPDX Headers

All source files in `src/`, `tests/`, `scripts/`, `examples/` require MIT SPDX headers.
Canonical spec: `omnibase_core/docs/conventions/FILE_HEADERS.md`

- Stamp missing headers: `uv run onex spdx fix src tests scripts examples`
- Check without writing: `uv run onex spdx fix --check src tests scripts examples`
- Bypass a file: add `# spdx-skip: <reason>` in the first 10 lines

## Key Documentation

- `docs/design/DESIGN_DRIFT_CONTROL_SYSTEM.md` — enforcement model and rollout phases
- `docs/design/DECISION_LOG.md` — architectural decisions and rationale
- `docs/VERSIONING_POLICY.md` — schema immutability rules
- `docs/TEMPLATE_GUIDE.md` — how to author YAML artifacts from templates
