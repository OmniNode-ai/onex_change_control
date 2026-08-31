# OMN-11513 Evidence Manifest

**Ticket:** OMN-11513 (May Monthly Integration Testing Plan)
**Engagement:** OmniNode Hackathon Integration — contractor Bret (clone45@gmail.com)
**Period covered:** 2026-05-19 through 2026-06-07
**Last updated:** 2026-06-07

---

## Artifacts in this directory (current-state, ported from engagement workspace)

### integration-test-passes/

| File | Date | Description | Status |
|---|---|---|---|
| `2026-05-21-first-integration-milestone.md` | Day 3 + Day 5 addendum | SOW §3.2 baseline report. Covers all 8 required line items + closing rubric (Top 3 blockers, Top 3 fixes, demo mode rubric, "what surprised you most?"). Day-5 addendum updates blockers + records full-chain achievement. | **Current** |
| `2026-05-24-integration-pass-report.md` | Day 7 | Fresh integration pass per Jonah's May 24 integration prompt plan. Covers A0-A3 + B + C + D workstreams on current main `62b87c2`. Most comprehensive single-session report of the engagement. | **Current** |
| `2026-05-26-day5-findings.md` | 2026-05-26 | Day-5 follow-on findings (previously merged; manifest row added 2026-06-04). | **Current** |
| `2026-05-29-bret-day7-findings.md` + `2026-05-29-bret-day7-evidence-manifest.json` | 2026-05-29 | Day-7 findings + typed evidence manifest (previously merged; manifest row added 2026-06-04). | **Current** |
| `2026-05-30-bret-obj1-token-registry-stability.md` | 2026-05-30 | Objective 1 evidence: SEA token + model-registry stability. SEA dev `13127a7`; registry blob `ca392b6f`; remote-live (Tailscale) + local. Authored 2026-05-30, ported to OCC 2026-06-04. | **Current** |
| `2026-06-05-handoff-reproduction/` | 2026-06-05 | Reproduction of the 2026-06-05 handoff proofs from the contractor seat (SEA dev `c553ceb`). SEA `--agent` e2e: **PASS, classified PROOF** per the handoff criteria (bus_backed, stability overlay, no fallback, correlation `affbaf27-…`, generated `status_health_checker` validated/registered/invoked → `{"is_healthy": true}`). Keyless variant: typed credential error (diagnostic — key still required; contract ADK backend selects cloud Gemini). InferenceClient smoke: blocked unpatched (`MaxContextTokensResolutionError` — omnimarket PR #1054 is the gate), then **clean pass under a disclosed temporary local mirror of the runtime-host routing-tier hotpatch** (diagnostic, never proof; correlation `93c2ade4-…`; patch applied/verified/reverted same hour). **PM update: the smoke now passes UNPATCHED** after the released omnimarket package picked up the routing change (resolver verified failing at 11:00 PT post-revert, passing at 14:58 PT after `uv sync`; correlation `e5a9ecbb-…`, `healthy=true`, 24576 context) — both handoff proofs reproduced. Companion folder `2026-06-05-cloud-tier-retest/`: now four forced-escalation runs. #1–#2 post-merge (2026-06-05 20:45Z/21:30Z, correlations `eb671856-…`, `489f8b3b-…`): 404 persists, runtime not yet redeployed. #3 (2026-06-06): DISCARDED — broker outage/transport, no conclusion. #4 (2026-06-07 16:18Z, correlation `1fbfd700-…`): **post-deploy verification FAILED** — runtime observed at v0.38.3 (healthy, via `:18085/health` at test time) and the identical two-URL 404 persists. Trailing whitespace note: log appended across days; trimmed at port time. Cost-observability note: the PROOF run reports $0.0000 despite cloud Gemini serving via ADK. | **Current — first PROOF-classified contractor run** |
| `2026-06-04-judge-dryrun/` | 2026-06-04 | Judge-experience dry run: fresh anonymous clone of the public repo (default branch `dev` @ `106e88c`), no env vars, README four-command judge flow. PASS: clone, `uv sync`, unit suite (1376 passed/1 skipped), keyless `--demo` (replay/fixture), `--replay`. FAIL: keyed `--agent` (`SeaKafkaReadinessError`, ~30s hang, default overlay bootstrap `localhost:19092` unreachable — fail-closed #206 gating the judge path) and native entropy demo (`DelegationBusRuntimeNotWiredError`, 0 tracks). Artifacts: dryrun.log, agent-keyed.log, demo-keyless.log, entropy-native.log, clone-info.txt. | **Current — judge-facing README/architecture divergence evidence** |

### proof-of-life/

| Directory | Date | Description | Status |
|---|---|---|---|
| `2026-05-24-62b87c2-live-a1/` | Day 7 | Event chain from first successful `--agent` full chain on UNMODIFIED main. Correlation `7d997a95-...`. No patches. | **Current — canonical LIVE evidence** |
| `2026-05-24-0b2a477-live-full-chain/` | Day 5/6 | Event chain from first-ever full 5/5 chain (with 3 temporary patches, all reverted). Correlation `c5deda45-...`. | **Historical — superseded by Day-7 unpatched run** |
| `2026-05-22-0b2a477-replay-p2-replay/` | Day 4 | Replay-mode proof: validate → register → invoke on golden fixture. 5/5 honesty checks pass. No LLM call. | **Current — canonical REPLAY evidence** |
| `2026-06-04-93adf56-live-agent-pass/` | 2026-06-04 | First full sanctioned-live `--agent` PASS from the contractor environment: generate → validate → register → invoke (`SentimentClassifier`), stability lane, `transport=bus_backed` (#206 readiness artifact). Generation served at ladder tier 1 (local Qwen, SEA #205) — cloud tier not attempted. Correlation `d76664cb-…`. Classified **diagnostic** per packet (missing: runtime identity, topic offsets, projection state). | **Current — first full sanctioned-live PASS (diagnostic)** |
| `2026-06-04-93adf56-live-agent-forced-escalation/` | 2026-06-04 | Forced-escalation run (local tiers made unreachable via documented seams) to exercise the cloud tier post-OMN-12664-closure. Escalated to `cloud_gemini`; runtime round-trip live; failed with the **same two-URL 404 signature as 2026-06-03**: registered `…/v1beta/openai/chat/completions`, called `…/v1/chat/completions`. Typed `SeaGenerationError`. Correlations `fa8daa9e-…` / `aab65120-…`. This seat cannot distinguish fix-not-deployed from fix-not-on-live-path (see packet Interpretation boundary). | **Current — OMN-12664 post-closure observation (diagnostic)** |

### sea-demo-acceptance/

| File | Date | Description | Status |
|---|---|---|---|
| `2026-05-22-wave5-evidence-report.md` | Day 4 + Day 5 addendum on Linear | Wave 5 SEA Demo + Security Acceptance evidence. 5/5 security-negative tests, LIVE vs REPLAY proof matrix (11/12 cells → 12/12 with Day-5 addendum), CLI provenance audit (0/20 labels). | **Current (Day-5 addendum posted as Linear comment on OMN-11513, not yet merged into this file)** |

### demo-risk-reports/

Currently empty in OCC. Demo risk findings are tracked via Linear tickets (OMN-11694, OMN-11695, OMN-11696, OMN-11827, OMN-11828, OMN-11972) + the integration reports above. The standalone demo-risk report from Day 2 (`docs/projects/hackathon_prep/demo-risk-reports/2026-05-20-bridge-widget-mismatch.md`) is historical — the bridge-widget mismatch was fixed Day 3 (PR #101, verified).

### daily-updates/

Not ported to OCC. Daily standups live in the engagement workspace at `docs/projects/hackathon_prep/progress_reports/` and are posted to Slack `#standups` per engagement doctrine. They are process artifacts, not typed evidence.

### repros/

| Directory | Date | Description | Status |
|---|---|---|---|
| `OMN-12587-asyncio/` | 2026-05-31 | Minimal repro script for the F1 asyncio publish-path failure (`publish_and_wait` inside a running event loop), referenced from the OMN-12587 verification thread. Fixed by SEA #203; retained as regression reference. | **Historical — fix verified** |

---

## 2026-06-04 addendum (post-Day-7 status notes)

- **OMN-12664** (runtime drops Gemini `/v1beta/openai` path) moved Done 2026-06-04 09:26 UTC (omnimarket#1031; infra#1859 reverted by #1863). Same-day forced-escalation run from the contractor seat reproduced the two-URL 404 signature post-closure — see `proof-of-life/2026-06-04-93adf56-live-agent-forced-escalation/`. Observation only; deploy-state vs live-path ambiguity is documented in the packet.
- **OMN-12665** (silent direct-mode downgrade): SEA #206 merged — sanctioned-live now fails closed on unprovable Kafka readiness and writes durable readiness artifacts (`final_transport_mode`). Both 2026-06-04 runs carry these artifacts.
- **SEA #205** wired `--agent` scaffold generation to the escalate-on-failure ladder from `src/contracts/model_registry.yaml` (local-first; cloud only on local-tier failure). Consequence for verification: an unforced `--agent` run does not exercise the cloud/runtime Gemini path when a local endpoint is healthy.
- Evidence gap noted for SEA: executor delegation events (attempt/escalation/completed) are dropped when `scaffold_onex_node` raises `SeaGenerationError` — no durable per-tier escalation evidence on failure paths.

---

## Local artifacts NOT ported (engagement-process docs, remain in workspace)

| Category | Location | Notes |
|---|---|---|
| Daily standups | `docs/projects/hackathon_prep/progress_reports/2026-05-{19,20,21,22,24}-update.md` | Posted to Slack; process cadence docs |
| Morning findings (working snapshots) | `progress_reports/2026-05-{21,22}-morning-findings.md` + `2026-05-24-integration-pass-findings.md` | Accumulated-during-day working docs; superseded by final reports |
| Walkthroughs (narrative companions) | `work_walkthroughs/2026-05-{20,21,22,24}-day{2,3,4,6}-walkthrough.md` | Plain-English narratives for Bret's mental model; not typed evidence |
| Team status reports | `team/{clover,cypress,hawthorn}/status_reports/` | Per-teammate task execution artifacts; findings rolled into final reports |
| Source-code sweep | `widget-audit/source-sweep-2026-05-21.md` | Day-3 26-widget analysis; findings filed as tickets (OMN-11286 territory) |
| Historical proof-of-life bundles (Day 4 patched runs) | `evidence/proof-of-life/2026-05-22-0b2a477-live-p1-{patched,twopatch,twopatch-r2,twopatch-r3,newflash-r1}/` | Superseded by Day-7 unpatched full-chain run |
| Day-6 P2 remaining modes | `evidence/proof-of-life/2026-05-24-p2-remaining/` | Progressive/demo/entropy stdout captures; findings in Day-7 report |

---

## Findings status summary (as of Day 7, 2026-05-24)

### Resolved (code fixes verified)

| Ticket | Finding | Fixed by | Verified |
|---|---|---|---|
| OMN-11691 | Model name hardcoded as `gemini-2.0-flash` (now 404) | PR #127 (→ `gemini-2.5-flash`) | Day 7 A1: 4 runs without model patches |
| OMN-11429 | Crashing widget locks dashboard via localStorage | PR #118 (WidgetErrorBoundary) | Day 7 Workstream C: error boundary catches crash |
| OMN-11431 | Systemic fixture-mode disclosure gap | PR #118 (DataModeBanner) | Day 7 Workstream C: "Fixture Mode" banner visible |
| OMN-11694 | 4 widgets show LIVE badges over fixture data | PR #118 (conditional isLive) | Day 7 Workstream C: badges absent in file mode |
| OMN-11690 | Asciinema "Watch the Demo" URL is placeholder 404 | PR #127 | Not re-verified (assumed fixed per PR title) |
| OMN-11300 | Control Plane hardcodes "Connected" status | PR #100 | Day 3: verified honest status chips |
| OMN-11301 | Pricing manifest abstraction unwired | PR #724 (omnimarket) | Day 3: verified at diff level |
| OMN-11303 | Bridge↔widget shape mismatch | PR #101 | Day 3: verified in sqlite mode |

### Open (not yet fixed in code)

| Ticket | Finding | Severity | One-line summary |
|---|---|---|---|
| OMN-11828 | System prompt lacks sandbox constraints | High (DEMO_DEGRADED) | ~30% of handlers include `import`; one-line prompt fix eliminates it |
| OMN-11827 | max_tokens=2048 exhausted by thinking mode | High (DEMO_DEGRADED) | Complex tasks truncated; bump to 4096+ fixes |
| OMN-11972 | Run Summary shows "FAIL" on successful runs | Medium (COSMETIC) | ADK response-wrapping mismatch in __main__.py |
| OMN-11696 | --report metrics lack provenance labels | Medium (ARCHITECTURAL_RISK) | 0/20 metrics carry required labels |
| OMN-11695 | File-mode WebSocket reconnect noise | Medium (DEMO_DEGRADED) | 8 failures/60s, no UI indicator |

---

## Evidence labels used in this directory

| Label | Meaning |
|---|---|
| `LOCAL_RESEARCH_ONLY` | Run on contractor machine with no production connectivity; proves code behavior, not runtime materialization |
| `REPLAY` | Fixture-driven; no LLM call; deterministic and reproducible |
| `FIXTURE` | Static fixture data; not derived from runtime events |
| `LIVE_MODEL_ONLY` | Live LLM API call (Gemini cloud); proves model interaction but not production runtime chain |
| `REMOTE_READ_ONLY` | Read-only probe of the runtime host via Tailscale; no mutation, no event publishing |
