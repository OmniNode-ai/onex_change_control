# SEA Delegation Verification — Integration Test Pass Report (2026-06-08)

**Engagement:** OmniNode Hackathon Integration (umbrella OMN-11513)
**Lane:** stability-test only, from the contractor seat (WSL2 over Tailscale)
**Plan executed:** `2026-06-08-bret-verification-plan.md` (8 phases)
**SEA branch/SHA:** `dev` / `27f4f03` for the afternoon retest (Phases 1–3). Morning preflight (Phase 0) ran on `dev` / `80dc069` before the contractor clone was pulled to current dev. Python 3.12.13.
**New since the previous baseline:** `27f4f03` raises the SEA generation retry budget (#218), plus #215 retiring legacy demo nodes.

---

## Final verdict — partially green / projection limited

The broker blocker (OMN-12832) is **cleared and confirmed from the contractor seat**. After Jonah's OMN-12834 fix, the contractor-seat `--agent` generation completes **end to end, fully local, with no cloud key** — bus-backed (for delegated inference), generate → register → invoke, fresh correlation, contract-valid node, correct terminal result (`{"is_healthy": true}`, PASS, attempts 1, Cost $0.0000). Local delegated inference is independently proven (Phase 3 PASS), and the projection API is reachable.

**This is runtime-observed, not projection-backed.** The `--agent` path records its `node-generation-requested/completed` events to a local event-chain file; it does not publish them to the bus, so they never reach the `generation_events` projection (queried for our correlation `0cdc2410`: row_count 0). A projection-backed generation row is produced only by the server-side runtime path (dashboard/API-triggered). The contractor seat cannot exercise that path — both because `--agent` records locally and because triggering the server path would require a raw bus publish or runtime trigger the plan forbids. So projection confirmation needs operator-side capture. This is a scope/architecture boundary, **not a projection defect** (root cause code-confirmed in `__main__.py:819-839`; see the Phase 4 transcript).

Scope note: the proven generation path uses the supported local-ADK overlay (local reasoning backend); the default cloud-Gemini orchestrator path still requires a key and was not exercised (out of scope today). Prod and cloud/escalation were not verified (out of scope).

**Day timeline:**
- **AM:** blocked at broker reachability (metadata advertised `<onex-host>`). Filed OMN-12832.
- **Midday:** Jonah recreated the stability broker. Retest from our seat: Phase 1 green, Phase 3 PASS. Phase 2 broker-cleared but stopped at the ADK backend; filed OMN-12834 for the local-ADK schema-dialect defect.
- **Afternoon:** Jonah merged the OMN-12834 fix (#219, `56d5a45`). Retest: fully-local `--agent` generation completes, PASS.

---

## Evidence table

| Phase | Command / Surface | Branch/SHA | Correlation ID | Transport | Model / Endpoint | Result | Classification |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0 Preflight | `pytest tests/unit -q` | dev/`80dc069` (AM) | n/a | n/a | n/a | 1385 pass / 3 fail / 1 skip | endpoint-resolution defect / env override ignored; not classified as no-regression |
| 1 Broker | aiokafka metadata (api 2.8.0) | dev/`27f4f03` | n/a | Tailscale | `100.109.203.94:39092` | advertises `100.109.203.94` (AM: `<onex-host>`) | **PROOF / cleared** |
| 2 SEA `--agent` (keyless) | `python -m src --agent` | dev/`27f4f03` | `1a7f6369-2399-4011-b2c6-d408207e4a89` | bus_backed | stability-test overlay | bus reached, no direct-mode fallback; typed stop at ADK backend `cloud_gemini` (no key) | **diagnostic** |
| 2b SEA `--agent` (local-ADK overlay, post OMN-12834 fix) | `python -m src --agent` | dev/`56d5a45` | `0cdc2410-3fa5-4da6-b561-52d4a7075352` | bus_backed | local `:8001`, no cloud ($0.00) | generated `status_health_checker`, registered as MCP tool + invoked → `{"is_healthy": true}`; PASS, attempts 1 | **PROOF (runtime-observed; not projection-backed — see verdict)** |
| 3 InferenceClient smoke | inline smoke | dev/`27f4f03` | `8c0ddf2a-762b-4365-b714-42ac73a0c3e5` | bus_backed | `Qwen3.6-27B-MTP-IQ4_XS.gguf` / `:8001` | resolver `24576`; `content healthy=true`; latency 2824ms; empty error | **PROOF** |
| 4 Projection API | `curl :13002` | n/a | `0cdc2410…` (queried) | read-only | projection API | `/health` ok; `generation_events` queried for our correlation → row_count 0 (run is local-capture, not bus-published — see verdict); plan's `hackathon_pipeline_events.v1` → 404 | **reachable**; run not projection-backed (by design) |
| 5 Evidence packet | docs review | n/a | n/a | n/a | n/a | `sea-final-runtime-e2e-20260608/` not in contractor checkout, not on dev/any SEA branch/OCC | **not accessible to contractor** (as the plan anticipated) |
| 6 Dashboard claims | observed surfaces | n/a | n/a | n/a | n/a | classified (table below) | safe / degraded / out-of-scope |
| 7 Progressive | `--agent --progressive` | n/a | n/a | n/a | n/a | not run — gated on a clean Phase 2; Phase 2 stops at ADK backend | **held** |

---

## Findings

**1. OMN-12832 — broker advertised LAN address (cleared).** AM: broker at `100.109.203.94:39092` accepted TCP over Tailscale but advertised `<onex-host>` in metadata, unreachable from our seat; every bus round-trip failed after the handshake. PM, after Jonah recreated the broker: the sanctioned aiokafka client (api 2.8.0) reports `brokers: [(0, '100.109.203.94', 39092)]`, and the Phase 3 round-trip — which had failed at `bus.start()` with `InfraTimeoutError` — now completes in 2.8s. Retest confirmation posted to the ticket.

**2. Phase 0 endpoint override defect (observed, not closed here).** The three unit failures are not a `model_registry.local.yaml` false alarm. `phase0-unit-tests.txt` shows endpoint resolution returning `http://100.109.203.94:8000/v1/chat/completions` where tests expected `localhost` or the `LOCAL_INFERENCE_BASE_URL_ENV` override. The failing `test_load_default_registry_honors_env_override` specifically verifies the env override path and shows it being ignored, so this report does not claim "no code regression" for Phase 0.

**3. OMN-12834 — local-ADK orchestrator backend sends Gemini-dialect tool schemas (new, medium).** Phase 2 keyless stops with "Select a local ADK tier in `contract.local.yaml`." Following that path (a contractor-created local overlay, since removed): the agent routes to the local `:8001` endpoint with no cloud key, then the local llama.cpp server returns HTTP 400 — `JSON schema conversion failed: Unrecognized schema: {"type":"STRING"}`. The request serializes every tool parameter in Gemini dialect (uppercase `STRING`/`OBJECT`); the OpenAI-compatible server requires lowercase JSON Schema. Confirmed it is the dialect: a hand-written lowercase tool returns 200 from the same endpoint; the agent's ADK-serialized tools return 400. Candidate site: `_OpenAICompatibleLlm` / `build_http_chat_completions_model` in `adk_backend_adapters.py`, which forwards the request without normalizing schema types for the OpenAI target. **Fixed same day:** Jonah merged #219 (`56d5a45`, adds `normalize_openai_tool_schema`). Retest from our seat: the 400 no longer reproduces and the fully-local generation completes (table row 2b; correlation `0cdc2410`).

**4. Phase 2 generation — runtime-proven fully local (not projection-backed).** After the OMN-12834 fix, the local-ADK orchestration path completes a full generation with no cloud (row 2b). Both the local *inference* path (Phase 3) and the local *orchestration* path (the agent loop) are runtime-proven from the contractor seat. The result is runtime-observed (terminal + local event-chain capture), not projection-backed: `--agent` records its generation events to a local file rather than publishing to the bus, so no `generation_events` projection row is created (root cause code-confirmed; see verdict and the Phase 4 transcript). Producing a projection row needs the server-side runtime path, which is out of the contractor seat's allowed scope. The default cloud-Gemini orchestrator path remains out of scope today (needs a key).

---

## Phase 6 — dashboard claim classification (omnidash dev `6b0a6b6`, file mode)

| Surface | Authority | Safe claim |
| --- | --- | --- |
| cost-savings-overview | fixture, provisioned, `captured_at` 2026-05-20 | safe with date disclosure |
| delegation-model-output (new this sprint) | fixture, `provisioned=false`, `captured_at` 2026-05-05 | degraded / disclosure only |
| most other delegation + cost widgets | fixture, no freshness metadata | degraded — do not present as live |
| delegation savings / model-routing / quality-gate / token-usage / control-plane | no fixture (OMN-12662 gap) | out-of-scope / no claim |
| cloud escalation, prod | gated / none today | not claimed |

Net: file mode is fixture-fallback-with-disclosure; the only date-backed cost surface is the savings overview.

---

## Phase 8 — answers to the 2026-06-06 feedback ledger

- **OMN-12719 (SubagentStop verifier):** no longer blocks the routine; agents stop cleanly. Did not re-patch; reporting observed behavior only.
- **OCC #2203:** merged 2026-06-07. Keyless run marked no-correlation/diagnostic; raw transcript annotated rather than rewritten (CodeRabbit's unsupported-ID finding corrected).
- **OMN-12700 cloud-tier:** not verified today (cloud out of scope). Post-deploy verification on v0.38.3 still showed the two-URL 404; filed OMN-12790 with the topic-binding hypothesis and source location.
- **OMN-12662 / cost widgets:** no cost/savings claims made; surfaces classified degraded or out-of-scope above.
- **Linear inbox digest:** ran (step 0); process hygiene, not evidence.

---

## Not verified today (explicit)

Prod deployment; cloud/Gemini/OpenRouter escalation; the full self-extension loop (generate → register → expose as MCP → invoke); receipt-honesty gate; URL authority remediation beyond observing sanctioned overlay values.

---

## Evidence

Transcripts in this folder: morning `phase0-unit-tests.txt`, `phase2-agent-e2e.txt`, `phase3-inference-smoke.txt`; afternoon `afternoon-retest-unblocked.txt`; ADK 400 capture `local-adk-400-capture.json`; post-fix fully-local proof `phase2-local-adk-PASS-postfix.txt`; projection readback + binding check `phase4-projection-readback.txt`.
