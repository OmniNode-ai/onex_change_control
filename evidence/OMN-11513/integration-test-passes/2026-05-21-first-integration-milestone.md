# First Integration Milestone — Baseline Report

**SOW reference:** §3.2 First Integration Milestone + §3.6 Integration Report Template
**Engagement:** OmniNode Hackathon Integration (umbrella ticket OMN-11241)
**Period covered:** Days 1–3 (2026-05-19 → 2026-05-21)
**Author:** clone45 (Bret)

---

## Headline findings from the Day-3 proof-of-life run

The Day-3 proof-of-life pass (run ID `2026-05-21-d2-run-1`) surfaced four findings that are load-bearing for the rest of this report:

1. **The README's reference model (`gemini-2.0-flash`) is unusable** on a freshly minted free-tier key — the key's project has `limit: 0` configured for that exact model, returning HTTP 429 immediately. `gemini-2.5-flash` works. A judge following the README verbatim with a newly minted key will likely hit the same wall. **Filed as OMN-11483.**
2. **`gemini-2.5-flash` returns markdown-fenced YAML inconsistently** — 4 of 6 progressive tasks produced clean YAML; 2 of 6 wrapped the contract in ` ```yaml ` fences which the deterministic validator strict-rejects. The retry prompt doesn't strip code fences, so retries fail the same way. **Filed as OMN-11484.**
3. **`--agent` mode (which exercises the full SOW proof chain) crashes at the invoke step** with `AttributeError: 'dict' object has no attribute 'text'`. The LLM-generated handler assumes input shape that doesn't match what the registry invoker passes. **Filed as OMN-11482 (Urgent).**
4. **Unit-test count has grown from 122 to 614** between Day 2 (clover's baseline) and Day 3 — the hackathon repo is moving quickly.

This report consolidates substantive material produced across 15+ daily artifacts (standups, walkthroughs, audit reports, source sweep, judge-readability findings, proof-of-life bundle) into the single SOW-shaped document. The proof-of-life run provided the one missing data point — whether the progressive demo runs end-to-end with a working credential.

---

## Scope Tested

### Three repositories exercised across Days 1–3

| Repo | Days touched | Scope of testing |
|---|---|---|
| `OmniNode-ai/omnidash` | Days 1–3 | All four data-source modes (file, sqlite, http, postgres) — fully exercised except postgres (no '`<onex-host>`' access). Visual audit of widget palette across modes. Bridge↔widget contract verification. |
| `OmniNode-ai/onex-self-extending-agent` (hackathon repo) | Days 1, 3 | Setup, unit tests, progressive demo, trend report, agent demo, judge-readability review of README/DEMO_GUIDE/ARCHITECTURE. |
| `OmniNode-ai/omnimarket` | Day 2 (contract audit), Day 3 (PR #724 review) | Pricing manifest wiring, delegation orchestrator contract, projection contract. Code-level review, not execution. |

### Coverage by SOW §3.2 line item

| §3.2 required item | Status | Where it lives |
|---|---|---|
| Local hackathon-repo setup result | ✓ Done | clover Day-2 setup transcript + Day-3 fresh re-run (this report's Environment section) |
| Whether the progressive demo command runs locally | ✓ Done | Day-3 proof-of-life run (this report's Result section) |
| Required env vars & which are missing | ✓ Done | Day-1/Day-2 task briefs + Day-3 run manifest |
| Gemini/API billing or provider quota blockers | ✓ Done — was blocked, partially resolved | Day-2 quota note + Day-3 quota probing (this report's Demo Risks section) |
| Event-chain capture produces usable files | ✓ Done | 6 event chains captured Day 3, see `evidence/proof-of-life/2026-05-21-d2-run-1/` |
| Demo reports/screenshots judge-readable | ✓ Done | clover v4 judge-readability findings: 9 actionable issues + 13 counter-findings |
| Failing tests or unclear setup steps | ✓ Done | clover setup-deviations, repo-snapshot, regression-candidates |
| Recommended next integration tasks | ✓ Done | See closing section |

---

## Environment

### Day-3 (proof-of-life) environment

- **repo / branch:** `OmniNode-ai/onex-self-extending-agent` @ `main` @ `03b3165` ("ci(OMN-11413): add merge_group trigger to CI workflow (#75)")
- **command(s):** `uv run python -m src --progressive`, `--report`, `--agent`
- **mode:** local
- **event bus:** in-memory (Kafka unavailable, gracefully skipped)
- **state store:** local filesystem under `.onex_state/hackathon/event_chains/`
- **model endpoint:** Gemini via OpenAI-compatible endpoint, `https://generativelanguage.googleapis.com/v1beta/openai/chat/completions`, model `gemini-2.5-flash`
- **credentials used:** free-tier Gemini API key (Bret-minted 2026-05-21, to be revoked post-run)
- **live services used:** none — Track B (local GPU model) unavailable, Kafka unavailable, '`<onex-host>`' Postgres unavailable

### Required env vars and current source-of-truth

The hackathon repo's progressive demo requires exactly three env vars:

