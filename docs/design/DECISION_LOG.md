## Decision Log (ONEX Change Control)

This log records **protocol-level change control decisions** required to make system evolution enforceable.

Rules:
- Every decision has an ID, a status, and an owner.
- “Deferred” is allowed, but only with an explicit revisit trigger.
- When we choose, we record consequences so future maintainers understand the tradeoffs.
- Decisions define *constraints*, not preferences; enforcement implications must be explicit.

### Template

```text
Decision: D-XXX <short title>
Status: Proposed | Accepted | Rejected | Deferred
Owner: <name/team>
Date: YYYY-MM-DD

Context:
<why this decision exists>

Options considered:
- A) <option>
- B) <option>
- C) <option>

Decision:
<what we chose>

Enforcement implications:
<what CI, tooling, or human process MUST change as a result of this decision>

Consequences:
- Positive:
  - ...
- Negative / costs:
  - ...
- Follow-ups:
  - ...
```

---

### D-001 Repo canonicality and ownership

Decision: D-001 Repo canonicality and ownership
Status: Accepted
Owner: OmniNode / ONEX
Date: 2025-12-19

Context:
We need a neutral, protocol-level change control authority that can evolve independently of product repos.

Options considered:
- A) Put drift control in `omnibase_core`
- B) Put drift control in `omnibase_spi`
- C) New repo (`onex_change_control`) as neutral governance home

Decision:
Option C. `onex_change_control` is the canonical home for drift-control governance (design, decision log, distribution artifacts, and later tooling). Canonical contract schemas are **owned, versioned, and distributed** by `onex_change_control`. Other repos may consume pinned schema artifacts, but **schemas do not originate outside this repo** (including `omnibase_core`).

Enforcement implications:
- All repos MUST treat `onex_change_control` as the policy and schema distribution authority.
- CI in each repo MUST validate ticket contracts against schemas pinned from `onex_change_control`.
- Repos MUST pin schema versions explicitly to avoid surprise enforcement changes.

Consequences:
- Positive:
  - Avoids coupling governance churn to Core’s bugfix/tech-debt-only policy.
  - Establishes a neutral authority across repos.
- Negative / costs:
  - Requires distribution/integration work in every repo’s CI.
  - Requires contributors to learn and comply with a new change-control surface.
- Follow-ups:
  - Define how repos pin schema/tool versions.

---

### D-006 Contract schema model source-of-truth location

Decision: D-006 Contract schema model source-of-truth location
Status: Reconsidered
Owner: OmniNode / ONEX
Date: 2025-12-19

Context:
Ticket contracts and daily close reports require a **single canonical schema**. We must choose
where the Pydantic models (source of truth) live so that the schema versioning and evolution
are controlled and auditable.

This decision must not conflict with D-001’s requirement that `onex_change_control` be the enforcement and distribution authority.

Options considered:
- A) Models live in `onex_change_control` (governance repo owns schema code)
- B) Models live in `omnibase_spi` (SPI owns schema code)
- C) Models live in `omnibase_core` (Core owns schema code)

Decision:
Option A (revised). Canonical contract schema models live in `onex_change_control`. Core may import generated artifacts if needed, but Core is not the enforcement or ownership boundary.

Enforcement implications:
- `onex_change_control` owns schema evolution, versioning, and JSON Schema exports.
- Other repos MUST consume schemas via pinned artifacts, not by copying models.
- `omnibase_core` MUST NOT become a coupling point for governance-only schema churn.
- **All Pydantic model classes MUST use the `Model` prefix** (e.g., `ModelDayClose`, `ModelTicketContract`) per `omnibase_core` naming conventions.
- **All model files MUST follow `model_<name>.py` convention** (e.g., `model_day_close.py`, `model_ticket_contract.py`).

Consequences:
- Positive:
  - Aligns schema ownership with enforcement authority.
  - Decouples governance iteration speed from Core release cadence.
- Negative / costs:
  - Requires careful dependency hygiene to avoid leaking infra concerns into schema models.
- Follow-ups:
  - Define whether Core imports schemas as generated artifacts or via a lightweight protocol package.

---

### D-007 Dependency layering and release order

Decision: D-007 Dependency layering and release order
Status: Accepted
Owner: OmniNode / ONEX
Date: 2025-12-19

