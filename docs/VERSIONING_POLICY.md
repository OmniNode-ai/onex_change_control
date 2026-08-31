# Versioning Policy

This document defines the versioning policy for the `onex-change-control` package and its schema artifacts.

## Package Version vs. Schema Version

The package version (defined in `pyproject.toml`) and schema version (used in YAML files) follow a **1:1 mapping**:

- **Package version `1.0.0`** → **Schema version `1.0.0`**
- **Package version `1.1.0`** → **Schema version `1.1.0`**
- **Package version `2.0.0`** → **Schema version `2.0.0`**

### Current Versions

- **Package version**: `0.5.1` (`pyproject.toml`, verified 2026-08-25)
- **Schema version**: `1.0.0` (current schema format)

**Note**: In practice the 1:1 mapping above has not held — the package has moved from
`0.1.0` to `0.5.1` across several minor releases while the schema version has stayed at
`1.0.0` the whole time (no schema-breaking change has shipped yet). Treat the 1:1 mapping
as the *rule for when a schema-breaking change ships*, not as a claim that the two numbers
move in lockstep on every release.

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
| `0.5.1` | `1.0.0` | Current live version (2026-08-25) — schema unchanged since `0.1.0` |
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

`onex-change-control` is not published to PyPI — downstream repos consume it as a `uv`
git dependency, pinned to an immutable commit SHA rather than a SemVer range (verified
live in `omnibase_core/pyproject.toml` and `omnibase_infra/pyproject.toml`, 2026-08-25):

```toml
[project]
dependencies = [
    "onex-change-control>=0.1.0",
]

[tool.uv.sources]
onex-change-control = { git = "https://github.com/OmniNode-ai/onex_change_control.git", rev = "<commit-sha>" }
```

The `>=0.1.0` in `[project.dependencies]` is a floor for tooling that reads package
metadata; the `rev` in `[tool.uv.sources]` is what actually resolves, and it is the
`git rev` a maintainer chose, not something `uv` re-resolves against new tags. This
means:
- There is no automatic minor/patch pickup — a consumer stays on its pinned SHA until
  someone bumps `rev` by hand.
- A breaking (major-version-equivalent) schema change does not "block" anything
  automatically the way a SemVer range would; it is caught only when/if the consumer
  re-pins and re-runs its own test suite against the new commit.
- The versioning policy above still governs *when a change counts as breaking*; it does
  not currently govern *how consumers are protected from picking one up*, since there is
  no registry-level range enforcement in this consumption model.

## References

- [Semantic Versioning 2.0.0](https://semver.org/)
- [Design Document](design/DESIGN_DRIFT_CONTROL_SYSTEM.md)
