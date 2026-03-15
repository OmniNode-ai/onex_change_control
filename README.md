# onex_change_control

Drift detection and governance enforcement for the ONEX ecosystem.

[![CI](https://github.com/OmniNode-ai/onex_change_control/actions/workflows/ci.yml/badge.svg)](https://github.com/OmniNode-ai/onex_change_control/actions/workflows/ci.yml)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

## Install

```bash
uv add onex-change-control
```

## Key Features

- **Drift detection**: Detect configuration and behavioral drift across ONEX repos
- **Governance schemas**: `day_close.yaml` for daily reconciliation, `contracts/*.yaml` for ticket specs
- **Branch protection audit**: Verify branch protection rules across all repos
- **Version pin checking**: Ensure dependency versions are pinned consistently
- **Enforcement policies**: Configurable rules for what constitutes actionable drift

## Documentation

- [Design docs](docs/)
- [Governance schemas](schemas/)
- [CLAUDE.md](CLAUDE.md) -- developer context and conventions
- [AGENT.md](AGENT.md) -- LLM navigation guide

## License

[MIT](LICENSE)