Context:
Change control enforcement (schemas, protocols, infra adapters) depends on a stable layering
model. We need an explicit dependency direction to prevent circular dependencies and to
define where schema models and tooling can live safely.

Options considered:
- A) Core depends on SPI/Infra (high coupling, circular risk)
- B) Infra depends on Core, Core depends on SPI (inverts protocol ownership)
- C) **Core → SPI → Infra** (Core is base; SPI is protocol layer; Infra is implementations)

Decision:
Option C. **Dependency order is: `omnibase_core` → `omnibase_spi` → `omnibase_infra`**.
Core is the package dependency distributed via Poetry release.

Enforcement implications:
- `omnibase_core` MUST NOT depend on `omnibase_infra` (directly or transitively).
- `omnibase_spi` MUST remain protocol-layer only and MUST NOT depend on infra.
- `omnibase_infra*` repos may depend on both `omnibase_core` and `omnibase_spi`.
- Contract schema models living in `onex_change_control` (D-006) are compatible with this layering (they must remain schema-pure and avoid infra/runtime dependencies).

Consequences:
- Positive:
  - Prevents circular dependencies and clarifies ownership boundaries.
  - Makes it safe to consume governance schemas as pinned artifacts without forcing policy churn into Core.
- Negative / costs:
  - Some “governance tooling” code may need to live outside Core if it would pull infra deps.
- Follow-ups:
  - Define an automated check to detect forbidden dependency edges (at least in CI).

---

### D-002 Artifact set: Daily Close Report + Ticket Contract

Decision: D-002 Artifact set: Daily Close Report + Ticket Contract
Status: Accepted
Owner: OmniNode / ONEX
Date: 2025-12-19

Context:
We need artifacts that are cheap to produce, hard to lie with, and machine-checkable.

These artifacts must be shaped so they can be generated by AI, validated mechanically, and later emitted by ONEX nodes without semantic drift.

Options considered:
- A) Daily report only (detect drift, no merge enforcement)
- B) Ticket contracts only (merge gates, but no daily reconciliation)
- C) Both daily close + ticket contracts

Decision:
Option C. Adopt both artifacts.

Enforcement implications:
<what CI, tooling, or human process MUST change as a result of this decision>

Consequences:
- Positive:
  - Daily reconciliation catches drift even if CI gates are immature.
  - Ticket contracts create merge-time enforcement.
- Negative / costs:
  - Adds a daily operational step until automation exists.
- Follow-ups:
  - Define schemas and canonical storage locations.

---

### D-003 Diff classifier correctness policy

Decision: D-003 Diff classifier correctness policy
Status: Accepted
Owner: OmniNode / ONEX
Date: 2025-12-19

Context:
Diff classification will be imperfect initially. We must prevent “classifier uncertainty” from weakening hard constraints.

Options considered:
- A) Require classifier to be complete before enforcing constraints (slow, fragile)
- B) Allow false negatives, but never allow declared constraints to be violated silently

Decision:
Option B. Diff classification may have false negatives, but **contract-declared constraints must never be silently violated**. Classifier uncertainty must never be used to argue for relaxing a declared contract.

Enforcement implications:
<what CI, tooling, or human process MUST change as a result of this decision>

Consequences:
- Positive:
  - Allows incremental rollout of enforcement.
  - Preserves the integrity of explicit contracts.
- Negative / costs:
  - Requires careful definition of “declared constraints” and how to validate them.
- Follow-ups:
  - Specify the minimum set of “interface surfaces” and file/path mappings per repo.

---

### D-004 Emergency bypass policy (hotfixes)

Decision: D-004 Emergency bypass policy (hotfixes)
Status: Accepted
Owner: OmniNode / ONEX
Date: 2025-12-19

Context:
We need a controlled escape hatch for time-critical remediation without normalizing drift.

Options considered:
- A) No bypass allowed (operationally unrealistic)
- B) Bypass allowed without governance (guaranteed drift)
- C) Bypass allowed but must be explicit, auditable, and trigger follow-up correction

Decision:
Option C. Bypass must be explicitly declared in the ticket contract, ledgered/auditable, and automatically create a corrective ticket.

Enforcement implications:
<what CI, tooling, or human process MUST change as a result of this decision>

Consequences:
- Positive:
  - Keeps the system operational under emergencies.
  - Forces cleanup and learning afterward.
