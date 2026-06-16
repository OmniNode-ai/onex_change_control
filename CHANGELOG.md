## v0.5.1

### Added
- feat(promotion): `staleness.py` helper for promotion staleness detection
- feat(testing): wire schema test generator (`wire_schema_test_generator.py`)
- feat(validators): `cross_schema_coherence.py` validator for cross-schema integrity checks

### Changed
- chore: version bump to 0.5.1

## v0.5.0

### Added
- feat(overseer): orchestration model suite — 14 models (`ModelWorkerContract`, `ModelSessionContract`, `ModelOvernightContract`, `ModelDispatchItem`, `ModelEscalationRequest`, `ModelContextBundle`, `ModelCompletionReport`, `ModelVerifierOutput`, `ModelPromotionBotPolicy`, `ModelTaskStateEnvelope`, `ModelTaskDeltaEnvelope`, `ModelTaskShapeFeatures`, `ModelProcessRunnerStateTransition`, `ModelContractAllowedActions`) and 14 enums (`EnumArtifactStoreAction`, `EnumCapabilityTier`, `EnumCodeRepositoryAction`, `EnumContextBundleLevel`, `EnumEventBusAction`, `EnumFailureClass`, `EnumLlmProviderAction`, `EnumNotificationAction`, `EnumProcessRunnerState`, `EnumProvider`, `EnumRetryType`, `EnumRiskLevel`, `EnumTicketServiceAction`, `EnumVerifierVerdict`) for worker/session/dispatch orchestration
- feat(promotion): promotion tooling module — `manifest.py` (`generate-promotion-manifest` CLI), `workflow.py` (`promotion-workflow-evidence` CLI), `cutover.py` (`dev-main-cutover` CLI) for automated dev→main promotion workflows
- feat(scanners): additional scanner implementations — `claude_md_cross_ref.py`, `claude_md_update_suggester.py`, `historical_docs_validator.py`, `model_dump_drift.py`; complementing the v0.3.0 doc-freshness and handler-compliance scanners
- feat(handlers): `handler_dependency_analysis.py` and `handler_drift_analysis.py` alongside the existing `handler_dod_sweep.py`
- feat(kafka): `governance_emitter.py` and `topics.py` for governance event emission
- feat(boundaries): Kafka boundary and DB routing rule YAML configs; `migration_inventory.yaml` added
- feat(canary): canary schema module (`schema.py`)
- feat(dispatch_claims): dispatch claim store (`claim_store.py`) and sweeper (`sweeper.py`)
- feat(doctrine): doctrine loader (`loader.py`) as the authoritative policy configuration surface
- feat(validators): `arch_handler_contract_compliance.py` validator
- feat(wire_schemas): `runtime_deployment_proof_v1.yaml` and `runtime_deployment_request_v1.yaml` wire schema definitions
- feat(scripts): additional CLI scripts — `check_db_routing`, `check_diagnosis_doc_freshness`, `check_integration_map_freshness`, `check_plan_vs_live`, `check_pr_touches_ticket_files`, `validate_pr_contracts` (registered as internal helpers, not all exposed as entry points)

### Changed
- chore: version bump to 0.5.0

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
