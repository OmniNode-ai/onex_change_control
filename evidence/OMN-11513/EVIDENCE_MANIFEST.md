# OMN-11513 Evidence Manifest

**Ticket:** OMN-11513 (May Monthly Integration Testing Plan)
**Engagement:** OmniNode Hackathon Integration — contractor Bret (clone45@gmail.com)
**Period covered:** 2026-05-19 through 2026-05-24 (Days 1-7)
**Last updated:** 2026-05-24

---

## Artifacts in this directory (current-state, ported from engagement workspace)

### integration-test-passes/

| File | Date | Description | Status |
|---|---|---|---|
| `2026-05-21-first-integration-milestone.md` | Day 3 + Day 5 addendum | SOW §3.2 baseline report. Covers all 8 required line items + closing rubric (Top 3 blockers, Top 3 fixes, demo mode rubric, "what surprised you most?"). Day-5 addendum updates blockers + records full-chain achievement. | **Current** |
| `2026-05-24-integration-pass-report.md` | Day 7 | Fresh integration pass per Jonah's May 24 integration prompt plan. Covers A0-A3 + B + C + D workstreams on current main `62b87c2`. Most comprehensive single-session report of the engagement. | **Current** |

### proof-of-life/

| Directory | Date | Description | Status |
|---|---|---|---|
| `2026-05-24-62b87c2-live-a1/` | Day 7 | Event chain from first successful `--agent` full chain on UNMODIFIED main. Correlation `7d997a95-...`. No patches. | **Current — canonical LIVE evidence** |
| `2026-05-24-0b2a477-live-full-chain/` | Day 5/6 | Event chain from first-ever full 5/5 chain (with 3 temporary patches, all reverted). Correlation `c5deda45-...`. | **Historical — superseded by Day-7 unpatched run** |
| `2026-05-22-0b2a477-replay-p2-replay/` | Day 4 | Replay-mode proof: validate → register → invoke on golden fixture. 5/5 honesty checks pass. No LLM call. | **Current — canonical REPLAY evidence** |

### sea-demo-acceptance/

| File | Date | Description | Status |
|---|---|---|---|
| `2026-05-22-wave5-evidence-report.md` | Day 4 + Day 5 addendum on Linear | Wave 5 SEA Demo + Security Acceptance evidence. 5/5 security-negative tests, LIVE vs REPLAY proof matrix (11/12 cells → 12/12 with Day-5 addendum), CLI provenance audit (0/20 labels). | **Current (Day-5 addendum posted as Linear comment on OMN-11513, not yet merged into this file)** |

### demo-risk-reports/

Currently empty in OCC. Demo risk findings are tracked via Linear tickets (OMN-11694, OMN-11695, OMN-11696, OMN-11827, OMN-11828, OMN-11972) + the integration reports above. The standalone demo-risk report from Day 2 (`docs/projects/hackathon_prep/demo-risk-reports/2026-05-20-bridge-widget-mismatch.md`) is historical — the bridge-widget mismatch was fixed Day 3 (PR #101, verified).

### daily-updates/

Not ported to OCC. Daily standups live in the engagement workspace at `docs/projects/hackathon_prep/progress_reports/` and are posted to Slack `#standups` per engagement doctrine. They are process artifacts, not typed evidence.

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
| `REMOTE_READ_ONLY` | Read-only probe of `.201` via Tailscale; no mutation, no event publishing |