| Env var | Required? | Value class |
|---|---|---|
| `ONEX_TRACK_A_API_KEY` | Yes | OpenAI-compatible API key (Gemini / OpenAI / OpenRouter / etc.) |
| `ONEX_TRACK_A_ENDPOINT` | Yes | Chat-completions endpoint URL |
| `ONEX_TRACK_A_MODEL` | Yes | Model ID |

The omnidash dashboard requires data-source-mode env vars (see §3.4 below in this report).

### Setup commands verified to work

```bash
git clone --depth 1 https://github.com/OmniNode-ai/onex-self-extending-agent.git
cd onex-self-extending-agent
uv sync                       # 150 packages resolved, 147 checked
uv run pytest tests/unit -v   # 614 passed / 1 skipped / 0 failed (~98s)
```

### Setup commands documented but not exercised this engagement

- `python -m src --live` — single-task live run (similar to `--progressive` but one task instead of six)
- `python -m src --failure-showcase` — deterministic failure-then-retry demo
- `python -m src --replay PATH` — replay a golden fixture (no LLM calls)
- `uv run pytest tests/integration -v -m integration` — integration tests requiring Kafka/Redpanda on `localhost:19092` (no Redpanda setup guidance in repo; clover MP-3 finding)

---

## Result

**Partial pass.**

The integration baseline can be characterized as:

