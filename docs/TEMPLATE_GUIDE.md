# Template Guide for ONEX Change Control

This guide provides detailed documentation for using the YAML templates in the `templates/` directory. For quick reference, see the inline comments in the templates themselves.

## Table of Contents

- [Day Close Template](#day-close-template)
- [Ticket Contract Template](#ticket-contract-template)
- [Common Patterns](#common-patterns)

## Day Close Template

### Overview

The Day Close template (`day_close.template.yaml`) is used to create daily reconciliation reports that track planned vs. actual work across the ONEX ecosystem.

**Goal**: Deterministic daily reconciliation of plan vs. actual work across repos.  
**Rule**: Every actual item must map to a plan item OR be recorded as `drift_detected`.

### Field Reference

#### `schema_version`
- **Type**: String
- **Required**: Yes
- **Value**: `"1.0.0"` (current schema version)
- **Description**: Schema version for validation compatibility

#### `date`
- **Type**: String (ISO date format)
- **Required**: Yes
- **Format**: `YYYY-MM-DD` (e.g., `"2025-12-21"`)
- **Description**: The date this day close report covers

#### `process_changes_today`
- **Type**: List of objects
- **Required**: Yes (can be empty list `[]`)
- **Description**: Daily process evolution (keep agility reviewable + rollbackable)
- **Fields**:
  - `change`: What changed in the process today
  - `rationale`: Why we changed it
  - `replaces`: What it replaces / previous behavior

#### `plan`
- **Type**: List of objects
- **Required**: Yes (can be empty list `[]`)
- **Description**: Planned requirements for today
- **Fields**:
  - `requirement_id`: Identifier for the requirement (e.g., `"MVP-2WAY-REGISTRATION"`)
  - `summary`: Brief description of the requirement

#### `actual_by_repo`
- **Type**: List of objects
- **Required**: Yes (can be empty list `[]`)
- **Description**: Actual work completed by repository
- **Fields**:
  - `repo`: Repository identifier (e.g., `"OmniNode-ai/omnibase_core"`)
  - `prs`: List of PR objects, each containing:
    - `pr`: PR number (must be >= 1)
    - `title`: PR title
    - `state`: `"merged"` or `"open"`
    - `notes`: Why it matters / what it unblocks

#### `drift_detected`
- **Type**: List of objects
- **Required**: Yes (can be empty list `[]`)
- **Description**: Work that happened but wasn't in plan
- **Unknown Handling**: If you're unsure about drift, record it here with category and mark `correction_for_tomorrow` as `"TBD - requires investigation"`
- **Fields**:
  - `drift_id`: Unique identifier (e.g., `"DRIFT-001"`, `"DRIFT-002"`)
  - `category`: Valid values: `"scope"` | `"architecture"` | `"interfaces"` | `"dependencies"` | `"infra"` | `"process"`
  - `evidence`: What changed / where (PRs, commits, files)
  - `impact`: Why it matters
  - `correction_for_tomorrow`: Specific fix / decision / ticket

#### `invariants_checked`
- **Type**: Object
- **Required**: Yes
- **Description**: ONEX architectural invariants
- **Unknown Handling**: Use `"unknown"` if invariant status cannot be determined (e.g., tests not run yet, investigation pending)
- **Valid values for each field**: `"pass"` | `"fail"` | `"unknown"`
- **Fields**:
  - `reducers_pure`: Reducers are pure (no I/O)
  - `orchestrators_no_io`: Orchestrators perform no I/O
  - `effects_do_io_only`: Effects perform I/O only
  - `real_infra_proof_progressing`: Real infrastructure proof is progressing

#### `corrections_for_tomorrow`
- **Type**: List of strings
- **Required**: Yes (can be empty list `[]`)
- **Description**: Actionable corrections for tomorrow

#### `risks`
- **Type**: List of objects
- **Required**: Yes (can be empty list `[]`)
- **Description**: Risks identified today
- **Fields**:
  - `risk`: Short risk description
  - `mitigation`: Short mitigation description

## Ticket Contract Template

### Overview

The Ticket Contract template (`ticket_contract.template.yaml`) is used to create acceptance specifications for tickets that touch cross-repo interfaces.

### Field Reference

#### `schema_version`
- **Type**: String
- **Required**: Yes
- **Value**: `"1.0.0"` (current schema version)
- **Description**: Schema version for validation compatibility

#### `ticket_id`
- **Type**: String
- **Required**: Yes
- **Format**: Ticket identifier (e.g., `"OMN-962"`)
- **Description**: The Linear ticket identifier

#### `summary`
- **Type**: String
- **Required**: Yes
- **Description**: One-line summary of the ticket

#### `is_seam_ticket`
- **Type**: Boolean
- **Required**: Yes
- **Description**: If `true`, this ticket touches cross-repo interfaces and must be drift-controlled. Set to `true` if the ticket affects interfaces between repositories (events, topics, protocols, etc.)

#### `interface_change`
- **Type**: Boolean
- **Required**: Yes
- **Description**: If `true`, this ticket changes interface surfaces. If `false`, `interfaces_touched` must be explicitly empty (`[]`). If `true`, `interfaces_touched` should list all affected interface surfaces.

#### `interfaces_touched`
- **Type**: List of strings
- **Required**: Yes (can be empty list `[]`)
- **Valid values**: `"events"` | `"topics"` | `"protocols"` | `"envelopes"` | `"public_api"`
- **Rules**:
  - If `interface_change=false`, this must be empty: `[]`
  - If `interface_change=true`, this should list all affected surfaces
- **Unknown Handling**: If unsure which interfaces are affected, set `interface_change=true` and leave this empty temporarily. Populate before ticket completion.

> **⚠️ IMPORTANT**: Leaving `interfaces_touched` empty when `interface_change=true` will cause `contract.is_complete` to return `False`, blocking ticket completion. This ensures interface changes are always documented before closing.

#### `evidence_requirements`
- **Type**: List of objects
- **Required**: Yes (can be empty list `[]`)
- **Description**: What proof is required for this ticket to be considered complete
- **Unknown Handling**: If evidence requirements are not yet known, leave empty but document in ticket description that requirements will be added
- **Fields**:
  - `kind`: Valid values: `"tests"` | `"docs"` | `"ci"` | `"benchmark"` | `"manual"`
  - `description`: What evidence must exist
  - `command`: How to reproduce, if applicable (optional: `null` or omit if not applicable)

**Evidence Kinds**:
- `"tests"`: Automated test coverage (unit, integration, property-based)
- `"docs"`: Documentation updates (ADRs, guides, API docs)
- `"ci"`: CI/CD pipeline changes (new checks, validation gates)
- `"benchmark"`: Performance benchmarks (latency, throughput, memory)
- `"manual"`: Manual verification steps (testing checklist, manual QA)

**Examples**:
```yaml
# Example with command:
- kind: "tests"
  description: "Unit tests for ModelDayClose date validation"
  command: "poetry run pytest tests/test_models.py::test_day_close_date_validation"

# Example without command (docs don't need reproduction commands):
- kind: "docs"
  description: "Update ADR for ticket contract schema"
  command: null  # or simply omit this field
```

#### `dod_evidence`
- **Type**: List of objects (optional; omit if not needed)
- **Description**: Executable DoD checks. Each item's `checks[].check_value` is a shell command (`check_type: "command"`) run from the `onex_change_control` repo root by `scripts/ci/run_contract_compliance_check.py`; exit 0 = pass.
- **`check_value` must be falsifiable — it must be able to fail if the work is actually wrong.** A command that reads the receipt file the check itself is stamped in and greps that file for `status: PASS` is **not evidence**: the receipt says PASS because the author wrote PASS, and the check confirms the author wrote PASS. It passes identically whether the code is correct or broken.

  > **⚠️ DO NOT DO THIS** (measured OMN-14417 at 2,137/6,915 = 31.3% of the live corpus, and 98.4% of contracts created in the last 7 days — this exact shape, copy-pasted PR to PR):
  > ```yaml
  > checks:
  >   - check_type: "command"
  >     check_value: "grep -q '^status: PASS$' drift/dod_receipts/OMN-XXXX/dod-001/command.yaml"
  > ```
  > This derives tier **L0** (content-free) under the contract substance floor (`scripts/validation/check_contract_substance_floor.py`, OMN-14409) and cannot satisfy it.

  Use a command that actually asserts something about the change instead — for evidence bound to a product PR, `gh pr checks <n> --repo <owner>/<repo>` (or the `$PR_NUMBER`/`$REPO` placeholders, auto-injected by the compliance-check runner whenever the command contains `gh `) derives **L1**: it fails when CI fails, and it satisfies the "no cross-repo filesystem paths" execution constraint (below) exactly as well as a self-grep does, without being circular. A test run (`pytest ...`), a static assertion over source (`grep`/`rg` pinning a real symbol in the tree, not in the receipt corpus), or a runtime readback (`psql`/`rpk`/`curl`) are the other substantive families the deriver recognizes.

  A **binding/stamp item** — one whose only job is to prove the OCC PR's own identity (e.g. `dod-occ-self`, matching this contract to its own PR/commit) rather than to prove the *product* work is correct — is exempt from this rule; a self-referential or existence check is legitimate there. It is only the item(s) meant to be *the* proof of the ticket's work that must clear L1.

  **Execution constraint**: `run_contract_compliance_check.py` sets `$CONTRACT_REPO_DIR` (this repo's root) but does **not** set `$OMNI_HOME`, so a `check_value` that greps a path in a sibling repo (`docs/handoff/...` in `omni_home`, etc.) will always fail — not because self-reference is required, but because the command must not depend on a filesystem path outside this checkout. A `gh`/API-based check has no such path dependency and works unmodified.

**Example (correct, falsifiable, for a product-PR evidence item):**
```yaml
dod_evidence:
  - id: "dod-omnimarket-pr-1719"
    description: "omnimarket PR #1719 CI green at head <sha>."
    source: "manual"
    status: "verified"
    checks:
      - check_type: "command"
        check_value: "gh pr checks 1719 --repo OmniNode-ai/omnimarket"
  - id: "dod-occ-self"
    description: "OCC self-binding: this branch carries the contract + PASS receipts."
    source: "manual"
    status: "verified"
    checks:
      - check_type: "command"
        check_value: "grep -q '^status: PASS$' drift/dod_receipts/OMN-XXXX/dod-occ-self/command.yaml"
```

#### `emergency_bypass`
- **Type**: Object
- **Required**: Yes
- **Description**: Allows temporary bypass of drift control enforcement
- **WARNING**: Use only in genuine emergencies (production incidents, critical blockers)
- **Fields**:
  - `enabled`: Boolean - Set to `true` only in genuine emergencies
  - `justification`: String - Required if `enabled=true`: explain why bypass is necessary
  - `follow_up_ticket_id`: String - Required if `enabled=true`: ticket that will properly address this
- **Unknown Handling**: If you're unsure whether bypass is needed, set `enabled=false` and document the situation in the ticket description for review

**Example of emergency bypass (DO NOT USE unless genuine emergency)**:
```yaml
emergency_bypass:
  enabled: true
  justification: "Production incident requires immediate hotfix. Full contract will be created in follow-up ticket."
  follow_up_ticket_id: "OMN-999"
```

## Common Patterns

### Unknown Handling

Both templates support "unknown" states for fields where the status cannot be determined:

- **Day Close**: Use `"unknown"` for invariant statuses when tests haven't run yet or investigation is pending
- **Ticket Contract**: Leave `interfaces_touched` empty temporarily if unsure, but note that `is_complete` will be `False` until populated

### Empty Lists

All list fields accept empty lists (`[]`) as valid values. Use empty lists when:
- No items exist for that field
- Items are not yet known (document in notes/description)

### Validation

After filling out a template, validate it against the schema:

**Option 1: CLI Tool (Recommended)**
```bash
# Validate one or more files
poetry run python scripts/validate_yaml.py drift/day_close/2025-12-21.yaml
poetry run python scripts/validate_yaml.py contracts/*.yaml

# Validate all day_close files
poetry run python scripts/validate_yaml.py drift/day_close/*.yaml
```

**Option 2: Pytest**
```bash
# Run template validation tests
poetry run pytest tests/test_yaml_parsing.py -k day_close
poetry run pytest tests/test_yaml_parsing.py -k ticket_contract
```

### Schema Alignment

Both templates are schema-aligned with their corresponding Pydantic models:
- Day Close → `ModelDayClose`
- Ticket Contract → `ModelTicketContract`

See `src/onex_change_control/models/` for the model definitions.
