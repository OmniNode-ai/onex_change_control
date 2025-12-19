## DESIGN: Daily Drift Control + Contract-Gated PRs

Status: Draft (Design)

### 1) Purpose

Prevent cross-repo component drift by making “intent vs reality” reconciliation and merge-time enforcement **deterministic and machine-checkable**.

This design is explicitly staged:
- Manual artifacts first (fast adoption)
- CI gates next (enforcement)
- Tooling/automation later (reduce human load)
- ONEX-aligned node automation last (event/ledger friendly)

### 2) Scope

In scope:
- Daily Close Report artifact (`day_close.yaml`)
- Ticket Contract artifact (`contracts/<ticket_id>.yaml`)
- Versioned schemas for both artifacts
- CI merge gates that enforce:
  - contract existence
  - contract schema validity
  - minimal diff compliance rules (interface drift detection)
  - evidence requirements

Out of scope (for this doc):
- Implementing the full CLI and CI adapters (will be in an implementation plan)
- Choosing a single canonical runtime/envelope architecture for ONEX workflows
- Rewriting existing repos to conform (this system enforces what is declared)

Layering constraint (assumed by this system):
- Dependency order is **`omnibase_core` → `omnibase_spi` → `omnibase_infra`**.
- See: `docs/design/DECISION_LOG.md` (D-007)

### 3) Non-negotiable invariants (policy level)

This repo maintains the “policy contract” of invariants. Examples (not exhaustive):
- Reducers fold EVENTS only; no commands.
- Reducers never read clocks; orchestrators own time.
- Orchestrators never perform I/O.
- Effects execute I/O; must be idempotent under at-least-once delivery.
- No implicit interface drift (event shapes, topic maps, schema contracts).

### 4) Canonical artifacts

#### 4.1 Daily Close Report

Path (recommended): `drift/day_close/YYYY-MM-DD.yaml`

Purpose:
- Daily reconciliation of plan vs diffs across repos.

Minimum required fields (conceptual):
- `date`
- `plan[]` (requirement_id, summary)
- `actual_by_repo[]` (repo, prs, commit ranges)
- `drift_detected[]` (type, evidence, correction)
- `invariants_checked` (pass/fail/unknown matrix)
- `corrections_for_tomorrow[]`
- `risks[]`

Exhaustiveness rule:
    - Every entry in actual_by_repo MUST map to either:
        - a plan[] requirement_id, or
        - a drift_detected[] entry.
    - No work may exist outside these two buckets.
    - Unmapped work is invalid and MUST be recorded as drift_detected.

Unknown handling rule:
    - Any invariant marked as unknown MUST produce at least one entry in corrections_for_tomorrow[].
    - "Unknown" is a temporary state, not an acceptable steady state.

#### 4.2 Ticket Contract

Path: `contracts/<ticket_id>.yaml`

Purpose:
- Machine-checkable acceptance criteria and enforcement hooks for a single ticket.

Non-negotiable properties:
- Schema-validated
- Predominantly structured fields (enums, booleans, lists)
- Explicit evidence requirements
- Explicit interface surfaces touched (or explicit “none”)

### 5) Contract schema versioning

Decision: schemas MUST be versioned and evolvable.

Recommended pattern:
- Schema model version is embedded in each artifact:
  - `schema_version: "1.0.0"`
- Source of truth:
  - **Pydantic schema models live in `onex_change_control`** (schema ownership follows enforcement authority).
  - **Model classes MUST use the `Model` prefix** (e.g., `ModelDayClose`, `ModelTicketContract`) per `omnibase_core` naming conventions.
  - **Model files MUST follow `model_<name>.py` convention** (e.g., `model_day_close.py`, `model_ticket_contract.py`).
- This repo provides:
  - Governance docs + decision log for schema evolution rules
  - A distribution path for **JSON Schema exports** suitable for CI validation (pinned by version)

Compatibility policy:
- Minor versions are backward compatible (additive)
- Major versions are breaking and require migration tooling

Downgrade and deprecation policy:
    - Deprecated fields emit warnings but continue to validate.
    - Removed fields cause schema validation failure.
    - Major version upgrades REQUIRE migration tooling or documented manual migration steps.

### 6) Enforcement model (CI merge gates)

CI in each target repo should run:

1) **Contract existence**
   - Every PR must reference a ticket ID (convention decided per org)
   - If the ticket is a “seam ticket” (see below), the contract must exist

2) **Schema validation**
   - Fail if contract YAML does not validate against schema

3) **Diff compliance (minimum viable)**
   - Fail if contract says `interface_change: false` but interface surfaces changed
   - Fail if interface surfaces changed but not declared in `interfaces_touched`

False-negative policy:
    - Diff classification is allowed to produce false negatives.
    - Declared contract constraints MUST NEVER be silently violated.
    - If a contract declares a constraint and the diff violates it, CI MUST fail regardless of classifier completeness.

4) **Evidence enforcement**
   - Fail if required tests/validators are not executed or fail

#### 6.1 “Seam ticket” definition

We need a deterministic gate to start small.

Options:
- Label-driven (Linear label: `seam`)
- Contract-driven (contract field: `is_seam_ticket: true`)
- Hybrid (label implies default; contract can override with explicit justification)

### 7) Diff classification: what is an "interface surface"?

The enforcement system needs a canonical classifier, even if imperfect.

Interface surface categories (initial):
- `events` (event schemas/models)
- `topics` (topic map, routing keys, partition keys)
- `protocols` (SPI protocol interfaces, public runtime interfaces)
- `envelopes` (envelope models, headers)
- `public_api` (exported/consumed APIs)

Each repo will have a mapping file (or defaults) that define "interface paths":
- e.g., `src/**/models/events/**`, `docs/**/TOPICS*.md`, `protocols/**`, etc.

**Naming convention note**: Interface surface model files must follow `omnibase_core` conventions:
- Model files: `model_<name>.py` (e.g., `model_event_envelope.py`)
- Model classes: `Model<Name>` (e.g., `ModelEventEnvelope`)

### 8) Repository integration model

This repo is intended to be **consumed** by other repos in two ways:

- **Schema consumption**:
  - Pull the JSON Schema artifacts and validate YAML files in CI
- **Tooling consumption** (later):
  - Run `onex-change-control` CLI to:
    - validate contract schema
    - classify diffs
    - enforce evidence requirements

Pinning policy:
- Each repo pins a version of drift-control schema/tooling to avoid surprise breakages.

### 9) Rollout phases (high-level)

Phase 0:
- Publish schemas + templates
- Run daily close manually

Phase 1:
- Add minimal CI check: contract exists (seam tickets only) + schema validates

Phase 2:
- Add diff compliance (interfaces_touched enforcement)

Phase 3:
- Add evidence enforcement hooks (tests/validators)

Phase 4:
- Add AI-assisted generation (skeleton then tightening), with explicit `unknowns`

Phase 5:
- Add ONEX-aligned automation (nodes emit reports/events into ledger)

### 10) Open questions (to track outside this doc)

This design assumes decisions will be captured in a separate decision log, including:
- See: `docs/design/DECISION_LOG.md`
- canonical contract location (central vs per repo vs hybrid)
- ticket ID extraction conventions in CI
- emergency bypass policy (hotfixes) without weakening enforcement
- emergency bypass policy:
    - Bypass MUST be explicitly declared in the ticket contract.
    - Bypass events MUST be ledgered and auditable.
    - Every bypass automatically creates a follow-up corrective ticket.
    - Bypass is permitted only for time-critical remediation, not feature delivery.
