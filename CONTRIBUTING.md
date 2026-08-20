# Contributing to onex_change_control

## Overview

`onex_change_control` is the governance and enforcement hub for the ONEX platform. Contributions here affect every downstream repo that consumes schemas, CLI tools, or enforcement policies. Take extra care with schema changes — breaking changes require a major version bump per [docs/VERSIONING_POLICY.md](docs/VERSIONING_POLICY.md).

---

## Prerequisites

- Python 3.12+
- [`uv`](https://github.com/astral-sh/uv) for dependency management
- `pre-commit` for local enforcement hooks

```bash
uv sync --all-groups
pre-commit install
```

---

## Development Workflow

### 1. Create a worktree

All work happens in a git worktree, never directly in `omni_home/onex_change_control/`:

```bash
TICKET="OMN-XXXX"
git -C "$OMNI_HOME/onex_change_control" worktree add \
  "$OMNI_WORKTREES_ROOT/$TICKET/onex_change_control" \
  -b "jonah/$TICKET-description"
```

Where `OMNI_WORKTREES_ROOT` is the platform-configured worktrees directory.

### 2. Make changes

- Schema changes go in `src/onex_change_control/models/` or `src/onex_change_control/enums/`.
- CLI tools go in `src/onex_change_control/scripts/`.
- Tests go in `tests/`.

### 3. Verify before pushing

```bash
# Format and lint
uv run ruff format src/ tests/
uv run ruff check --fix src/ tests/

# Type check
uv run mypy src/ --strict

# Full test suite — no -k filter
uv run pytest tests/ -v

# Pre-commit hooks
pre-commit run --all-files

# Schema purity
uv run check-schema-purity
```

### 4. Naming conventions

All code in this repo follows `omnibase_core` naming conventions:

| Type | Convention | Example |
|------|------------|---------|
| Model classes | `Model<Name>` | `ModelDayClose`, `ModelTicketContract` |
| Model files | `model_<name>.py` | `model_day_close.py` |
| Enum classes | `Enum<Name>` | `EnumDriftCategory` |
| Enum files | `enum_<name>.py` | `enum_drift_category.py` |

### 5. SPDX headers

All source files in `src/`, `tests/`, `scripts/`, `examples/` require MIT SPDX headers.

```bash
# Stamp missing headers
uv run onex spdx fix src tests scripts examples

# Check without writing
uv run onex spdx fix --check src tests scripts examples
```

---

## Schema Purity (D-008)

Schema modules (`models/`, `enums/`) must be pure — no I/O, no env reads, no time calls:

- No `os.environ`, `os.getenv`
- No `open`, `pathlib.Path.read_*`
- No `httpx`, `requests`, `urllib`
- No `datetime.now()`, `time.time()`

Purity is enforced by `uv run check-schema-purity` in CI.

---

## Adding a New CLI Checker

1. Create `src/onex_change_control/scripts/check_<name>.py` with a `main()` function.
2. Add to `pyproject.toml` `[project.scripts]`:
   ```toml
   check-<name> = "onex_change_control.scripts.check_<name>:main"
   ```
3. Wire in `.github/workflows/ci.yml` as a required gate.
4. Add to [docs/README.md](docs/README.md) workflow index.
5. Write tests in `tests/`.

---

## Schema Changes

Schema changes affect every downstream consumer. Before changing a schema:

1. Read [docs/VERSIONING_POLICY.md](docs/VERSIONING_POLICY.md).
2. Determine if the change is breaking (requires major version bump) or non-breaking.
3. Update `pyproject.toml` `[project] version` accordingly.
4. Re-export JSON schemas: the CI determinism check will fail if schemas are stale.
5. Update `CHANGELOG.md`.

---

## PR Requirements

Every PR must:

- Include `OMN-XXXX` in the title.
- Have a `dod_evidence` block in the PR body citing the ticket contract.
- Pass all CI gates (`gh pr checks <num> --watch`).
- Have no unresolved CodeRabbit threads.

See `omni_home/CLAUDE.md` for the full PR checklist.

---

## License

By contributing, you agree that your contributions will be licensed under the [MIT License](LICENSE).
