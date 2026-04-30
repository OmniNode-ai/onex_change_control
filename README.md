# onex_change_control

Governance, drift detection, and enforcement library for the ONEX (OmniNode eXecution) ecosystem.

[![CI](https://github.com/OmniNode-ai/onex_change_control/actions/workflows/ci.yml/badge.svg)](https://github.com/OmniNode-ai/onex_change_control/actions/workflows/ci.yml)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

---

## What This Repo Is

`onex_change_control` (package: `onex-change-control`) is the **canonical governance and enforcement hub** for the ONEX platform. It prevents cross-repo drift by:

- Defining versioned Pydantic schemas for governance artifacts (`ModelTicketContract`, `ModelDayClose`).
- Shipping CLI validators that downstream repos run in CI to prove contract compliance.
- Enforcing architectural invariants (schema purity, naming conventions, DB-boundary, hardcoded-topic detection) via pre-commit hooks and CI gates.
- Owning the evaluation framework (A/B eval suites and comparators) for quantitative ONEX value measurement.

---

## Who Uses This Repo

| Consumer | Usage |
|----------|-------|
| Every downstream repo | Runs `validate-yaml contracts/<TICKET>.yaml` in CI |
| OmniClaude | Imports models to verify and emit DoD receipts |
| OmniBase Infra | Runs `check-drift`, `check-schema-purity`, and `scan-contract-dependencies` in CI |
| OmniDash | Consumes eval-completed events from the comparator |
| Developers | Authors ticket contracts and day-close reports from templates |

---

## What This Repo Owns

- **Canonical Pydantic schemas**: `ModelTicketContract`, `ModelDayClose`, and all supporting models/enums.
- **Exported JSON schemas**: `schemas/<version>/` — immutable versioned build artifacts.
- **YAML templates**: `templates/ticket_contract.template.yaml`, `templates/day_close.template.yaml`.
- **CLI enforcement tooling**: validators, purity checkers, drift checkers, boundary checkers.
- **Evaluation framework**: eval suite definitions, comparator logic, regression checks.
- **Governance policy docs**: design, decision log, versioning policy, template guide.

## What This Repo Does Not Own

- **Business logic nodes**: portable workflow nodes live in `omnimarket`.
- **Kafka infrastructure**: topic registration and bus wiring live in `omnibase_infra`.
- **UI rendering**: governance dashboards are rendered in `omnidash`.
- **Agent invocation UX**: skill definitions and hooks live in `omniclaude`.

---

## Install

```bash
uv add onex-change-control
```

Or pin a specific version range in `pyproject.toml`:

```toml
[project.dependencies]
onex-change-control = ">=0.5.0,<1.0.0"
```

---

## Core Workflows

### Daily Use: Author and Validate a Day-Close Report

```bash
# Copy the template
cp templates/day_close.template.yaml drift/day_close/$(date +%Y-%m-%d).yaml

# Fill it out, then validate
uv run validate-day-close drift/day_close/$(date +%Y-%m-%d).yaml
```

### Daily Use: Author and Validate a Ticket Contract

```bash
# Copy the template
cp templates/ticket_contract.template.yaml contracts/OMN-XXXX.yaml

# Fill in ticket_id, summary, interface_change, evidence_requirements

# Validate against pinned schema
uv run validate-yaml contracts/OMN-XXXX.yaml
```

### Incident/Drift Response: Run a Drift Check

```bash
# Detect drift across configured repos
uv run check-drift

# Check for DB boundary violations
uv run check-db-boundary

# Check for hardcoded Kafka topics
uv run check-hardcoded-topics

# Scan contract dependencies for violations
uv run scan-contract-dependencies
```

### Readiness Evaluation: Check Schema Purity

```bash
# Enforce D-008: schema modules must have no I/O, env, or time calls
uv run check-schema-purity

# Warn-only mode for gradual CI adoption
uv run check-schema-purity --warn-only
```

### Policy Authoring: Extend Enforcement Rules

1. Add a new check script under `src/onex_change_control/scripts/`.
2. Register it in `pyproject.toml` `[project.scripts]`.
3. Wire it in `.github/workflows/ci.yml` as a CI gate.
4. Add a pre-commit hook entry if it should run locally on commit.

See [docs/README.md](docs/README.md) for the full governance workflow index.

### Generated Ticket Review: Validate Existing Contracts

```bash
# Validate all contracts in the contracts/ directory
uv run validate-yaml contracts/*.yaml

# Validate a specific contract
uv run validate-yaml contracts/OMN-9605.yaml

# Validate agent YAML artifacts
uv run validate-agent-yaml <path-to-agent.yaml>
```

---

## Architecture Summary

```text
onex_change_control/
├── src/onex_change_control/
│   ├── enums/            # Enum* types (EnumDriftCategory, EnumEvidenceKind, ...)
│   ├── models/           # Model* Pydantic schemas (ModelDayClose, ModelTicketContract, ...)
│   ├── nodes/            # ONEX node implementations (drift compute/reducer/effect/orchestrator)
│   ├── scripts/          # CLI entry points (validate_yaml, check_schema_purity, ...)
│   ├── eval/             # Evaluation framework (suite manager, comparator)
│   ├── cosmetic/         # Cosmetic lint tooling
│   └── validation/       # Shared validation helpers (patterns, SEMVER_PATTERN)
├── schemas/              # Exported JSON schemas (immutable per version)
├── templates/            # YAML template files for artifact authoring
├── contracts/            # Per-ticket contract YAML files
├── drift/                # Day-close reports and DoD receipts
│   ├── day_close/        # Historical day_close YAML artifacts
│   └── dod_receipts/     # Per-ticket DoD receipts (canonical receipt location)
├── eval_suites/          # Eval suite definitions (standard_v1.yaml)
├── docs/                 # Governance design, policy, and reference docs
└── tests/                # pytest test suite
```

**Key design principle**: Schema modules are **pure** (no I/O, no env reads, no time calls). `check-schema-purity` enforces this in CI.

See [docs/design/DESIGN_DRIFT_CONTROL_SYSTEM.md](docs/design/DESIGN_DRIFT_CONTROL_SYSTEM.md) for the full enforcement model.

---

## Development and Test Commands

```bash
# Install all dependencies
uv sync --all-groups

# Run full test suite (no -k filter required)
uv run pytest tests/ -v

# Type check (strict)
uv run mypy src/ --strict

# Lint and format
uv run ruff format src/ tests/
uv run ruff check --fix src/ tests/

# Pre-commit hooks (run before every commit)
pre-commit install
pre-commit run --all-files

# Check schema purity
uv run check-schema-purity

# Check DB boundary
uv run check-db-boundary

# Check hardcoded topics
uv run check-hardcoded-topics

# Stamp or check SPDX headers
uv run onex spdx fix src tests scripts examples
uv run onex spdx fix --check src tests scripts examples
```

---

## Documentation Map

| Document | Purpose |
|----------|---------|
| [docs/README.md](docs/README.md) | Full docs index — start here |
| [docs/design/DESIGN_DRIFT_CONTROL_SYSTEM.md](docs/design/DESIGN_DRIFT_CONTROL_SYSTEM.md) | Enforcement model, phases, invariants |
| [docs/design/DECISION_LOG.md](docs/design/DECISION_LOG.md) | Architectural decisions and rationale (D-001 through D-008+) |
| [docs/VERSIONING_POLICY.md](docs/VERSIONING_POLICY.md) | Schema immutability and SemVer rules |
| [docs/TEMPLATE_GUIDE.md](docs/TEMPLATE_GUIDE.md) | How to author YAML artifacts from templates |
| [docs/EVAL_FRAMEWORK.md](docs/EVAL_FRAMEWORK.md) | A/B evaluation framework architecture |
| [docs/RECEIPT_LOCATIONS.md](docs/RECEIPT_LOCATIONS.md) | DoD receipt location (canonical: `drift/dod_receipts/`) |
| [CLAUDE.md](CLAUDE.md) | Developer context and conventions |
| [AGENT.md](AGENT.md) | LLM navigation guide |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Contribution guide |
| [SECURITY.md](SECURITY.md) | Security policy |

---

## Security

See [SECURITY.md](SECURITY.md) for how to report vulnerabilities.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for contribution guidelines.

## License

[MIT](LICENSE)
