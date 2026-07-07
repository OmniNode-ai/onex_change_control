# Integration Findings — 2026-05-29

**Status:** IN PROGRESS. Captured through Group A of the dashboard visual audit. Group B, Task 5, and Task 7 are pending or blocked (see status table). This document is being written incrementally so the information survives a context compaction.

**Naming note:** the task plan calls this "Day 7" and the deliverable filename follows that convention, but the number refers to the task-plan's own count, not the actual session count. The unambiguous anchor is the date, 2026-05-29.

**Umbrella ticket:** OMN-11513
**Tester:** Bret Truchan (clone45@gmail.com)
**Branches / SHAs tested:**
- omnidash `dev` at `e10c825`
- onex-self-extending-agent `main` at `c04a4d8`, `dev` at `48aeed9`
**Data source mode:** `VITE_DATA_SOURCE=file` only. Both projection paths (`<onex-host-gpu>:3003` and `localhost:3003`) are down today, so projection mode could not be exercised.

---

## Status at a glance

| Task | Description | Status |
|---|---|---|
| 1 | Re-baseline repos | DONE |
| 2 | Re-test Day 6 fixes | DONE (code-verified all 6; visual Group A confirmed all 4 widget-surfaces) |
| 3 | Dashboard delegation surface audit | DONE for file mode (Group A and Group B assessed; #152 is backend-only; projection mode unavailable) |
| 4 | Projection and runtime read-only probes | DONE |
| 5 | Golden-chain evidence classification | BLOCKED (bundle in omni_home, no access) |
| 6 | SEA authority verification | DONE (clean) |
| 7 | Judge-readiness rehearsal | PENDING (blocked on Group B and Task 5) |
| Deliverable | This findings doc + evidence manifest | IN PROGRESS (manifest still to write; delivery blocked by OCC push access) |

---

## Task 1 — Re-baseline

| Field | Value |
|---|---|
| omnidash branch / SHA | `dev` / `e10c825` (10 commits ahead of yesterday's `680339d`) |
| omnidash build | Clean (chunk-size and TraceExplorer dual-import warnings only, both pre-existing) |
| omnidash new deps | None since `680339d` |
| SEA main SHA | `c04a4d8` (1 commit since yesterday: a CI main-target guard) |
| SEA dev SHA | `48aeed9` |
| SEA unit tests (main) | 1104 passed / 1 skipped / 0 failed, identical to yesterday |

The 10 omnidash commits include the six Day 6 fix PRs (#156 to #161) plus housekeeping (#162 to #165). No regressions in baseline.

## Task 2 — Re-test Day 6 fixes

Two passes: a code-level verification (background agent) and a visual pass (Group A). Full visual detail is in `docs/projects/hackathon_prep/progress_reports/2026-05-29-group-a-visual-findings.md`.

**Code verification (omnidash dev `e10c825`):** all six fixes PRESENT, each matching its commit claim (OMN-12366 tab indicator, OMN-12367 graceful file-mode trace, OMN-12397 dynamic source line, OMN-12399 heatmap registration, OMN-12402 CommandPalette contrast, OMN-12139 trace fixture).

**Visual confirmation (file mode):**

| Surface | Ticket | Result | Disclosure |
|---|---|---|---|
| Correlation Trace tab | OMN-12367 | Renders with fixture data (was a raw JSON.parse error) | "File Mode" badge |
| Savings Proof Pack tab | OMN-12397 | Source line now dynamic and honest by mode | "File Mode" badge |
| Trace Explorer | OMN-12139 | Trace list renders from fixture (was empty) | "File Mode" shown |
| Context Effectiveness Heatmap | OMN-12399 | In palette and renders (was unregistered) | "File Mode" (also in description) |

All six 2026-05-28 tickets are confirmed fixed. Data source disclosure is honest on every surface tested. Six new minor observations were captured (see Findings Register). The honest characterization: the fixes landed correctly, but the fixture content behind several surfaces is thin, so surfaces render honestly without demonstrating much in file mode.

## Task 3 — Dashboard delegation surface audit

Group A (the four fixed widget-surfaces above) is complete. Group B, the five new demo surfaces from PRs #151 to #155, was assessed in file mode:

- **#151 Delegation trigger: PASS.** Renders in the Delegation Control Plane run header, discloses "File Mode", sits idle with no auto-fire, and the dispatch button activates only on prompt input. Not fired (prohibited action). Minor observation (Obs 8): the button activates in file mode even though no backend bridge is reachable, while the Control Plane page version of this panel is gated to live mode only.
- **#152 Short-topic alias: not testable in file mode.** Backend projection-reader change (postgres and sqlite readers); no frontend code surface. Would require projection mode, which is unavailable today.
- **#153 Mode banners: confirmed indirectly.** Every surface tested across Group A and Group B disclosed "File Mode." No undisclosed surface was observed.
- **#154 Trace deep-link: DEMO_DEGRADED (Obs 9).** The deep-link control sets the shared-store `traceFilter`, but the Trace Explorer consumes it only on mount, and omnidash has no client-side router or standalone trace route (TraceExplorer exists only as a dashboard widget). Clicking the deep-link does not update an already-placed Trace Explorer. Workaround: type the correlation_id into the Trace Explorer search field.
- **#155 Cost proof widget: renders but empty (Obs 7).** Shows "No cost comparison data" in file mode because no fixture exists for its topic `onex.snapshot.projection.delegation.savings.v1`. Empty-correct, not broken.

Projection-mode testing of any surface remains impossible today (no projection bridge reachable, see Task 4).

## Task 4 — Projection and runtime read-only probes

| Endpoint | Expected interpretation | Result |
|---|---|---|
| `<onex-host>:18085/v1/introspection/manifest` (via Tailscale `100.109.203.94`) | Runtime manifest | Alive. 245 contracts registered, 0 errors. omnimarket 0.4.2. 8 delegation-related nodes present (delegate-skill orchestrator, delegation orchestrator, quality-gate reducer, routing-feedback reducer, routing reducer, llm-delegation projection, llm-delegation routing compute, projection_delegation). |
| `localhost:3003/projection/delegation` | Local omnidash bridge | Connection refused. Bridge not running. |
| `<onex-host-gpu>:3003/projection/delegation` | Per today's plan, not the authoritative bridge | Connection timed out. Unreachable from WSL, as expected. |

Net: file mode is the only available data path today. The runtime itself is healthy and richly populated.

Side observation: '`<onex-host>`' /health reports version 0.37.0 today versus 0.37.2 yesterday. Logged as observed runtime identity drift per Jonah's guidance, not as an intentional rollback.

## Task 5 — Golden-chain evidence classification

BLOCKED. The evidence bundle (`docs/evidence/delegation-golden-chain-proof-2026-05-28/`, 19 files) is on the `OmniNode-ai/omni_home` repo, which this account cannot access (GitHub returns 404 for the repo and for the cited commit `1a71567b1`). The bundle could not be read, so the per-artifact classification table cannot be filled in. Documented as a blocker for Jonah, with a request to grant access or mirror the bundle. See `docs/projects/hackathon_prep/discussions/2026-05-29-jonah-blockers-batch2.md`.

Known target run for classification once the bundle is reachable: correlation_id `bda0b379-3b0a-47f6-b398-d682424bff19`. The plan also references a second, partial correlation_id `d9884619-85ad-4b1e-8342-1fbdabaaa4fb` (terminated at inference with InfraAuthenticationError).

## Task 6 — SEA authority verification

CLEAN. SEA is no longer a second dashboard or routing authority on dev `48aeed9`.

- `#157`: `src/dashboard_server.py` is retired (commit `73b373e` deleted the 1131-line FastAPI server, the WebSocket display stub, and associated tests). No live `FastAPI()`, no route decorators, and no `uvicorn` call sites exist in `src/`.
- `#160`: SEA commands route through the ONEX CLI via the `onex.cli` entry point (`sea = src.cli.cli_sea:sea`). The legacy `python -m src` path resolves to the same functions, so there is no duplicate execution authority.

Two minor observations:
1. `presentations/demo-dashboard-startup.md` still contains a `uvicorn src.dashboard_server:app` invocation referencing the now-deleted file. A presenter following it verbatim would hit a ModuleNotFoundError. Stale runbook for a completed demo. Proposed severity COSMETIC. (Exact line to be re-verified before any ticket.)
2. Three `[project.scripts]` entry points (`onex-demo`, `onex-entropy-demo`, `onex-build-adapter`) bypass the `onex sea` routing layer. They call the same functions, so not duplicate authority, but they are undocumented bypass paths. Observation only, depends on whether "all SEA commands route through onex sea" is an intended invariant.

## Task 7 — Judge-readiness rehearsal

PENDING. Blocked on Group B (Task 3) and on the golden-chain bundle (Task 5). A preliminary Demo Readiness Assessment based on what is known so far appears at the end of this document.

---

## Findings register (severity-ordered)

### ARCHITECTURAL_RISK

**OMN-12434 (FILED, High) — SEA delegation registry model IDs do not match served vLLM IDs.**
The registry declares `Qwen/Qwen3.6-35B-A3B-Instruct` on port 8000, but the vLLM server serves the short name `Qwen3.6-35B-A3B`. Every tier-1 request returns HTTP 404, and the escalation ladder silently advances to tier 2 (port 8001, llamacpp, which is permissive). The cheapest tier never serves. The 7-task regression reports 7/7 PASS, which masks this because escalation absorbs the tier-1 failure. Verified first-hand (direct 404 on tier 1, direct 200 on tier 2). Full detail and suggested remediation in the ticket.

### DEMO_DEGRADED (candidates, not yet filed)

- **Obs 1: Savings Proof Pack renders with empty value columns in file mode.** Only `correlation_id` is populated; task, model, tokens, cost, and savings are blank. The proof pack cannot demonstrate savings in file mode. Likely fixture-shape mismatch. Related: OMN-12287.
- **Obs 3: Trace Explorer selected trace shows "No events for this trace."** The trace list renders, but selecting a trace shows an empty detail panel. Graceful, not broken, but the fixture provides trace groups without their events. Related: OMN-12139, OMN-12287.
- **Obs 7: Delegation Cost Comparison widget (#155) renders empty in file mode.** Shows "No cost comparison data" because no fixture exists for its topic `onex.snapshot.projection.delegation.savings.v1`. Empty-correct, not broken. The headline cost surface cannot demonstrate savings in file mode. Related: OMN-12287.
- **Obs 9: Trace deep-link (#154 / OMN-12288) does not update a co-resident Trace Explorer.** The deep-link control sets the shared-store `traceFilter`, but the Trace Explorer consumes it only on mount, and omnidash has no client-side router or standalone trace route, so an already-placed Trace Explorer never reacts. Act 4 (click run, land on trace) cannot be shown as a click interaction. Workaround: type the correlation_id into the Trace Explorer search field. Related: OMN-12288.

### COSMETIC (candidates, not yet filed)

- **Obs 2: Savings Proof Pack correlation ID wraps** to a second line despite a horizontal scrollbar.
- **Obs 4: Context Heatmap widget-library description is developer jargon** (segment x model matrix, context_experiment_scores ablation projection OMN-12082, OMN-11241 research fixture), not user-facing. It does honestly state it renders a research fixture until the projection is live.
- **Obs 5: Context Heatmap fixture index 404s** in the console (`/_fixtures/onex.snapshot.projection.context.experiment-scores.v1/index.json`). Widget renders via an embedded fallback fixture, so console noise rather than breakage. Related: OMN-12287.
- **Obs 6: Context Heatmap cell detail renders below the fold.** Clicking a cell works, but the detail appears at the bottom of a tall grid, often out of view. Suggested fix: scroll detail into view on click.
- **Obs 8: Delegation trigger button activates in file mode** even though no backend bridge is reachable there, while the Control Plane page version of the same panel is gated to live mode only. Minor consistency question about presenting an active trigger in a mode where it cannot dispatch.
- **Task 6 stale runbook** (see Task 6, observation 1).

### Observation only

- **Task 6 bypass CLI entry points** (see Task 6, observation 2).

Proposed handling for the Group A candidates: bundle into two tickets, one fixture-content gap (Obs 1, 3, 5; related to OMN-12287) and one cosmetic polish (Obs 2, 4, 6). Re-verify exact strings and paths before filing.

## Verification wins (not findings)

- **Delegation regression resolved for the WSL environment.** After adding the sanctioned local overlay (`model_registry.local.yaml`) to point the local tiers at the Tailscale IP, the 7-task regression runs 7/7 PASS, where every prior run returned all failures (tier empty, attempts 0) due to the unreachable LAN IP. Note: all 7 currently resolve at tier 2 because of the OMN-12434 tier-1 drift.
- **`--progressive` runs clean.** 6/6 PASS on dev `48aeed9`, total spend 0.0029 USD (local model track skipped due to the same tier-1 drift; only cloud_gemini executed).
- **SEA #160, #161, #149 verified** (CLI routing, adapter builder present, demo defaults removed).

## Blockers

1. **omni_home access** — blocks Task 5 (golden-chain classification). Bundle is real and on omni_home main; this account cannot read that repo.
2. **OCC push access — RESOLVED 2026-05-29.** Write access was granted (the account now has `push: true`). The Day 5 and Day 7 findings and manifests have been committed and pushed to branch `clone45/omn-11513-integration-evidence-days-1-7` on origin.

The remaining omni_home blocker is documented for Jonah in `docs/projects/hackathon_prep/discussions/2026-05-29-jonah-blockers-batch2.md`.

## Severity tally (provisional, through Group B)

| Severity | Count | Items |
|---|---|---|
| DEMO_BLOCKER | 0 | |
| DEMO_DEGRADED | 4 | Obs 1, 3, 7, 9 (candidates) |
| ARCHITECTURAL_RISK | 1 | OMN-12434 (filed) |
| COSMETIC | 6 | Obs 2, 4, 5, 6, 8, stale runbook (candidates) |
| BACKLOG_ONLY | 0 | |
| Observation only | 1 | bypass CLI entry points |

## Demo Readiness Assessment (preliminary)

- **What mode would you show today?** File mode only. No projection bridge is reachable.
- **What is the strongest honest delegation proof visible in omnidash today?** The delegation surfaces render honestly with disclosed fixture data, but the fixtures are thin. The proven golden chain (`bda0b379...`) exists on stability-test but cannot be displayed in the dashboard today because the projection path is unavailable.
- **What is most likely to fail or mislead live?** Two things. First, a cost-savings claim: per OMN-12434 the cheapest tier is offline, and in file mode both the Savings Proof Pack (empty columns, Obs 1) and the Cost Comparison widget (no fixture, Obs 7) show no figure. Second, Act 4: the trace deep-link click does nothing visible in a co-resident dashboard (Obs 9).
- **What should not be shown to a judge as a live click interaction?** The trace deep-link (Act 4) does not update a co-resident Trace Explorer. If shown, use the workaround of typing the correlation_id into the Trace Explorer search, and do not imply the click drives the trace. Also avoid the Savings Proof Pack in file mode (empty columns).
- **What fallback is acceptable without overstating?** File mode with the "File Mode" disclosure visible on each surface is honest about what it is.
- **Can the dashboard show cost savings today?** Not in file mode: projection mode is unavailable and the file-mode surfaces are empty. Important framing: this is a file-mode and projection-availability limitation, not evidence that the platform cannot compute or route savings. In a live or projection-backed demo, with the projection populated and OMN-12434 resolved, these surfaces are designed to display real figures. The file-mode gap is worth fixing but should be read in that proportional light, especially if a live demo is the intended path.
- **Does any SEA surface still duplicate omnidash or runtime authority?** No (Task 6 clean).