- Negative / costs:
  - Needs CI + policy wiring to detect/record bypass events.
- Follow-ups:
  - Define “who can approve bypass” and “how bypass is represented in the ticket contract.”

---

### D-005 Contract location model (central vs per repo vs hybrid)

Decision: D-005 Contract location model (central vs per repo vs hybrid)
Status: Proposed
Owner: OmniNode / ONEX
Date: 2025-12-19

Context:
CI gates run inside each repo, but contracts may need to be shared across repos for seam changes.

This decision determines how contracts are discovered, versioned, and validated across repos, and has direct impact on CI complexity.

Options considered:
- A) Central-only contracts in `onex_change_control`
- B) Per-repo contracts (each repo stores only its own)
- C) Hybrid: central schema + per-repo contracts + optional “cross-repo” aggregation

Decision:
Provisionally adopt Option B (per-repo contracts) for the enforcement prototype.

Enforcement implications:
- CI MUST look for ticket contracts locally within the repo under a fixed path (e.g. `contracts/<ticket_id>.yaml`).
- CI MUST NOT require network access to validate contracts.
- Cross-repo seam changes, if any, MUST be represented via explicit references, not implicit discovery.

Consequences:
- Positive:
  - Simplifies CI wiring and reduces sources of nondeterminism.
  - Enables rapid prototyping of enforcement with minimal tooling.
- Negative / costs:
  - Cross-repo seams require explicit coordination rather than implicit sharing.
- Follow-ups:
  - Define a hybrid extension for cross-repo seam contracts if needed.
  - Revisit this decision after one full enforcement pilot.

---

### D-008 Schema purity constraints (non-negotiable)

Decision: D-008 Schema purity constraints (non-negotiable)
Status: Accepted
Owner: OmniNode / ONEX
Date: 2025-12-19

Context:
With schema models living in `onex_change_control` (D-006), we must prevent this repo from
becoming a dumping ground for runtime logic or infra coupling. If schema models become
“tooling code,” consumers will inherit governance churn and transitive dependencies.

Options considered:
- A) No purity constraints (fast iteration, guaranteed drift/coupling)
- B) Best-effort purity (guideline only)
- C) Hard purity constraints with enforcement (CI) and explicit allowed dependencies

Decision:
Option C. Schema models in `onex_change_control` are **protocol-like** and must remain **pure**.

Enforcement implications:
- All schema/model modules MUST:
  - Avoid importing `omnibase_infra*` (directly or transitively).
  - Avoid runtime execution logic (no "do the check" code in schema modules).
  - Avoid environment assumptions (no reading env vars, clocks, filesystem, network).
  - Prefer static, declarative constraints (types, enums, regexes, bounded lists).
  - **Follow `omnibase_core` naming conventions**:
    - Model classes MUST use `Model` prefix (e.g., `ModelDayClose`, `ModelTicketContract`).
    - Model files MUST follow `model_<name>.py` convention (e.g., `model_day_close.py`, `model_ticket_contract.py`).
    - Enum classes MUST use `Enum` prefix (e.g., `EnumInterfaceSurface`, `EnumDriftType`).
    - Enum files MUST follow `enum_<name>.py` convention (e.g., `enum_interface_surface.py`, `enum_drift_type.py`).
- CI MUST implement at least:
  - an import boundary check (fail on forbidden imports)
  - a purity check (fail on disallowed stdlib usage: `os.environ`, `time.time`, `datetime.now`, `pathlib.Path(...).read_*`, networking clients)
  - **a naming convention check** (fail on model classes without `Model` prefix, model files without `model_` prefix, enum classes without `Enum` prefix, enum files without `enum_` prefix)
- If a schema requires shared "types," those types MUST live in schema-pure modules within `onex_change_control` (or a dedicated protocol package), not in infra.

Consequences:
- Positive:
  - Keeps `onex_change_control` safe to consume as pinned artifacts.
  - Prevents governance schema churn from dragging in runtime coupling.
- Negative / costs:
  - Some convenience helpers must live outside schema modules (e.g., in a CLI package/module).
- Follow-ups:
  - Define the exact allowed dependency set for schema modules (stdlib + pydantic only is the recommended baseline).
  - Add an initial CI check for forbidden imports.
  - Add CI checks for naming convention compliance (model/enum class and file naming).