- **Strong**: dashboard side. The data-source abstraction works across modes (file / sqlite / http verified; postgres unreachable but expected). Bridge↔widget shape mismatch was filed (OMN-11303), fixed (PR #101), and visually verified.
- **Strong**: hackathon repo's deterministic guardrails. Validator does what it says — it rejected markdown-fenced YAML correctly. Unit-test suite passes cleanly at 614 tests.
- **Adequate**: hackathon repo's headline path. The progressive demo runs end-to-end with cloud credentials. 4 of 6 tasks pass first-attempt; 2 fail due to the model returning markdown-fenced YAML that the retry doesn't recover from.
- **Weak**: hackathon repo's `--agent` invocation path. The full chain (generate → validate → register → invoke) crashes at invoke time with a shape mismatch between the generated handler's expected input and what the registry passes.
- **Weak**: judge onboarding. The README's reference model isn't usable on a newly minted free-tier key (`gemini-2.0-flash` returns 429 immediately; project quota is `limit: 0`). The README does not document this fallback, does not link to the Gemini API console, and stacks three undefined terms in its opening content sentence.
- **Unaddressed**: runtime path. '`<onex-host>`' is unreachable from the contractor environment; no Postgres or runtime API testing was possible. The seed delivery substitutes for the SQLite path only.

---

## What Worked

### Dashboard side (Days 1–2)

- **All four projection contract audits completed** with per-file / per-endpoint discipline. Model registry (3A), orchestrator contract (3B including the new `runtime_ingress` stanza), projection contract (3C with reusable Python comparison script).
- **No-hardcoded-routing audit clean across 25 files.** Zero hardcoded model-selection conditionals across orchestrator handlers, `delegation-api.ts`, and the dashboard delegation widget directory.
- **Express bridge ↔ widget shape mismatch root-caused and resolved.** Filed as OMN-11303, fixed by PR #101 overnight, visually verified in sqlite mode Day 3: Quality Gate / Model Routing / Token Usage / Delegation Savings all render aggregated bridge responses correctly.
- **Control Plane false-live behavior root-caused and resolved.** Filed as OMN-11300, fixed by PR #100 overnight, verified Day 3 morning: status chips correctly distinguish file/sqlite/http/postgres modes; synthetic events get `[DEMO]` badge + reduced opacity.
- **Pricing manifest unwired finding** root-caused and resolved at the orchestrator layer. Filed as OMN-11301, fixed by PR #724 in omnimarket (72-line patch to `pricing.py` wiring the existing `ModelPricingTable` into delegation orchestrator handlers).
- **Seeded-SQLite verification path established.** Jonah's `omnidash-sqlite-setup.zip` integrated, `npm run seed:sqlite` works, row counts match the doc (delegation_events=25, llm_call_metrics=18, savings_estimates=12, delegation_event_log=6, schema_migrations=4). All 12 §4a projection endpoints return HTTP 200 with aggregated shapes.

### Hackathon repo (Days 1, 3)

- **Setup happy path is exemplary.** `uv sync` + `uv run pytest tests/unit -v` runs cleanly with zero failures and no credentials required. This is more than most hackathon repos manage. Clover's judge-readability review explicitly highlighted this as a counter-finding.
- **Deterministic validation gate works as designed.** The validator correctly rejected markdown-fenced YAML in 2 of 6 progressive runs. This is a positive — it's catching real model misbehavior, not just decorative.
- **Event-chain capture produces usable JSON files.** Each progressive task wrote a typed event chain JSON to `.onex_state/hackathon/event_chains/<correlation_id>.json` containing the command envelope, the completion envelope, all attempt metadata (provider, model, tokens, latency, validation_errors), and the generated `contract_yaml` for passing runs.
- **Trend report renders correctly.** `python -m src --report` produces a clean cost/latency/attempts table covering both pre-existing chains and Day-3 additions. The "ledger learns from successes" thesis is verifiable in the file output even if cost values are zero (see Pricing finding below).

### Cross-cutting

- **Judge-readability review captured 32 sections with 9 actionable findings + 13 counter-findings.** The judge-facing docs (README, DEMO_GUIDE, ARCHITECTURE) are in better shape than typical for a hackathon repo, with specific small surgical edits identified.
- **Overnight pipeline behavior is now understood.** The "batch-close at 04:11 UTC then reopen-and-fix" pattern was empirically validated across OMN-11300/11301/11303 — distinct from the "Backlog→Done without code fix" pattern seen on contractor tickets OMN-11286/11288/11289.

---

## What Broke

### New findings from Day-3 proof-of-life run

1. **`gemini-2.0-flash` returns HTTP 429 with `limit: 0` on a freshly minted free-tier key.** The README's reference model is not usable for first-contact testing without project configuration that the README doesn't document. Switched to `gemini-2.5-flash` to complete the run.

2. **`gemini-2.5-flash` returns markdown-fenced YAML inconsistently.** 2 of 6 progressive tasks failed because the model wrapped the generated `contract.yaml` in ` ```yaml ` fences. The validator rejects this strictly (`yaml parse error: found character '`' that cannot start any token`). The retry prompt does not strip fences, so retries fail the same way. The same model returned clean YAML on the other 4 tasks — behavior is inconsistent.

3. **`--agent` mode crashes at invoke step.** The full agent loop (`python -m src --agent`) generated and registered a node successfully but crashed during invocation with `AttributeError: 'dict' object has no attribute 'text'` (in the generated handler). The LLM-produced handler expected an attribute-style input; the registry invoker passes a dict. This is a contract violation between generator and runtime that the validation gate doesn't catch.

4. **Cost values are zero because pricing isn't wired for `gemini-2.5-flash`.** All event chains show `cost_inference_usd: 0.0` with `cost_basis: "unknown"`. Token usage IS measured correctly; only the USD conversion is missing. This ties to OMN-11301's broader theme of pricing-manifest plumbing.

### Carried-forward findings from Days 1–2

5. **No per-widget fixture-mode disclosure** across 25 of 26 widgets (filed as OMN-11431 Day 3). Only Control Plane (post-PR-#100) shows mode badges. A user in file mode sees data that looks authoritative without distinguishing fixture-bound from projection-bound.

6. **Single crashing widget locks the dashboard** via persisted localStorage layout (filed as OMN-11429 Day 3). User has no in-app recovery — must use DevTools or a private browser window.

7. **Three contractor tickets closed Backlog→Done without code fix** (OMN-11288, OMN-11289 plus OMN-11286's ambiguous lifecycle). Clarification questions posted Day 3 awaiting response.

8. **Dashboard has no in-UI data-source switcher.** Mode resolves from `VITE_DATA_SOURCE` at Vite startup, falling back to contract default which is currently `postgres`. With no env override and no local Postgres, every widget renders empty — symptomatic of widget-level bugs but is actually configuration.

### Contractor-environment friction (not bugs, but real)

9. **OCC repo not accessible from contractor environment.** All evidence currently lives in `docs/projects/hackathon_prep/`; canonical path is `onex_change_control/evidence/OMN-11241/`. Port-over will be needed once OCC access is configured.

10. **`/tmp` swept overnight on the contractor machine.** Required re-cloning `omnimarket` and `onex-self-extending-agent` on Day 3. Persistent clones now live under `docs/projects/hackathon_prep/` to survive future sweeps.

---

## Demo Risks

The full set of Linear-tracked demo risks across the engagement:

| Ticket | Title | Severity | Demo-blocking? | Status |
|---|---|---|---|---|
| OMN-11286 | Synthetic telemetry labels (3 prototype widgets) | Medium | No — narrow scope confirmed | Closed; clarification question pending |
| OMN-11287 | Empty-data crashes | Medium | No | Closed cleanly (PR #92 from Day 1 + later patches) |
| OMN-11288 | Local fixture shape drift | Medium | Maybe (Delegation Metrics crashes) | Closed; clarification question pending — bug appears unfixed |
| OMN-11289 | Express bridge fixture fallback | Medium | No | Closed; clarification question pending — bug appears unfixed |
| OMN-11290 | Offline SQLite seed | High (was) | No — seed delivered | Closed cleanly |
| OMN-11300 | Control Plane false-live | Urgent | Yes (was) | Closed; PR #100 visually verified |
| OMN-11301 | Pricing manifest unwired | Medium (architectural) | No | Closed; PR #724 (omnimarket) ships the fix |
| OMN-11303 | Bridge↔widget shape mismatch | High (was) | Yes (was) | Closed; PR #101 visually verified |
| OMN-11429 | Crash-loop locks dashboard via localStorage | Medium (UX-resilience) | No, but UX hazard | Open |
| OMN-11431 | No per-widget fixture-mode disclosure | Medium (architectural) | Maybe — credibility risk for judges | Open |

Additional demo risks from clover's judge-readability review (Day-2 v4 pass, 32 sections, 9 actionable findings + 13 counter-findings). All 11 findings were filed as Linear tickets on Day 3 — High-priority ones below, full set in OMN-11485 through OMN-11495:

- **OMN-11485** (High) — README §5.6 trend-report sample output isn't labeled as captured-vs-illustrative
- **OMN-11486** (High) — README never says how to acquire a Gemini API key (compounds with OMN-11483)
- **OMN-11489** (High) — README §4 unconditional "in parallel on every task" claim contradicts §8 Track-B-is-optional qualifier
- **OMN-11490** (High) — README §3/§4 imply same-session tool invocation; ARCHITECTURE §6 is candid that standalone demo simulates only (reinforced by OMN-11482)
- **OMN-11494** (High) — README opening sentence stacks three undefined terms (ONEX / compute nodes / MCP tools)

---

## Evidence

### Day-3 proof-of-life run

- **Bundle:** `docs/projects/hackathon_prep/evidence/proof-of-life/2026-05-21-d2-run-1/`
- **Typed artifacts:** `run_manifest.json`, `contract_snapshot.json`, `verifier_result.json`, `artifact_manifest.json`, `golden_event_chain.json`
- **Raw event chains:** 6 files under `event-chains/` (one per progressive task)
- **Run logs:** `progressive-stdout.log`, `trend-report-stdout.log`, `agent-stdout.log`
- **Companion narrative:** `proof_summary.md` — covers all reporting-format-doc required fields including the "live / simulated / replayed" registration-status disclosure

### Day-1/Day-2 evidence and audit reports

- **Day-1 standup + diary entries** under `docs/projects/hackathon_prep/progress_reports/`
- **Day-2 mid-day + end-of-day standups** under same
- **Day-2 walkthrough** under `docs/projects/hackathon_prep/work_walkthroughs/2026-05-20-day2-walkthrough.md`
- **Demo risk report** for OMN-11303 under `docs/projects/hackathon_prep/demo-risk-reports/2026-05-20-bridge-widget-mismatch.md`
- **Visual aid (HTML)** for OMN-11303 under `docs/projects/hackathon_prep/visual-aids/2026-05-20-bridge-widget-mismatch.html`
- **Source-code sweep** of all 26 widgets under `docs/projects/hackathon_prep/widget-audit/source-sweep-2026-05-21.md`
- **Per-widget catalog from visual audit** appended to `docs/projects/hackathon_prep/progress_reports/2026-05-21-morning-findings.md`

### Sub-team status reports (full audit trail)

- **cypress** — 5 visual-audit checklists/reports + screenshots + console captures across versions v2–v5 under `team/cypress/status_reports/`
- **hawthorn** — 27 contract-audit artifacts (model registry, orchestrator, projection, no-hardcoded-routing, endpoint comparison) under `team/hawthorn/status_reports/`
- **clover** — Phase 4 baseline (setup-transcript, regression-candidates, repo-snapshot, judge-readability findings across 32 sections) under `team/clover/status_reports/`

### Correlation continuity

The Day-3 proof-of-life run produced six correlation IDs, one per task; full chain captured in `event-chains/<id>.json`. Designated golden chain for cross-reference: **`1e83ee21-eb64-4018-9371-64a3695430ab`** (task: "Categorize product descriptions"; PASS on first attempt; complete contract_yaml + token usage + latency captured). The dashboard's own delegation data uses correlation IDs from the seeded SQLite dataset (`corr-seed-001` through `corr-seed-005`) — these two correlation spaces are distinct.

---

## Issues / Repro Steps

### New issues from Day-3 proof-of-life run (filed in Linear 2026-05-21)

#### Issue D2-1 → OMN-11483 (Medium): `gemini-2.0-flash` quota=0 on free-tier project

- **Severity:** Medium (onboarding blocker for judges)
- **Demo-blocking:** No — workaround is to use `gemini-2.5-flash`
- **Repro:** Mint a fresh Gemini API key in a Google Cloud project. Configure `ONEX_TRACK_A_MODEL=gemini-2.0-flash` per README. Run `python -m src --progressive`.
- **Expected:** Progressive demo runs.
- **Actual:** HTTP 429 immediately on first request. Direct API probe confirms `Quota exceeded for metric: generativelanguage.googleapis.com/generate_content_free_tier_requests, limit: 0, model: gemini-2.0-flash`.
- **Suggested ticket title:** `[Hackathon Integration] README's reference model gemini-2.0-flash has limit=0 on free-tier; document fallback`
- **Suggested fix:** README §5.4 documents both `gemini-2.0-flash` and `gemini-2.5-flash` as acceptable models; clarify that free-tier quotas may differ across models in the same project.

#### Issue D2-2 → OMN-11484 (Medium): Markdown-fenced YAML rejected by validator; retry doesn't strip fences

- **Severity:** Medium
- **Demo-blocking:** Maybe — 2 of 6 progressive tasks failed this way on Day 3
- **Repro:** Run `python -m src --progressive` with `ONEX_TRACK_A_MODEL=gemini-2.5-flash`.
- **Expected:** All 6 tasks pass, possibly with retries.
- **Actual:** 2 of 6 tasks (specifically the "spam detection" and "user-preferences" tasks) fail both attempts because the model returns the contract YAML wrapped in ` ```yaml ` fences. Validator rejects with `yaml parse error: found character '`' that cannot start any token`.
- **Suggested fix:** Either (a) strip code fences in the consumer before passing to the validator, OR (b) update the retry prompt to explicitly tell the model "do not wrap the YAML in markdown code fences."

#### Issue D2-3 → OMN-11482 (Urgent): `--agent` mode crashes at invoke step with shape mismatch

- **Severity:** High (demo-blocking for the headline "agent invokes the tool it just built" claim)
- **Demo-blocking:** Yes — `python -m src --agent` is the canonical "full proof chain" command
- **Repro:** `ONEX_TRACK_A_MODEL=gemini-2.5-flash python -m src --agent`
- **Expected:** Agent loop completes through invoke.
- **Actual:** `AttributeError: 'dict' object has no attribute 'text'` in the generated handler at `tool_registry.py:85`. The handler the LLM generated expects `.text` attribute access; the registry passes a dict.
- **Root cause hypothesis:** Either the generation prompt does not constrain handler input shape, OR the registry invoker doesn't normalize the test input to the contract's declared `input_model` shape.
- **Suggested fix:** Tighten the generation prompt's input-model contract, AND/OR have the registry invoker construct typed input objects from the contract's `input_model` declaration before invoking.

### Existing issues with full repros already filed

For the eight existing Linear tickets (OMN-11286 / 11287 / 11288 / 11289 / 11290 / 11300 / 11301 / 11303 / 11429 / 11431), full repro steps already exist in Linear and need not be duplicated here.

---

## Recommended Next Actions

### Top 3 blockers

1. **Hackathon repo's `--agent` mode crashes at invoke step (D2-3 above).** This is the canonical "full proof chain" command and the one whose output most closely matches the SOW D2 proof chain. With it broken, demo recording of the full chain requires either the `--progressive` mode (which doesn't exercise registration / invocation), the `--failure-showcase` mode (which uses an injected handler so doesn't test the LLM-generated-handler invoke path), or the `--replay` mode (which is golden fixture only, no live LLM call). Each workaround loses something from the demo narrative.

2. **No '`<onex-host>`' access from contractor environment.** This blocks SOW §3.3 D2 step 5 ("Invoke the tool if the runtime path is available"), all Postgres-mode dashboard testing, and any "live MCP registration" evidence. The seed-delivery + sqlite-mode substitution covers the projection-store path but the runtime-projection-API path remains uncovered.

3. **OCC repo not accessible from contractor environment.** All Day-1/2/3 evidence currently lives in `docs/projects/hackathon_prep/` rather than the canonical `onex_change_control/evidence/OMN-11241/` path. Port-over is mechanical once access is granted but until then the canonical evidence trail is technically not in the canonical place.

### Top 3 quickest fixes

1. **Strip markdown code fences in the consumer's response handling (D2-2 above).** One-line fix in the LLM response processing: if response starts with ` ```yaml ` and ends with ` ``` `, strip them before passing to the validator. Or alternatively, add a one-sentence instruction in the retry prompt. Either way: ~10 minutes of work, eliminates 2-of-6 progressive failures.

2. **Document `gemini-2.5-flash` as the README's recommended model (D2-1 above).** The current README references `gemini-2.0-flash` which has `limit: 0` quota on newly minted free-tier keys. Updating the example commands to use `gemini-2.5-flash` (which works) is a one-line README change and removes a real onboarding blocker for judges.

3. **Fix data-source-mode default flip in `omnidash/contract.yaml`.** Per OMN-10976 the contract default was flipped from `sqlite` to `postgres` for runtime use. For local dev / hackathon judging, the default with no Postgres available produces all-empty widgets — looks like widget bugs but is config. A separate `OMNIDASH_LOCAL_PROFILE=true` env or a CLI override could keep the runtime default at `postgres` while making local-dev default to `sqlite`. ~30 minutes of work.

### Recommended demo path

**Local mode with sqlite-backed dashboard plus `gemini-2.5-flash` for the hackathon repo's progressive demo. Specifically: `local + replay-friendly` rather than `live`.**

Reasoning:

- **`live` mode is unreachable.** Requires '`<onex-host>`' Postgres + full ONEX runtime + MCP tool sync service. None of that is reachable from the contractor environment, and per ARCHITECTURE §6, the MCP tool sync component is not bundled in the standalone repo at all.
- **`local` mode works end-to-end for the dashboard.** All four data-source modes function (file / sqlite / http verified). sqlite mode shows real aggregated projection data after PR #101.
- **`local` mode works for the hackathon repo's progressive demo.** With `gemini-2.5-flash` + the markdown-fence-stripping fix proposed above, this is the strongest available proof chain.
- **`replay` mode (the hackathon repo's `--replay PATH` flag) is the safe fallback** for demo recording where network or quota issues might disrupt a live run. The committed event chains (12 entries in `.onex_state/hackathon/event_chains/` post-Day-3) provide replay corpus.

The honest framing for judges should be: "We're showing local-mode demo with deterministic replay fallback; runtime MCP registration is simulated per architectural design (ARCHITECTURE §6); the deterministic validation gate is exercised live against the LLM's output."

### Recommended next integration tasks in priority order

1. ~~File the three D2-1/D2-2/D2-3 findings as Linear tickets~~ — **Done 2026-05-21**: filed as OMN-11482 (Urgent, D2-3), OMN-11483 (Medium, D2-1), OMN-11484 (Medium, D2-2). All three linked to OMN-11241.

2. **Visually verify PR #724** by tracing a delegation orchestrator handler through the new `pricing.py` flow once an end-to-end orchestrator run is possible (currently blocked on runtime access; can be tested via a focused unit test instead).

3. **Re-run the `--agent` proof chain after D2-3 is fixed** to capture a complete generate→validate→register→invoke evidence bundle (the missing piece for §3.3 D2 chain step 5 under the "if the runtime path is available" caveat — even simulated invocation satisfies the bundle requirements once it doesn't crash).

4. **Port the entire `docs/projects/hackathon_prep/evidence/` tree to OCC** once contractor-side access is configured.

5. ~~Decide which of clover's README/DEMO_GUIDE/ARCHITECTURE findings to upstream as PRs.~~ — **Done 2026-05-21**: all 11 findings filed as Linear tickets OMN-11485 through OMN-11495. High-priority (judge-facing credibility): OMN-11485, OMN-11486, OMN-11489, OMN-11490, OMN-11494. Medium: OMN-11487, OMN-11492, OMN-11493, OMN-11495. Low: OMN-11488, OMN-11491. All linked to OMN-11241.

6. **Address the three clarification-question tickets (OMN-11286, OMN-11288, OMN-11289)** based on overnight responses from the platform.

7. **Wire the cost values for `gemini-2.5-flash`** in the consumer's pricing table. Without it, the trend report's signature "cost decreases over time" claim cannot be verified with current model.

---

## Day-5 Addendum (2026-05-24)

This addendum updates the Day-3 baseline with findings from Days 4–5 of the engagement. The original sections above are preserved as-is; the updated blockers, fixes, and demo-path recommendation below supersede the Day-3 versions.

### Updated: Top 3 blockers

1. **`gemini-2.0-flash` hardcoded across 3+ source surfaces (OMN-11691).** The model name `gemini-2.0-flash` — which has `limit: 0` on the free tier — is hardcoded in `src/agent/agent.py:40`, `src/contracts/model_registry.yaml:35`, and `src/contracts/cost_pricing.yaml:53,61,69,77`. The `ONEX_TRACK_A_MODEL` env var is never read by any source file (`grep -rn "ONEX_TRACK_A_MODEL" src/` returns zero hits). A judge following the documented commands with a freshly minted API key hits HTTP 429 immediately. We empirically confirmed today that replacing the hardcoded value with `gemini-flash-latest` across all surfaces unblocks the full chain.

2. **System prompt doesn't tell the LLM about sandbox constraints (~30% failure rate).** The generation prompt at `consumer.py:340-348` asks the LLM to produce a Python handler but never mentions that the handler runs in a restricted sandbox where `import`, `open`, `exec`, `eval`, and `compile` are stripped. Empirical probe today: 10 identical calls without the constraint → 7/10 safe, 3/10 generated `import re` (which the sandbox correctly rejects per OMN-11496). Adding one line — `"IMPORTANT: Do NOT use import statements. Your handler runs in a restricted sandbox where import, open, exec, eval, and compile are not available."` — brought the rate to 9/9 safe (10th was rate-limit 429). The constraint is 100% effective. Without it, roughly 1 in 3 demo runs will fail at registration despite the code working correctly.

3. **Judge-facing documentation may not land as intended for the hackathon audience.** Across Days 2–4, the engagement reviewed all 32 sections of the three judge-facing docs (README, DEMO_GUIDE, ARCHITECTURE) and filed 11 tickets (OMN-11485 through OMN-11495) for patterns that could affect judge perception — undefined terms in the opening sentence, unlabeled sample output, framing that implies capabilities the standalone demo doesn't deliver, and internal contradictions. Of the 11, 6 were fixed-real by the overnight pipeline's restructure; 2 remain still-valid (OMN-11494: first sentence stacks 3 undefined terms; OMN-11485: trend-report sample not labeled); 3 are partially-fixed. An important caveat: the contractor's perspective (Bret's perspective) may not match the judges'. Hackathon judges may share domain vocabulary with the author and read the docs differently than a first-contact outsider would. The engagement's observation is that these patterns exist and are worth attention; the recommendation is to have additional readers outside the development team — ideally someone closer to the judge persona — review the judge-facing docs before submission and assess whether the remaining findings affect readability for that specific audience. The asciinema "Watch the Demo" placeholder URL (OMN-11690) is a concrete sub-item here: `README.md:133-139` links to a nonexistent recording at `https://asciinema.org/a/omn-11523`; the real recording IDs from `docs/demo/demo_manifest.json` don't match. A judge clicking "Watch the Demo" hits a 404 regardless of how well the prose reads.

### Updated: Top 3 quickest fixes

1. **Add one line to the generation system prompt (consumer.py:347).** Adding `"IMPORTANT: Do NOT use import statements. Your handler runs in a restricted sandbox where import, open, exec, eval, and compile are not available. Use only built-in Python functions and operations."` eliminates the ~30% sandbox-rejection failure rate. Empirically verified today: 0% unsafe handlers across 9 successful calls with the constraint. ~2 minutes of work.

2. **Update `gemini-2.0-flash` → `gemini-flash-latest` in 3 source files.** `agent.py:40`, `model_registry.yaml:35`, and `cost_pricing.yaml:53,61,69,77`. Three one-line edits. Unblocks the demo for any judge with a free-tier Gemini key. ~5 minutes of work. (Also wire `ONEX_TRACK_A_MODEL` env var so this doesn't have to be hardcoded — slightly more work but the right fix.)

3. **Have someone outside the dev team read the judge-facing docs before submission.** The engagement identified 11 documentation patterns that could affect judge perception; 6 were already fixed by the overnight pipeline. The remaining 5 (2 still-valid + 3 partially-fixed) are small but judge-visible. A 30-minute read-through by someone in the judge persona — not the author and not the contractor — would surface whether the remaining patterns actually matter for the intended audience. The asciinema URL fix (`omn-11523` → a real recording ID from `demo_manifest.json`) is a concrete 2-minute sub-item that should happen regardless.

### Updated: Recommended demo path

**Local mode with `gemini-flash-latest` for the hackathon repo's `--agent` demo + deterministic `--replay` as fallback. Specifically: `local + live-when-stable + replay-fallback`.**

Updated reasoning (supersedes Day-3's "local + replay-friendly"):

- **LIVE `--agent` mode now works end-to-end** when three surfaces are patched (model name + prompt constraint). Day-5 achieved the full 5/5 chain: generate → validate → register → invoke (×2 with correct sentiment results). Correlation `c5deda45-a1eb-4c0a-b675-3fb6198a69a6`. The code is functional; the demo path is blocked only by configuration/prompt issues that are each one-line fixes.
- **`--replay` remains the safe fallback** for recorded demo sessions where Gemini availability can't be guaranteed. The replay path is deterministic, honest (`[REPLAY]` labeling + "no LLM calls made" closer), and reproducible across multiple invocations (fixture sha256 stable).
- **Upstream availability is a real concern for LIVE demos.** Day-4 testing showed `gemini-2.5-flash` flapping at sub-minute granularity (single-call probes returned 200; multi-call agent sequences hit 503 on call 2-4). `gemini-flash-latest` was stable across the multi-call sequence today. For a recorded demo, try LIVE first and fall back to REPLAY if Gemini is unstable. For a live-in-front-of-judges demo, REPLAY is the safer choice unless upstream stability is confirmed immediately prior.
- **Dashboard in sqlite mode** remains the recommendation for the dashboard side (PR #101 fixes verified Day 3). File mode has 4 actively-misleading widgets (OMN-11694) and no per-widget fixture-mode disclosure (OMN-11431).

### Updated: Demo mode decision rubric (Day-2 deliverable, filled Day 5)

| Criterion | Recommended mode | Notes |
|---|---|---|
| Most stable | `--replay` | Deterministic, fixture-driven, bit-for-bit reproducible across invocations. No network dependency. Zero observed failures across all replay runs in the engagement. |
| Most architecturally honest | `--agent` (LIVE, patched) | The full generate→validate→register→invoke chain against a real LLM. Requires 3 temporary patches (model name ×2 + prompt constraint). Achieved 5/5 chain on Day 5. Honestly demonstrates the self-extending loop working end-to-end. |
| Best fallback/offline mode | `--replay` | Works with no API key, no network, no provider. Golden fixture at `docs/evidence/golden/golden_fixture.json` (sha256 `527c0de6...`). Explicitly labeled `[REPLAY]` with "no LLM calls made" closer. |
| Least operationally risky for live demo | `--replay` | Zero external dependency = zero points of failure. `--agent` LIVE depends on Gemini upstream availability (observed flapping at sub-minute granularity on Day 4) and LLM output quality (~30% sandbox-rejection rate without prompt constraint, ~0% with it). |
| Easiest to explain to judges | `--agent` (LIVE) then `--replay` | LIVE is the more impressive story ("the agent just built a tool and used it"). REPLAY is easier to frame honestly ("deterministic proof of the pipeline; no LLM call but the same validate→register→invoke chain"). Recommended: attempt LIVE first; fall back to REPLAY if it fails. |
| Least likely to drift during demo | `--replay` | Fixture-pinned. The LIVE path depends on model availability (gemini-2.0-flash is now 404; gemini-flash-latest alias floats; gemini-2.5-flash 503s intermittently). Replay doesn't drift. |

**Net recommendation:** Try `--agent` LIVE first (with the 3 patches). If it succeeds (70%+ probability with prompt constraint), that's the strongest demo. If it fails (model flapping or sandbox rejection), immediately switch to `--replay` — the fallback is honest, deterministic, and judge-ready. For the dashboard side, use sqlite mode (PR #101 verified; all 4 delegation widgets render correctly).

### Updated: "What surprised you most?" (Day-2 deliverable, filled Day 5)

**Hidden assumptions we discovered:**

- The `ONEX_TRACK_A_MODEL` env var is never read by any source file (`grep -rn "ONEX_TRACK_A_MODEL" src/` returns zero hits). The README documents it, PR #104 updated the README to reference it, but the agent and consumer code both hardcode model names independently. A user who sets the env var per the README gets silently ignored.
- The `gemini-flash-latest` model alias floats between Gemini versions. On Day 4, it pointed at `gemini-2.5-flash`; by Day 5, it pointed at `gemini-3.5-flash` — which has a thinking mode that consumes the `max_tokens` budget before visible output is emitted. The same code, same prompt, same `max_tokens=2048`, produced complete responses one day and truncated responses the next. The model alias is an invisible dependency.
- `gemini-2.0-flash` — the model hardcoded throughout the codebase and documented in the README — returned HTTP 404 (model not found) by Day 5. It worked (with quota issues) on Days 3-4. The model was retired from the API without notice. Any demo recorded with `gemini-2.0-flash` commands would fail on playback.

**Confusing UX:**

- The dashboard has no in-UI data-source switcher. Mode resolves from `VITE_DATA_SOURCE` at Vite startup, falling back to the contract default (`postgres`). With no Postgres available and no env override, every widget renders empty — symptoms identical to widget-level bugs. We spent time diagnosing "broken widgets" before realizing it was a config default.
- 4 dashboard widgets display "LIVE" badges over fixture data (event-stream, live-event-stream, delegation-control-plane, readiness-gate). A judge looking at file-mode dashboard sees what appears to be a connected live system. The new HonestyStateBadge component (OMN-11651) addresses this but currently shows "Fixture" AND "LIVE" simultaneously — contradictory.
- Adding the Delegation Metrics widget crashes the entire dashboard and locks it via localStorage. Recovery requires DevTools or a private browser window. No error boundary catches the crash.

**Architectural inconsistencies:**

- The security sandbox (`_SAFE_BUILTINS`) and the generation system prompt are in tension. The sandbox strips `__import__` (correctly blocking `import` statements); the system prompt doesn't mention this restriction. ~30% of generated handlers use `import re`, which the sandbox correctly rejects. Both subsystems are working as their authors intended — they just don't know about each other. One line added to the prompt eliminates the conflict.
- The same contract YAML field (`pricing_manifest_version`) is declared, persisted in the DB, and populated with `v2` values — but nothing in the consumer layer reads it. Both the orchestrator and the dashboard hardcode model-specific prices instead. The abstraction was designed and populated; the consumer wiring was never done.
- The `--report` trend table (the headline "cost decreases over time" evidence) displays metrics with zero provenance labels. A judge reading the report can't tell if the numbers are measured, estimated, fixture-derived, or demo-only.

**Demo-risk surfaces:**

- The README's "Watch the Demo" asciinema link (`https://asciinema.org/a/omn-11523`) is a Linear-ticket-ID placeholder. It returns 404. Judges clicking the most prominent demo link get a broken page as their first interaction.
- The replay path is the only reliable demo path today. LIVE `--agent` succeeded once (Day 5, with 3 patches) but depends on Gemini upstream availability + LLM output quality + token-budget headroom for thinking models. A recorded demo should use LIVE-first-REPLAY-fallback; a live-in-front-of-judges demo should use REPLAY unless Gemini stability is confirmed immediately prior.

### Updated: Key milestone achieved — full LIVE 5/5 proof chain

On Day 5 (2026-05-24), the `--agent` proof chain completed end-to-end in LIVE mode for the first time in the engagement:

| Step | Observed |
|---|---|
| Generate | `sentiment_classifier` contract + handler — no `import` statements |
| Validate | Passed |
| Register | Sandbox accepted the handler; registered as MCP tool |
| Invoke #1 | `"I absolutely love this!"` → `{"sentiment": "positive", "confidence": 1.0}` |
| Invoke #2 | `"This was a terrible waste of money."` → `{"sentiment": "negative", "confidence": 1.0}` |

Three temporary local patches were required (agent.py model, registry.yaml model, consumer.py prompt constraint). All reverted post-capture, byte-for-byte verified. Evidence at `evidence/proof-of-life/2026-05-24-0b2a477-live-full-chain/`.

This observation demonstrates: **the self-extending agent loop works end-to-end when the model is available, the model name is configured correctly, and the system prompt tells the LLM about sandbox constraints.** The three fixes needed are each one-line edits. Whether this observation constitutes demo-readiness is the platform/Jonah determination.

---

## Pointers for porting to OCC

When OCC access is configured:

| Local path | OCC path |
|---|---|
| `docs/projects/hackathon_prep/evidence/2026-05-21-first-integration-milestone.md` | `onex_change_control/evidence/OMN-11241/integration-test-passes/2026-05-21-first-integration-milestone.md` |
| `docs/projects/hackathon_prep/evidence/proof-of-life/2026-05-21-d2-run-1/` | `onex_change_control/evidence/OMN-11241/proof-of-life/2026-05-21-d2-run-1/` |
| `docs/projects/hackathon_prep/demo-risk-reports/2026-05-20-bridge-widget-mismatch.md` | `onex_change_control/evidence/OMN-11241/demo-risk-reports/2026-05-20-bridge-widget-mismatch.md` |
| `docs/projects/hackathon_prep/progress_reports/*` | `onex_change_control/evidence/OMN-11241/daily-updates/*` |

---

*End of report.*
