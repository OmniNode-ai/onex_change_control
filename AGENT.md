# AGENT.md -- onex_change_control

> LLM navigation guide. Points to context sources -- does not duplicate them.

## Context

- **Drift policies**: `src/onex_change_control/policies/`
- **Architecture**: `docs/`
- **Conventions**: `CLAUDE.md`

## Commands

- Tests: `uv run pytest -m unit`
- Lint: `uv run ruff check src/ tests/`
- Type check: `uv run mypy src/`
- Pre-commit: `pre-commit run --all-files`

## Cross-Repo

- Shared platform standards: `~/.claude/CLAUDE.md`

## Rules

- Drift detection and governance enforcement
- Policy files define what constitutes drift
- Never suppress drift alerts without documented exemption
