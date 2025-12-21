## Implementation Plan (ONEX Change Control)

Status: Draft (Planning)
Owner: OmniNode / ONEX
Last Updated: 2025-12-19

### 1) Goal

Implement the drift-control system described in `docs/design/DESIGN_DRIFT_CONTROL_SYSTEM.md` with:
- Canonical, versioned schema **models** (Pydantic) living in `onex_change_control`
- Deterministic **JSON Schema exports** for CI consumption (pinned by version/commit)
- Local, no-network **validation tooling**
- CI enforcement of schema **purity** + **naming conventions** + **export determinism**

This plan assumes **Poetry** is used (consistent with other repos).

### 2) Non-negotiable constraints (from design + decision log)

- **Canonical ownership**: schemas originate here (D-001).
- **Schema model location**: Pydantic schema models live here (D-006).
- **Dependency layering compatibility**: must remain compatible with `omnibase_core → omnibase_spi → omnibase_infra` (D-007).
- **Schema purity**: schema modules are protocol-like and must remain pure (D-008).
- **Naming conventions**: align with `omnibase_core/docs/conventions/NAMING_CONVENTIONS.md`:
  - Model classes: `Model<Name>`
  - Model files: `model_<name>.py`
  - Enum classes: `Enum<Name>`
  - Enum files: `enum_<name>.py`
- **Explicit schema surface freeze**: once a schema version is released, field names, enum values, and required/optional status are immutable within that version line.

### 3) Output artifacts (what this repo must produce)

- **Schema models** (source of truth):
  - `ModelDayClose` and related models/enums
  - `ModelTicketContract` and related models/enums
- **Exported JSON schemas** (pinned artifacts):
  - `schemas/<schema_version>/day_close.schema.json`
  - `schemas/<schema_version>/ticket_contract.schema.json`
  - A machine-readable manifest (e.g., `schemas/<schema_version>/manifest.json`) listing schema files, hashes, and export tool version
- **Templates** (human- and AI-friendly):
  - `templates/day_close.template.yaml`
  - `templates/ticket_contract.template.yaml`
- **Validation tooling** (local, deterministic):
  - Validate YAML against pinned JSON schema files
  - (Later phases) diff classifier + evidence enforcement hooks

### 4) Repo structure (target)

```text
onex_change_control/
  pyproject.toml
  src/onex_change_control/
    __init__.py
    models/
      common/
        model_schema_version.py
      day_close/
        model_day_close.py
        enum_*.py
      ticket_contract/
        model_ticket_contract.py
        enum_*.py
  schemas/
    1.0.0/
      day_close.schema.json
      ticket_contract.schema.json
      manifest.json
  templates/
    day_close.template.yaml
    ticket_contract.template.yaml
  scripts/
    export_json_schema.py
    validate_yaml_against_schema.py
    check_purity.py
    check_naming.py
  .github/workflows/
    ci.yml
```

### 5) Packaging plan (Poetry)

Deliverables:
- `pyproject.toml` defining:
  - Package name (e.g., `onex-change-control`)
  - Python version constraint (match org standard)
  - Dependencies: **pydantic** (and only what is necessary for schema modeling/export)
  - Dev dependencies: test runner + lint tooling per org standards
- `src/` layout with a minimal `onex_change_control` package

Purity policy implementation note:
- Schema model modules must not import infra/runtime tooling.
- Tooling/CLI scripts must live outside the schema modules (e.g., `scripts/` or `onex_change_control/tools/`).

### 6) Schema design plan (v1)

#### 6.1 `day_close.yaml` (Daily Close Report)

Planned model surface (names indicative; final names must follow `Model*`):
- `ModelDayClose`
  - `schema_version: str` (SemVer string)
  - `date: str` (ISO date)
  - `plan: list[ModelDayClosePlanItem]`
  - `actual_by_repo: list[ModelDayCloseActualRepo]`
  - `drift_detected: list[ModelDayCloseDriftDetected]`
  - `invariants_checked: ModelDayCloseInvariantsChecked`
  - `corrections_for_tomorrow: list[ModelDayCloseCorrection]`
  - `risks: list[ModelDayCloseRisk]`

Validation rules:
- Exhaustiveness rule from design:
  - Every `actual_by_repo` entry must map to a plan requirement OR be represented as drift.
  - Implementation approach:
    - Prefer **tooling validation** (script-level) if cross-referencing is too complex for JSON Schema alone.
    - Keep schema-level validation for structural constraints (required fields, types, enums, non-empty lists).
- Unknown handling rule:
  - Any invariant marked `unknown` must produce at least one correction entry.
  - Same approach: enforce at tooling-level if needed; schema enforces allowed values.

Explicit non-goal: JSON Schema will not encode cross-collection exhaustiveness or semantic drift logic.
Those rules are treated as executable policy and enforced exclusively in tooling and CI.

#### 6.2 `contracts/<ticket_id>.yaml` (Ticket Contract)

