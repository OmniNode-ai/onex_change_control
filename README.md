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

### Development Setup

1. **Install Poetry** (if not already installed):
   ```bash
   curl -sSL https://install.python-poetry.org | python3 -
   ```

2. **Install dependencies**:
   ```bash
   poetry install
   ```

3. **Install pre-commit hooks** (requires Poetry environment):
   ```bash
   poetry run pre-commit install
   poetry run pre-commit install --hook-type pre-push
   ```

   **Note**: Pre-commit hooks require Poetry to be installed and dependencies available, as they use `poetry run` to execute ruff, mypy, etc.

4. **Run tests**:
   ```bash
   poetry run pytest
   ```

### Downstream Usage

This package exports Pydantic models that can be used directly for validation:

```python
from onex_change_control import ModelDayClose, ModelTicketContract
import yaml

# Validate a YAML file
with open("contracts/OMN-123.yaml") as f:
    data = yaml.safe_load(f)

ModelTicketContract.model_validate(data)  # Raises ValidationError if invalid
```

For CLI validation, use the `validate-yaml` command:

```bash
poetry run validate-yaml contracts/OMN-123.yaml
```

### Schema Purity Checking

The package includes a CLI tool to enforce schema module purity and naming conventions:

```bash
# Check all schema files (exits with code 1 on violations)
poetry run check-schema-purity

# Warn-only mode (useful for gradual adoption in CI)
poetry run check-schema-purity --warn-only

# Disable colored output (useful for CI environments)
poetry run check-schema-purity --no-color
```

**What it checks:**
- **Purity (D-008)**: No env/fs/network/time usage in schema modules
- **Naming conventions**: `Model*`/`model_*`, `Enum*`/`enum_*`
- **No environment-dependent defaults**

**CLI Options:**
- `--warn-only`: Print violations but exit with code 0 (for gradual adoption)
- `--no-color`: Disable colored output (useful for CI environments)

### Where to start

- `docs/design/DESIGN_DRIFT_CONTROL_SYSTEM.md`
- `docs/design/DECISION_LOG.md`
- `docs/planning/IMPLEMENTATION_PLAN.md`
