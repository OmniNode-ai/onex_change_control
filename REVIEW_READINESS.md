# Implementation Readiness Review

## Executive Summary

**Status**: ✅ **Nearly ready for downstream consumption** - Minor improvements needed

**What's Working:**
- ✅ Core Pydantic models are complete and tested
- ✅ Models are properly exported and importable
- ✅ Local validation CLI works
- ✅ CI gates enforce purity and naming conventions

**What's Missing (Minor):**
- ⚠️ No CLI entrypoint configured (scripts need to be in package)
- ⚠️ No version mapping policy documented
- ⚠️ Limited downstream usage documentation

---

## Critical Gaps for OMN-967 (Publishing)

### 1. **No CLI Entrypoint** 🟡 **HIGH PRIORITY**

**Issue**: `validate_yaml.py` is not exposed as a Poetry CLI entrypoint.

**Current State**: Downstream repos can use models directly:
```python
from onex_change_control import ModelTicketContract
import yaml

with open("contracts/OMN-123.yaml") as f:
    data = yaml.safe_load(f)
ModelTicketContract.model_validate(data)
```

Or use the script (if moved to package):
```bash
poetry run python scripts/validate_yaml.py contracts/OMN-123.yaml
```

**Fix Required** (Optional - models work without it):
```toml
[tool.poetry.scripts]
validate-yaml = "onex_change_control.scripts.validate_yaml:main"
```

**Note**: Scripts are currently in `scripts/` not `src/onex_change_control/scripts/`, so this needs refactoring if we want CLI entrypoint.

### 2. **No Version Mapping Policy** 🟡 **MEDIUM PRIORITY**

**Issue**: OMN-967 requires "Define how versions map to schema versions (explicit policy)".

**Current State**: Package version is `0.1.0`, schema version is `1.0.0` - no documented relationship.

**Fix Required**: Document in README or `docs/`:
- Package version `1.0.0` → Schema version `1.0.0`
- Semantic versioning policy
- Breaking change policy

### 3. **Limited Downstream Usage Documentation** 🟡 **MEDIUM PRIORITY**

**Issue**: README has basic usage but could be more comprehensive.

**Current State**: README shows basic model usage.

**Enhancement**: Add CI/pre-commit examples:
```markdown
### Using in CI/Pre-commit

Create a validation script in your repo:

```python
# scripts/validate_contracts.py
from pathlib import Path
from onex_change_control import ModelTicketContract
import yaml
import sys

contract_dir = Path("contracts")
for contract_file in contract_dir.glob("*.yaml"):
    with open(contract_file) as f:
        data = yaml.safe_load(f)
    try:
        ModelTicketContract.model_validate(data)
        print(f"✅ {contract_file} is valid")
    except ValidationError as e:
        print(f"❌ {contract_file} is invalid: {e}")
        sys.exit(1)
```
```

---

## Architecture Issues

### Scripts Location

**Current**: Scripts are in `scripts/` (root level)
**Problem**: Not importable as a package module
**Options**:
1. Move to `src/onex_change_control/scripts/` (better for package)
2. Keep in `scripts/` but add as package data (works but less clean)

**Recommendation**: Move to `src/onex_change_control/scripts/` for proper package structure.

---

## What Works Well ✅

1. **Pydantic Models**: Complete, tested, pure, properly exported
2. **Model Import**: Downstream can `from onex_change_control import ModelDayClose, ModelTicketContract`
3. **Validation CLI**: Works locally, good error messages
4. **CI Gates**: Enforce purity and naming conventions
5. **Test Coverage**: 69 tests, comprehensive

---

## Recommended Action Plan

### Before OMN-967 (Publishing):

1. **Optional: Move scripts to package + add CLI entrypoint** (30 min)
   - Move `scripts/` → `src/onex_change_control/scripts/`
   - Update imports
   - Add CLI entrypoint to `pyproject.toml`
   - Update tests
   - **Note**: Not required - models work without CLI

2. **Document version mapping** (10 min)
   - Add policy to README or design docs

3. **Enhance downstream usage docs** (15 min)
   - Add CI/pre-commit examples to README

### After OMN-967 (Before OMN-968):

4. **Test downstream consumption** (30 min)
   - Create test repo or branch
   - Install package from local build
   - Verify model imports work
   - Verify validation works

---

## Estimated Time to Ready

**Total**: ~1 hour of work (mostly optional enhancements).

**Critical Path**: Models are already exportable and usable. CLI entrypoint is nice-to-have.

---

## Questions to Resolve

1. **Scripts location**: Move to package or keep as root-level?
2. **Version policy**: What's the relationship between package version and schema version?
3. **CLI naming**: `validate-yaml` or `onex-validate-yaml`?
4. **Schema discovery**: Should we provide a function to list available schema versions?


