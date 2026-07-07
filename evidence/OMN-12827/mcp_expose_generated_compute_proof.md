# OMN-12827 — MCP exposure of generated COMPUTE node (close-the-loop Plan B2)

Repo under change: `OmniNode-ai/omnibase_infra`
Plan: `omni_home/docs/plans/2026-06-08-close-the-loop-plan.md` (§Phase B, B2).
Captured: 2026-06-08. Adversarial receipt: verifier is the B2 implementation agent
acting as an independent re-runner of the runner's claim, against the worktree HEAD.
This is a **code-path / unit-proof** receipt, not a live-runtime receipt — B2 is the
infra-side classification change only; live MCP-tool-appears proof is Phase D (D1/D2).

## Change summary (the claim under verification)

`ServiceMCPToolSync` previously exposed an MCP tool only for events tagged
`mcp-enabled` + `node-type:orchestrator` (`_is_mcp_orchestrator`). Generated COMPUTE
nodes (the SEA self-extension loop output) could never surface as MCP tools.

B2 relaxes the rule via a renamed predicate `_is_mcp_exposable(tags)`:

- `mcp-enabled` + `node-type:orchestrator` → exposed (unchanged).
- `mcp-enabled` + `node-type:compute` + `generated` → exposed (NEW).
- `mcp-enabled` + `node-type:compute` WITHOUT `generated` → NOT exposed.
- missing `mcp-enabled` → NOT exposed (any type).

The relaxation does NOT relabel a generated compute node as an orchestrator: the tool
description and `metadata.node_kind` distinguish "generated compute node" from
"orchestrator", and only `generated` compute nodes surface (hand-authored compute nodes
do not leak into the MCP registry).

Files changed in omnibase_infra (branch `jonah/mcp-expose-generated-compute`,
base SHA `5ffe2978911ab17d2b7e5446299c77688e04ae0d`):

- `src/omnibase_infra/services/mcp/service_mcp_tool_sync.py` — predicate relaxed +
  type-aware tool description / `node_kind` metadata; added `TAG_NODE_TYPE_COMPUTE`,
  `TAG_GENERATED` constants.
- `tests/unit/services/mcp/test_service_mcp_tool_sync_exposure.py` — new TDD unit tests.
- `contracts/OMN-12827.yaml` — evidence contract.

## Verdict

PASS (code-path proof). The relaxed exposure predicate exposes generated compute nodes
and orchestrators, and excludes non-generated compute and non-mcp nodes. TDD red→green
was observed (tests failed pre-implementation with `AttributeError: ... has no attribute
'_is_mcp_exposable'`, then passed post-implementation). Full omnibase_infra unit suite is
green except one pre-existing, environment-induced failure unrelated to this change
(migration vendor-sync; see below), which also fails on pristine `main` HEAD.

## Probe — TDD RED (before implementation)

Command (in omnibase_infra worktree):
`uv run pytest tests/unit/services/mcp/test_service_mcp_tool_sync_exposure.py -v`

```
E   AttributeError: type object 'ServiceMCPToolSync' has no attribute 'TAG_NODE_TYPE_COMPUTE'
E   AttributeError: 'ServiceMCPToolSync' object has no attribute '_is_mcp_exposable'
...
6 failed in 0.67s
```

## Probe — TDD GREEN (after implementation)

Command: `uv run pytest tests/unit/services/mcp/test_service_mcp_tool_sync_exposure.py -v`

```
tests/unit/services/mcp/test_service_mcp_tool_sync_exposure.py::TestMcpExposureRule::test_mcp_enabled_orchestrator_is_exposed PASSED
tests/unit/services/mcp/test_service_mcp_tool_sync_exposure.py::TestMcpExposureRule::test_mcp_enabled_generated_compute_is_exposed PASSED
tests/unit/services/mcp/test_service_mcp_tool_sync_exposure.py::TestMcpExposureRule::test_non_generated_compute_is_not_exposed PASSED
tests/unit/services/mcp/test_service_mcp_tool_sync_exposure.py::TestMcpExposureRule::test_non_mcp_node_is_not_exposed PASSED
tests/unit/services/mcp/test_service_mcp_tool_sync_exposure.py::TestMcpExposureRule::test_generated_compute_without_mcp_enabled_is_not_exposed PASSED
tests/unit/services/mcp/test_service_mcp_tool_sync_exposure.py::TestMcpExposureRule::test_empty_tags_is_not_exposed PASSED

6 passed in 0.17s
```

## Probe — MCP unit + integration sweep (no regression)

Command: `uv run pytest tests/unit/services/mcp/ tests/integration/services/mcp/ -q`

```
132 passed, 25 skipped in 4.60s
```

(The 25 skips are Kafka-gated integration tests; `<onex-host>` Kafka not reachable
from the local CI sandbox — they skip by design, not failure.)

## Probe — type check + lint

```
$ uv run mypy src/omnibase_infra/services/mcp/service_mcp_tool_sync.py --strict
Success: no issues found in 1 source file

$ uv run ruff check src/ tests/      # (changed files)
All checks passed!
```

## Pre-existing unrelated failure (disclosed, not introduced)

Full `tests/unit/` run: `1 failed, 20160 passed, 15 skipped`. The single failure is
`tests/unit/migrations/test_node_migration_discovery.py::TestVendoredTreeMatchesSource::test_sync_check_reports_in_sync`.

This failure is environmental and orthogonal to B2: the test runs
`scripts/sync-node-migrations.sh --check`, which detects that the shared local
`omnimarket` canonical clone carries uncommitted migration files
(`node_projection_delegation/0012_*.sql`, `0013_*.sql` — sibling A3 work) that the
omnibase_infra vendored tree has not yet picked up. It reproduces on **pristine
`main` HEAD with no B2 changes applied** (verified independently), and B2 touches no
migration files. In CI, omnimarket is resolved at a clean pinned version so the
vendored tree matches and this test passes.

## Honesty metadata

- runner: omni B2 implementation agent (Claude Code subagent), local CI sandbox.
- verifier: same agent re-running probes independently against worktree HEAD —
  NOT the user; `verifier != jonah`.
- All probe_stdout above is real captured output, not echoed/asserted PASS.
- No PENDING marked PASS. The live-runtime "MCP tool appears" assertion remains PENDING
  and is explicitly deferred to Phase D — it is NOT claimed PASS here.
