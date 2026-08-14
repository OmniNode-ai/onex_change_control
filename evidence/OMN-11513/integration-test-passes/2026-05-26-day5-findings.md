# Day 5 Integration Test Findings — 2026-05-26

**Umbrella ticket:** OMN-11513
**Branch tested:** `dev` (omnidash), `main` (SEA, omnidash post-release)
**Tester:** Bret Truchan (clone45@gmail.com)
**Evidence label:** `LOCAL_RESEARCH_ONLY` / `LIVE_MODEL_ONLY`

---

## Baseline Capture

### omnidash (dev)

| Field | Value |
|-------|-------|
| Branch | `dev` |
| Commit SHA | `e7c55a3` |
| Previous baseline SHA | `0f26d11` (main, Day 8) |
| Build status | Clean (warnings only — chunk size) |
| Node version | v20.19.3 |
| npm version | 10.8.2 |
| New deps | `kafkajs ^2.2.4` (dispatch bridge, PR #128) |

### omnidash (main, post-release)

| Field | Value |
|-------|-------|
| Branch | `main` |
| Commit SHA | `cc97d4e` |
| Build status | Clean (warnings only — chunk size, eval in third-party dep) |
| Version | omnidash@1.1.0 |

### onex-self-extending-agent (main)

| Field | Value |
|-------|-------|
| Branch | `main` |
| Commit SHA | `aeaf53a` |
| Previous baseline SHA | `24c3f33` |
| Test suite | 1104 passed / 0 failed / 1 skipped (+21 from baseline) |
| `--agent` path | PASS, 1 attempt, 25.4s |

---

## Per-Widget Audit Table

All tests run in file mode (`VITE_DATA_SOURCE=file`) on omnidash dev branch (`e7c55a3`).

| Surface | PR | Mode | Status | Severity | Source Disclosed? | Evidence | Notes |
|---------|-----|------|--------|----------|-------------------|----------|-------|
| Delegation Correlation Trace | #121 | file | DEGRADED | COSMETIC | Yes (error message references postgres) | `07-correlation-trace-tab.png` | Designed for sqlite/http only. In file mode, hits Express bridge and shows JSON parse error. Error is caught inline (no crash) but message is unclean. Source code documents this is intentional (delegation-api.ts:92-98). |
| Delegation Savings Proof Pack | #123 | file | PASS | ARCHITECTURAL_RISK | **NO** — undisclosed in printable section | `05-savings-proof-pack-tab.png` | Parent widget shows "Fixture" badge. But the Proof Pack panel — designed as a standalone printable audit artifact for procurement — has no data source indicator. A PDF export would show fixture data without disclosure. |
| Swarm-runs projection | #124 | file | PASS | N/A | N/A | N/A | Backend-only change. Adds `onex.snapshot.projection.swarm-runs.v1` query to postgres projection reader. No UI widget to test. |
| Trace Explorer | #126 | file | PASS | — | Yes (`isLive=false` via ComponentWrapper) | `04-widgets-no-library.png` | Shows "No traces / Traces appear after log entries are written with correlation IDs". Empty state is correct — no fixture data exists for traces topic. |
| Context Effectiveness Heatmap | #127 | file | BLOCKED | ARCHITECTURAL_RISK | N/A (cannot instantiate) | `02-widget-palette.png` (absent from palette) | Full implementation exists (358 lines) with fixtures, types, hook, and test file. NOT registered in `componentImports` (index.ts), NOT in `component-registry.json`, NOT in `generate-registry.ts` MVP_COMPONENTS. Dead code — cannot be added from widget palette. |
| Command Dispatch UI | #129 | file | PASS | — | N/A (not a data widget) | `06-command-palette.png` | Cmd+K opens palette listing 8 known nodes with type badges. Keyboard nav works. `useDispatch` detects bridge unavailability via HEAD → 404, sets `isAvailable=false`, disables dispatch. No auto-fire. |
| Dispatch topic alignment | #131 | file | PASS | N/A | N/A | N/A | Backend-only change. Aligns topic namespace in `shared/types/command-topics.ts`. No UI changes. |

### Summary

- **Testable UI widgets:** 4 (PRs #121, #123, #126, #129)
- **Backend-only:** 2 (PRs #124, #131)
- **Dead code:** 1 (PR #127)
- **ARCHITECTURAL_RISK:** 2
- **DEMO_BLOCKER:** 0
- **DEMO_DEGRADED:** 0

---

## Projection Connectivity Finding

Two independent systems tested separately.

### Stability-test runtime (<onex-host>:18085)

**Status: HEALTHY**

| Metric | Value |
|--------|-------|
| Version | 0.36.1 |
| Status | healthy (not degraded, not draining) |
| Event bus | healthy, circuit closed |
| Subscribers | 305 |
| Topics | 237 |
| Consumers | 304 |
| Handlers | db and http (both healthy) |
| Handler pool | 4 instances each, all available, 0 in-flight |
| Ingress routes | 1565 |
| Active packages | omnibase_infra, omnimarket, omniclaude, omniintelligence |

Tested via Tailscale at `100.109.203.94:18085`. Probe: `curl -fsS http://100.109.203.94:18085/health`.

### Dashboard projection API (<onex-host-gpu>:3003)

**Status: UNREACHABLE**

`curl -fsS http://<onex-host-gpu>:3003/api/health` timed out after 5 seconds. The Express projection API container is running internally (confirmed in integration plan) but not exposed externally at probe time.

**Impact:** Cannot test any widget against live projection data. File and sqlite modes only. This is consistent with the Day 4 finding. The projection API being unreachable means the B9 proven delegation chain (`correlation_id: 4c270da6-671c-46cd-9744-d48478220924`) cannot be verified through the dashboard's Correlation Trace widget.

---

## Dispatch Bridge Shape Documentation

### Endpoint: `POST /api/dispatch`

- **Source:** `server/routes.ts:316-358`
- **Required fields:** `command_type` (string), `target_node_id` (string), `payload` (object)
- **Behavior without Kafka:** Returns HTTP 503 with body `{ "error": "kafka_unavailable" }`. Checks `isProducerConnected()` before publishing. If producer not connected, request never reaches Kafka.
- **Envelope shape:** `{ request_id (UUID), command_type, target_node_id, payload, requested_by: 'omnidash-ui', requested_at (ISO) }`
- **Publish topic:** `onex.cmd.omnimarket.dispatch-request.v1` (from `COMMAND_TOPICS.dispatchRequest`)
- **Response on success:** `{ request_id, status: 'published', topic, timestamp }`

### Topic Naming — COMPLIANT

| Topic | Source | Pattern |
|-------|--------|---------|
| `onex.cmd.omnimarket.dispatch-request.v1` | `command-topics.ts:10` | `onex.cmd.{service}.{event}.v{N}` |
| `onex.cmd.omnimarket.delegate-skill.v1` | `routes.ts:132` | `onex.cmd.{service}.{event}.v{N}` |

### Command Palette (PR #129)

- **Location:** `CommandPalette.tsx:6-15`
- **Known nodes:** 8 hardcoded entries (node_build_loop, node_log_persistence_effect, node_dispatch_request_handler, node_test_runner, node_projection_traces, node_dispatch_worker, node_intent_classifier, node_evidence_pipeline)
- **Node types:** ORCHESTRATOR (3), EFFECT (2), COMPUTE (2), REDUCER (1)
- **Interaction:** Ctrl/Cmd+K to toggle, arrow keys, Enter to select, Esc to close. 3-stage flow: search → payload editor → result display
- **Auto-fire:** None. `useDispatch` fires a HEAD availability probe only when palette is opened (read-only). `handleDispatch` only invoked via button click.
- **Gap:** KNOWN_NODES is hardcoded — not dynamically fetched from runtime's 1565 registered routes.

### Prohibited action compliance

No POST requests were sent to `/api/dispatch`. All verification was through source inspection and GET/HEAD behavior.

---

## Data Source Mode Matrix

| Widget | file | sqlite | http | Notes |
|--------|------|--------|------|-------|
| Correlation Trace (#121) | DEGRADED (JSON parse error) | Not tested (no bridge) | Not tested (<onex-host-gpu> unreachable) | Designed for sqlite/http only |
| Savings Proof Pack (#123) | PASS (undisclosed fixture) | Not tested | Not tested | ARCHITECTURAL_RISK in print view |
| Swarm-runs (#124) | N/A (backend) | N/A | N/A | — |
| Trace Explorer (#126) | PASS (empty-correct) | Not tested | Not tested | — |
| Context Heatmap (#127) | BLOCKED (unregistered) | BLOCKED | BLOCKED | Dead code |
| Command Dispatch UI (#129) | PASS (empty-correct) | Not tested | Not tested | — |
| Dispatch topic fix (#131) | N/A (backend) | N/A | N/A | — |

SQLite and HTTP modes not tested due to: no Express bridge running (sqlite requires it), projection API unreachable (http requires <onex-host-gpu>:3003).

---

## Screenshots

All saved to `/mnt/c/Code/omninode_ai/docs/projects/hackathon_prep/evidence/2026-05-26-screenshots/`:

| File | Content | Data source | Mode |
|------|---------|-------------|------|
| `00-initial-dashboard.png` | Empty dashboard with Fixture Mode banner | file | fixture |
| `01-dashboard-created.png` | New dashboard created | file | fixture |
| `02-widget-palette.png` | Widget library — 29 widgets, Context Heatmap absent | file | fixture |
| `04-widgets-no-library.png` | Trace Explorer + Delegation Control Plane rendered | file | fixture |
| `05-savings-proof-pack-tab.png` | Savings Proof Pack printable report (undisclosed fixture) | file | fixture |
| `06-command-palette.png` | Cmd+K command palette with 8 nodes | file | fixture |
| `07-correlation-trace-tab.png` | Correlation Trace JSON parse error in file mode | file | fixture |

Capture timestamp: 2026-05-26T14:20Z–14:45Z (approximate).

---

## Console Errors

All expected in file mode:
- `WebSocket connection to 'ws://localhost:3002/ws' failed` — no Express bridge running
- `Failed to load resource: 404` — Vite dev server doesn't serve /api/* endpoints
- No React errors, no widget-specific JS errors

---

## Demo Readiness Assessment

- **What command would you run for a judge?** `uv run python -m src --agent` (with Gemini key) for the self-extending agent. `uv run python presentations/delegation_demo.py` (with OpenRouter key) for cost-aware routing.
- **What mode would you choose?** LIVE for both. Delegation demo and --agent both work reliably.
- **What is most likely to fail live?** `--agent` has a 15% truncation failure rate (OMN-11827). Run it again if it fails.
- **What would you avoid showing a judge?** Dashboard in http mode (projection API unreachable). Correlation Trace in file mode (ugly error). Context Heatmap (dead code, can't instantiate).
- **What fallback would you use?** `--demo` without a key runs REPLAY + FIXTURE pipeline honestly. Dashboard in file mode with Fixture Mode banner is clean.

---

## ARCHITECTURAL_RISK Findings

### Finding 1: Context Effectiveness Heatmap is dead code (PR #127)

**Severity:** ARCHITECTURAL_RISK
**Widget:** `src/components/dashboard/context-heatmap/ContextEffectivenessHeatmap.tsx`
**Evidence:** Full implementation (358 lines) with fixtures, types, hook (`useContextHeatmapData`), and test file. NOT registered in:
- `src/components/dashboard/index.ts` (componentImports) — grep empty
- `src/registry/component-registry.json` — grep empty
- `scripts/generate-registry.ts` (MVP_COMPONENTS) — grep empty

**Impact:** Widget cannot be instantiated from the palette. A judge navigating the dashboard would not encounter it. The implementation exists but is unreachable.

**Suggested fix:** Register in componentImports and MVP_COMPONENTS list.

### Finding 2: Savings Proof Pack fixture data undisclosed in print view (PR #123)

**Severity:** ARCHITECTURAL_RISK
**Widget:** Delegation Savings Proof Pack (tab inside Delegation Control Plane)
**Evidence:** The ProofPack panel renders a printable "OmniNode Delegation Savings Report" with 15 runs, all showing "unknown" task/model and "projected" state. The parent Delegation Control Plane shows a "Fixture" badge, but the ProofPack panel — designed as a standalone printable audit artifact — has no data source indicator.

**Impact:** A user printing this page gets a professional-looking procurement document with no warning that all values are synthetic. If a judge or auditor exports this view to PDF, the output contains no fixture disclosure.

**Suggested fix:** Add a "Data source: Fixture" watermark or header to the print-ready section.

---

## Additional Findings

### OMN-12286: --agent CLI Gemini-only limitation (filed today)

The `--agent` path uses Google's ADK `InMemoryRunner` which requires a Google API key. The generation layer (Layer 3) now reads the model registry (PR #130), but the orchestration layer (Layer 1) is still Gemini-only. Filed as OMN-12286 (Medium, Backlog, DO NOT START without Jonah's approval).

### Dispatch command palette uses hardcoded node list

The CommandPalette component lists 8 nodes in `KNOWN_NODES` — not dynamically discovered from the runtime's 1565 registered routes. Functional but a discoverability gap. Severity: BACKLOG_ONLY.

### omnidash PR #134 (v1.1.0 release) was CLOSED, not merged

The release landed on main via a different mechanism (direct tag at `853d978`). PR #134 shows `mergedAt: null`, `closedAt: 2026-05-26T13:34:11Z`. The release is on main but not through the expected PR path.
