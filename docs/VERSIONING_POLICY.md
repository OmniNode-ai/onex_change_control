# Versioning Policy

This document defines the versioning policy for the `onex-change-control` package and its schema artifacts.

## Package Version vs. Schema Version

The package version (defined in `pyproject.toml`) and schema version (used in YAML files) follow a **1:1 mapping**:

- **Package version `1.0.0`** → **Schema version `1.0.0`**
- **Package version `1.1.0`** → **Schema version `1.1.0`**
- **Package version `2.0.0`** → **Schema version `2.0.0`**

### Current Versions

- **Package version**: `0.1.0` (pre-release)
- **Schema version**: `1.0.0` (current schema format)

**Note**: The package is currently in pre-release (`0.1.0`), but schemas use `1.0.0` to indicate the stable schema format. When the package reaches `1.0.0`, the versions will align.

## Semantic Versioning (SemVer)

Both package and schema versions follow [Semantic Versioning 2.0.0](https://semver.org/):

- **MAJOR.MINOR.PATCH** (e.g., `1.2.3`)
- **MAJOR**: Breaking changes (incompatible API or schema changes)
- **MINOR**: New features (backward-compatible)
- **PATCH**: Bug fixes (backward-compatible)

## Schema Version Policy

### Breaking Changes (Major Version Bump)

A major version bump is required when:

1. **Field removal**: Removing a required or optional field from a model
2. **Field type changes**: Changing a field's type (e.g., `str` → `int`)
3. **Enum value removal**: Removing an enum value
4. **Required field addition**: Adding a new required field (makes existing YAML invalid)
5. **Validation rule changes**: Making validation stricter (e.g., adding new required constraints)

### Non-Breaking Changes (Minor/Patch Version Bump)

These changes are backward-compatible:

1. **Optional field addition**: Adding a new optional field
2. **Enum value addition**: Adding new enum values
3. **Validation relaxation**: Making validation less strict
4. **Documentation improvements**: Clarifying field descriptions
5. **Bug fixes**: Fixing validation bugs that incorrectly rejected valid data

## Version Mapping Examples

| Package Version | Schema Version | Notes |
|----------------|---------------|-------|
| `0.1.0` | `1.0.0` | Pre-release package, stable schema |
| `1.0.0` | `1.0.0` | First stable release |
| `1.1.0` | `1.1.0` | New optional fields added |
| `1.1.1` | `1.1.1` | Bug fix in validation |
| `2.0.0` | `2.0.0` | Breaking change (field removed) |

## Schema Immutability

Once a schema version is released:

- **Field names are immutable** within that version line
- **Enum values are immutable** within that version line
- **Required/optional status is immutable** within that version line

This ensures that downstream consumers can rely on stable schema contracts.

## Migration Strategy

When a breaking change is required:

1. **Release new major version** (e.g., `2.0.0`)
2. **Document migration guide** in `CHANGELOG.md`
3. **Maintain backward compatibility** in validation tooling (support both versions)
4. **Provide deprecation period** for old version (if applicable)

## Downstream Consumption

Downstream repos should pin package versions using SemVer ranges:

```toml
[tool.poetry.dependencies]
onex-change-control = "^1.0.0"  # Allows 1.x.x, blocks 2.0.0+
```

This ensures:
- Automatic updates for minor/patch versions (bug fixes, new optional fields)
- Protection from breaking changes (major version bumps require explicit upgrade)

## References

- [Semantic Versioning 2.0.0](https://semver.org/)
- [Implementation Plan](planning/IMPLEMENTATION_PLAN.md)
- [Design Document](design/DESIGN_DRIFT_CONTROL_SYSTEM.md)