Planned model surface:
- `ModelTicketContract`
  - `schema_version: str`
  - `ticket_id: str`
  - `summary: str`
  - `is_seam_ticket: bool`
  - `interface_change: bool`
  - `interfaces_touched: list[EnumInterfaceSurface]` (must allow explicit “none” representation)
  - `evidence_requirements: list[ModelEvidenceRequirement]`
  - `bypass: ModelEmergencyBypass` (D-004)

Validation rules:
- If `interface_change: false`, then `interfaces_touched` must represent “none”.
- If `is_seam_ticket: true`, contract must exist in the repo (this is enforced downstream in repo CI).

### 7) JSON schema export plan (deterministic artifacts)

Deliverables:
- `scripts/export_json_schema.py`:
  - Exports JSON schema for the top-level models (day close + ticket contract)
  - Writes to `schemas/<schema_version>/...`
  - Produces stable output order (deterministic)
- Determinism gate:
  - CI re-exports schemas and fails if git diff is non-empty.
  - Schema export script version must be embedded into exported schema `$comment` or manifest for traceability

Versioning policy:
- Patch/minor updates write to new `schemas/<new_version>/...` directories.
- Existing exported schema artifacts are treated as immutable once released.

### 8) Tooling plan (local, no-network)

Deliverables:
- `scripts/validate_yaml_against_schema.py`:
  - Loads YAML (contract/day close)
  - Validates against pinned JSON schema file from `schemas/<schema_version>/...`
  - Errors are actionable (path + reason)
- Templates:
  - Provide `templates/*.template.yaml` aligned with schema
  - Include comments for required evidence and “unknown” handling

Non-goals for the first tooling increment:
- No cross-repo network calls
- No Linear lookups
- No auto-migrations (until a major-version bump exists)
- No schema auto-normalization or silent coercion; invalid inputs must fail fast

### 9) CI plan (this repo)

Deliverables in `.github/workflows/ci.yml`:
- **Unit tests** for:
  - Model validation for key invariants (especially cross-field logic that JSON Schema cannot express)
  - Export determinism (at least “no diff” check in CI)
- **Purity check** (D-008):
  - Forbidden usage scan in schema modules (e.g., `os.environ`, `datetime.now`, filesystem reads, networking)
  - Forbidden import boundary scan (no `omnibase_infra*`, and no runtime/tooling deps in schema modules)
  - Fail if schema modules reference environment-dependent defaults (e.g., locale, timezone, system paths)
- **Naming check**:
  - Fail if schema model classes do not start with `Model`
  - Fail if model files do not start with `model_`
  - Fail if enum classes do not start with `Enum`
  - Fail if enum files do not start with `enum_`
- **Schema export determinism**:
  - Run export script and fail on diff

### 10) Downstream consumption plan (other repos)

Decision (Accepted): **Poetry package distribution + SemVer pinning**, with schema artifacts treated as immutable build outputs.
Downstream systems must assume schemas are append-only across versions and never mutated in-place.

Downstream repos (e.g., `omnibase_infra`) will consume drift-control artifacts the same way they consume
other ONEX packages: via Poetry dependencies pinned by SemVer (e.g., `^1.0.0`).

Deliverables in this repo to support this:
- Publish a package (recommended name): `onex-change-control`
- Package MUST include:
  - Exported JSON schemas under `schemas/<schema_version>/...`
  - (Optional but recommended) a small CLI entrypoint for validation (no network)
- Package MUST remain schema-pure in its schema/model modules (D-008); tooling modules may depend on
  additional libraries, but schema modules may not.

Downstream dependency (example):

```toml
[tool.poetry.dependencies]
onex-change-control = "^1.0.0"
```

Pinning policy:
- Downstream repos MUST pin with explicit SemVer ranges (at minimum, a caret range) to avoid surprise breakages.
- Breaking schema changes require a major version bump (see version policy in design docs).

Phase 1 integration (minimum viable):
- In each repo CI:
  - Ensure `contracts/<ticket_id>.yaml` exists for seam tickets
  - Validate contract YAML against pinned `ticket_contract.schema.json`

Phase 2 integration:
- Add diff compliance check:
  - If contract declares no interface change, fail if interface paths changed
  - If interface paths changed, require they be declared in `interfaces_touched`

Phase 3 integration:
- Evidence enforcement hooks:
  - Required tests/commands must run and pass

### 11) Milestones (aligned with design phases)

- **M0 (Bootstrap)**: Poetry package + structure + CI skeleton
  - Test coverage threshold: 10% (bootstrap phase with minimal code)
- **M1 (Schemas v1)**: Pydantic models + exported JSON schemas + templates + manifest
  - Test coverage threshold: 40% (schema validation tests added)
- **M2 (Local validator)**: validate YAML against pinned schemas (no network)
  - Test coverage threshold: 60% (validation logic tests added)
- **M3 (Repo CI hardening)**: purity + naming + determinism checks enforced
  - Test coverage threshold: 80% (full test suite for all functionality)
- **M4 (First downstream pilot)**: 1 repo adopts "contract exists + schema validates"
- **M5 (Enforcement expansion)**: diff compliance + evidence enforcement

**Coverage Tracking**: Coverage thresholds increase with each milestone as functionality is added. Update `pyproject.toml` `--cov-fail-under` value when reaching each milestone.
