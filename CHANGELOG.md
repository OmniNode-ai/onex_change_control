## v0.4.0 (2026-03-31)

### Added
- feat(ci): add reusable PR title check workflow [OMN-6913] (#128)

### Fixed
- fix(ci): add continue-on-error to auto-merge step [OMN-6489] (#126)

## v0.3.0 (2026-03-28)

### Added
- feat: permanent version skew prevention [OMN-6692] (#125)
- feat(ci): add auto-merge-on-open workflow [OMN-6571] (#124)
- feat: generate compliance allowlists for 4 repos [OMN-6840] (#123)
- feat: handler contract compliance scanner and models (#122)
- feat: eval A/B framework models, enums, and comparator [OMN-6770-6778] (#121)
- feat: add doc freshness scanner models, enums, and modules (#120)

### Changed
- chore(deps): pin omnibase-core==0.34.0

### Dependencies
- omnibase-core >=0.30.2 -> ==0.34.0

## v0.2.0 (2026-03-27)

### Added
- feat: add Python<>TypeScript null contract test + node introspection boundaries [OMN-6405] (#113)
- feat: add check-drift CLI entry point [OMN-6574] (#115)
- feat: add pending status with 14-day grace period for cross-repo topics [F17] (#114)
- feat: add pending status for boundary topics [OMN-6463] (#111)
- feat: pre-surgery pipeline hardening Plan B [OMN-6417] (#107)
- feat(enums): add WIRING_VERIFICATION to EnumIntegrationSurface [OMN-6426] (#106)
- feat: register contract drift event topic in boundary manifest [OMN-6386]
- feat: add ORCHESTRATOR node (topology stub) for contract drift pipeline [OMN-6385]
- feat: add EFFECT node for contract drift event emission [OMN-6384]
- feat: add REDUCER node for contract drift history accumulation [OMN-6383]

### Fixed
- fix(types): narrow Any to concrete types in 3 files [OMN-6683] (#118)
- fix(tests): add ticket references to blocked skip reasons [OMN-6689] (#116)
- fix(boundaries): add pending status with 14-day grace period for cross-repo topics [F17] (#114)
- fix(ci): symlink calling repo into boundary parity workspace [OMN-6462] (#110)
- fix(ci): promote migration-conflicts CI to hard-fail [OMN-6438] (#108)

### Changed
- chore: exempt test fixture TODO from format checker [OMN-6655] (#117)
- chore: fix mypy attr-defined in hash divergence test [OMN-6388]
- chore: fix unused type-ignore in drift compute node [OMN-6388]
- chore(deps): bump the actions group with 2 updates (#109)

## v0.1.2 (2026-03-24)

- Previous release
