# DB Boundary Policy

> **Canonical location**: `onex_change_control/docs/policy/db-boundary-policy.md`
> **Enforcement tool**: `uv run check-db-boundary` (entry point in `onex_change_control`)
> **Exception registry**: `onex_change_control/registry/db-boundary-exceptions.yaml`

## Principle

**One service, one database.** Each OmniNode service owns exactly one logical database (or
schema). No service may directly access another service's database tables, models, repositories,
or connection credentials.

Cross-service data access MUST go through the owning service's API or event bus.

## Service Ownership

| Service | Database | Approved Direct Access |
|---------|----------|----------------------|
| `omnibase_infra` | `omnibase_infra` (PostgreSQL) | Yes -- owns the DB |
| `omniintelligence` | `omniintelligence` schema | Yes -- owns the DB |
| `omnimemory` | `omnimemory` schema | Yes -- owns the DB |
| `omnidash` | `omnidash_analytics` (read model) | Yes -- owns the read model |
| `omniclaude` | None (stateless plugin) | N/A |
| `omnibase_core` | None (pure models/contracts) | N/A |
| `omnibase_spi` | None (interface definitions) | N/A |
| `onex_change_control` | None (governance tooling) | N/A |

## Prohibited Patterns

The following patterns violate the DB boundary policy:

### 1. Cross-Service DB Environment Variables

A service MUST NOT reference another service's database connection string.

```python
# VIOLATION: omniintelligence using omnimemory's DB URL
db_url = os.getenv("OMNIMEMORY_DB_URL")
```

### 2. Cross-Service Model/Repository Imports

A service MUST NOT import another service's database models or repository classes.

```python
# VIOLATION: omniintelligence importing omnimemory models
from omnimemory.models import DocumentModel
from omnimemory.repositories import DocumentRepository
```

### 3. Shared Database Access

Services MUST NOT share a single database connection pool or use a common "bridge" database
for cross-service communication. The legacy `omninode_bridge` database pattern is deprecated.

## Allowed Shared Variables

The following environment variables are shared infrastructure and do NOT constitute a boundary
violation:

- `POSTGRES_PASSWORD` -- superuser credential for local dev bootstrap
- `POSTGRES_HOST` / `POSTGRES_PORT` -- shared infrastructure coordinates
- `OMNIBASE_INFRA_DB_URL` -- used only by `omnibase_infra` service itself

## Exception Process

If a service legitimately needs cross-boundary database access (e.g., read models,
test fixtures, bootstrap scripts), the exception must be registered in:

```
onex_change_control/registry/db-boundary-exceptions.yaml
```

Each exception requires:
- **repo**: Which repository contains the violation
- **file**: Exact file path
- **usage**: Brief description of what the cross-boundary access does
- **reason_category**: One of `READ_MODEL`, `TEST_ONLY`, `BOOTSTRAP`, `LEGACY_MIGRATION`
- **justification**: Why this exception is necessary
- **owner**: Who is responsible for the exception
- **approved_by**: Who approved it
- **review_by**: YYYY-MM date when the exception must be re-evaluated
- **status**: `APPROVED`, `PENDING`, `EXPIRED`, or `REVOKED`

## Enforcement

### Automated Validation

Run the validator against any service:

```bash
# Check a specific service
uv run check-db-boundary --repo omniintelligence --path /path/to/omniintelligence

# Validate the exception registry
uv run check-db-boundary --validate-all --registry registry/db-boundary-exceptions.yaml
```

### Pre-commit Hook

The exception registry is validated automatically on commit via a pre-commit hook
configured in `onex_change_control/.pre-commit-config.yaml`.

### Enforcement Coverage

`check-db-boundary` v1 enforces the following direct violation patterns:

1. **Cross-service DB env-var usage** -- detects `os.getenv("OMNI<OTHER_SERVICE>_DB_URL")` patterns
2. **Direct cross-service model/repository imports** -- AST-based detection of `from <other_service>.models import` and `from <other_service>.repositories import`
3. **Exception registry validity** -- validates YAML schema, checks for expired review dates

**Out of scope for v1** (manual review concern):
- Indirect patterns: helper wrappers around env access
- Dynamic imports: `importlib.import_module()` with cross-service targets
- Proxy modules: shared utility abstractions that smuggle cross-service DB access
- String-built module paths

## Current State

As of 2026-03-13, all OmniNode services have **zero direct DB boundary violations** in the
checked categories. Each service properly uses its own database credentials and does not
directly import models or repositories from other services.

## Related Tickets

- **OMN-4815**: DB boundary policy infrastructure fix (moved from omni_home to onex_change_control)
- **OMN-4792**: Epic: Permanent Cross-Repo Integration Health Enforcement
- **OMN-4850--4853**: Epic 3 per-service boundary verification tickets
