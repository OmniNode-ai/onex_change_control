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

### Versioning Policy

Package version and schema version follow a **1:1 mapping**:
- Package version `1.0.0` → Schema version `1.0.0`
- Package version `1.1.0` → Schema version `1.1.0`

See [`docs/VERSIONING_POLICY.md`](docs/VERSIONING_POLICY.md) for full details on:
- Semantic versioning policy
- Breaking vs. non-breaking changes
- Schema immutability rules
- Migration strategy

### Downstream Usage

#### Python API (Direct Model Validation)

This package exports Pydantic models that can be used directly for validation:

```python
from onex_change_control import ModelDayClose, ModelTicketContract
import yaml
from pydantic import ValidationError

# Validate a YAML file
with open("contracts/OMN-123.yaml") as f:
    data = yaml.safe_load(f)

try:
    ModelTicketContract.model_validate(data)
    print("✅ Valid")
except ValidationError as e:
    print(f"❌ Invalid: {e}")
```

#### CLI Validation

For command-line validation, use the `validate-yaml` command:

```bash
# Validate a single file
poetry run validate-yaml contracts/OMN-123.yaml

# Validate multiple files
poetry run validate-yaml contracts/*.yaml

# Validate day close reports
poetry run validate-yaml drift/day_close/2025-12-25.yaml
```

#### CI/Pre-commit Integration

Create a validation script in your repo:

```python
# scripts/validate_contracts.py
from pathlib import Path
from onex_change_control import ModelTicketContract
import yaml
import sys
from pydantic import ValidationError

contract_dir = Path("contracts")
errors = []

for contract_file in contract_dir.glob("*.yaml"):
    with open(contract_file) as f:
        data = yaml.safe_load(f)
    try:
        ModelTicketContract.model_validate(data)
        print(f"✅ {contract_file} is valid")
    except ValidationError as e:
        print(f"❌ {contract_file} is invalid: {e}")
        errors.append(contract_file)

if errors:
    sys.exit(1)
```

Add to your CI workflow:

```yaml
# .github/workflows/validate.yml
- name: Install Poetry
  uses: snok/install-poetry@v1
- name: Install dependencies
  run: poetry install
- name: Validate contracts
  run: poetry run python scripts/validate_contracts.py
```

Or use as a pre-commit hook:

```yaml
# .pre-commit-config.yaml
repos:
  - repo: local
    hooks:
      - id: validate-contracts
        name: Validate ticket contracts
        entry: poetry run python scripts/validate_contracts.py
        language: system
        files: ^contracts/.*\.yaml$
```

#### Package Installation

Add to your `pyproject.toml`:

```toml
[tool.poetry.dependencies]
# Current (pre-release):
onex-change-control = "^0.1.0"
# After 1.0.0 release:
# onex-change-control = "^1.0.0"  # Pin to major version for stability
```

Then install:

```bash
poetry add onex-change-control
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

### Documentation

- **Design**: [`docs/design/DESIGN_DRIFT_CONTROL_SYSTEM.md`](docs/design/DESIGN_DRIFT_CONTROL_SYSTEM.md)
- **Decisions**: [`docs/design/DECISION_LOG.md`](docs/design/DECISION_LOG.md)
- **Implementation Plan**: [`docs/planning/IMPLEMENTATION_PLAN.md`](docs/planning/IMPLEMENTATION_PLAN.md)
- **Versioning Policy**: [`docs/VERSIONING_POLICY.md`](docs/VERSIONING_POLICY.md)
- **Template Guide**: [`docs/TEMPLATE_GUIDE.md`](docs/TEMPLATE_GUIDE.md)
