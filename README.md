## ONEX Drift Control

Centralized, versioned **governance + enforcement** for preventing cross-repo drift across the ONEX ecosystem.

### What this repo is (as-is intent)

- **Schemas** for:
  - `day_close.yaml` (daily reconciliation artifact)
  - `contracts/<ticket_id>.yaml` (ticket contract / acceptance spec)
- **Design docs** describing the enforcement model and rollout phases.
- (Later) **tooling** to validate contracts against diffs and enforce merge gates in CI.

### Naming Conventions

All schema models in this repo **MUST** follow `omnibase_core` naming conventions:
- **Model classes**: `Model<Name>` (e.g., `ModelDayClose`, `ModelTicketContract`)
- **Model files**: `model_<name>.py` (e.g., `model_day_close.py`, `model_ticket_contract.py`)
- **Enum classes**: `Enum<Name>` (e.g., `EnumInterfaceSurface`, `EnumDriftType`)
- **Enum files**: `enum_<name>.py` (e.g., `enum_interface_surface.py`, `enum_drift_type.py`)

See: `omnibase_core/docs/conventions/NAMING_CONVENTIONS.md` for full details.

### Where to start

- `docs/design/DESIGN_DRIFT_CONTROL_SYSTEM.md`
- `docs/design/DECISION_LOG.md`
- `docs/planning/IMPLEMENTATION_PLAN.md`
