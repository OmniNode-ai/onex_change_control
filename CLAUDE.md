# CLAUDE.md

> **Shared standards** (Python, Git, Testing) are in `~/.claude/CLAUDE.md`.

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository Overview

**onex_change_control** is the centralized governance, schema distribution, and enforcement library for the ONEX platform. It prevents cross-repo drift by defining canonical Pydantic schemas for two artifact types — **ticket contracts** and **day close reports** — along with CLI tooling to validate those artifacts in any downstream repo.

## What This Repo Is

- **Canonical Pydantic schemas** for:
  - `ModelTicketContract` — per-ticket acceptance spec (interfaces touched, evidence requirements, emergency bypass)
  - `ModelDayClose` — daily reconciliation artifact (PRs closed, drift metrics, schema hashes)
- **CLI entry points**:
  - `validate-yaml` — validates one or more YAML files against the appropriate schema
  - `check-schema-purity` — enforces D-008 purity rules on schema modules (no env/fs/network/time usage)
- **Validation helpers**: regex patterns, YAML validation logic, schema purity checks
- **Templates**: `templates/ticket_contract.template.yaml`, `templates/day_close.template.yaml`
- **Design docs**: `docs/design/DESIGN_DRIFT_CONTROL_SYSTEM.md`, `docs/design/DECISION_LOG.md`

## Architecture

```text
onex_change_control/
├── src/onex_change_control/
│   ├── enums/               # Enum* types (EnumDriftCategory, EnumEvidenceKind, etc.)
│   ├── models/              # Model* Pydantic schemas (ModelDayClose, ModelTicketContract)
│   ├── scripts/             # CLI entry points (validate_yaml.py, check_schema_purity.py)
│   └── validation/          # Shared validation helpers (patterns.py, SEMVER_PATTERN)
├── templates/               # YAML template files for artifact authoring
├── drift/day_close/         # Example / historical day_close artifacts
├── docs/                    # Design docs, decision log, versioning policy
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
# Install dependencies (this repo uses Poetry, not uv)
poetry install

# Run all tests
poetry run pytest

# Run single test file
poetry run pytest tests/path/to/test_file.py -v

# Type checking
poetry run mypy src/ --strict

# Lint and format
poetry run ruff check src/ tests/
poetry run ruff format src/ tests/

# Validate a YAML artifact
poetry run validate-yaml contracts/OMN-123.yaml

# Validate multiple YAML files
poetry run validate-yaml drift/day_close/*.yaml

# Check schema purity (enforces D-008)
poetry run check-schema-purity

# Warn-only mode (for gradual CI adoption)
poetry run check-schema-purity --warn-only

# Pre-commit hooks
pre-commit run --all-files
```

> **Note**: This repo uses **Poetry** for dependency management, not `uv`. Use `poetry run` instead of `uv run`.

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

## Key Documentation

- `docs/design/DESIGN_DRIFT_CONTROL_SYSTEM.md` — enforcement model and rollout phases
- `docs/design/DECISION_LOG.md` — architectural decisions and rationale
- `docs/planning/IMPLEMENTATION_PLAN.md` — roadmap
- `docs/VERSIONING_POLICY.md` — schema immutability rules
- `docs/TEMPLATE_GUIDE.md` — how to author YAML artifacts from templates
