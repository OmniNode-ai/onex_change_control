# Forced-escalation sanctioned `--agent` run — OMN-12664 path exercised

**Date:** 2026-06-04, 09:07–09:09 PT (16:07–16:09 UTC)
**Classification: DIAGNOSTIC, not proof** (missing packet fields listed below).
**Purpose:** the 2026-06-04 morning re-run (`../2026-06-04-omn12664-postfix-agent-run/`)
passed at the local tier, so the cloud-tier path that OMN-12664 fixed was never
attempted. This run forces the escalation ladder to the cloud tier so that path
is actually exercised from the contractor seat.

## Method (documented seams only)

SEA #205's ladder (`src/contracts/model_registry.yaml`) escalates only on
failure, so both local tiers were made unreachable for one run:

1. `src/contracts/model_registry.local.yaml` (gitignored contractor overlay)
   set aside for the duration of the run, restored immediately after.
2. `SEA_LOCAL_INFERENCE_BASE_URL=http://127.0.0.1:9` — the documented
   contractor seam (`model_registry.py` docstring) — rewrites both
   `local_vllm` entries to an unreachable origin (instant refusal).
3. Dry-check before the run confirmed the resolved ladder:
   `local_qwen_coder` and `local_deepseek_reasoning` → `http://127.0.0.1:9/...`,
   `cloud_gemini` → `https://generativelanguage.googleapis.com/v1beta/openai/chat/completions`, enabled.
4. Broker warmed (1365 topics, 2.2s), then the sanctioned invocation per
   `CONTRACTOR_SAFE_COMMANDS.md` §2 (stability-test lane, same overlay).

No bus fabrication, no direct produce, allowed command set, sanctioned lane.

## Observation

Observation on SEA dev `93adf56`, stability lane, 2026-06-04 16:09Z:

The run escalated to the cloud tier and failed fast with a typed
`SeaGenerationError` carrying the **same two-URL 404 signature observed
2026-06-03** (pre-fix, the original OMN-12664 filing):

```
model registry drift: inference effect reported model-not-found for
served_model_id 'gemini-2.5-flash'
at https://generativelanguage.googleapis.com/v1beta/openai/chat/completions;
error_message="Client error '404 Not Found' for url
'https://generativelanguage.googleapis.com/v1/chat/completions'"
```

- **Registered endpoint:** `…/v1beta/openai/chat/completions`
- **URL actually called (404):** `…/v1/chat/completions`
- The `/v1beta/openai` path segment is still dropped between registration and
  the HTTP call, as observed from this seat.

Timing context: OMN-12664 moved to Done at 2026-06-04 09:26 UTC (02:26 PT);
this run is ~6.7h later. `Result: FAIL`, latency 16.7s, reported cost $0.00,
exit 0 (typed failure, not a crash). Run correlation
`fa8daa9e-15aa-4d7c-ac24-28f7863b32d7`; executor correlation
`aab65120-cce6-4744-82de-e81f8a3c0b3e`.

## What this run additionally shows

1. **The runtime round-trip is live from this seat.** The typed error was
   *reported by the runtime's inference effect* and came back over the bus
   (`transport=bus_backed`, readiness probe 4808ms, durable artifact). The
   full chain — SEA → stability bus → runtime → provider HTTP attempt →
   typed error → bus → SEA fail-fast — executed; the URL defect is the one
   observable break, same as 2026-06-03.
2. **The escalation ladder works from the contractor seat.** Local tiers
   unreachable → cloud tier attempted, per the #205 design.
3. **SEA evidence gap (observation):** the executor's delegation events
   (attempt_started/escalation/completed) are not persisted when
   `scaffold_onex_node` raises `SeaGenerationError` — the raise path drops
   `delegation_result.events`, so per-tier escalation evidence is absent
   from durable artifacts. (Relevant to unified-plan Workstream G evidence
   discipline.)

## Interpretation boundary (no disposition)

This seat **cannot distinguish** between:

- **(a) the fix is not deployed** to the stability runtime — omnimarket#1031
  merged to `dev` today; whether the stability-lane runtime container runs
  that code is not observable from here (runtime identity field below is
  missing); and
- **(b) the fix is not on the live inference path** — context from the
  ticket's own attachments and the 2026-06-04 unified plan: the infra-side
  fix PR #1859 (`handler_bifrost_gateway`) was merged then **reverted by
  #1863**; the surviving fix #1031 touches
  `handler_generation_consumer.py`, which the unified plan's own audit
  classifies as a **dead node** ("subscribe topic has zero producers");
  the plan's ground-truth row already classifies the endpoint path as
  "Not architecture-complete; supersede before promotion" (Workstream D).

Both readings are consistent with this observation. Distinguishing them
requires runtime-side facts (deployed image/SHA, which handler served the
inference request) that only the owner side can capture.

## Packet fields

| Field | Status |
|---|---|
| SEA repo SHA + branch | ✅ dev `93adf56` |
| Runtime image digest / hotpatch ID + container identity | ❌ not observable from contractor environment |
| Overlay path + hash + lane | ✅ `sea-delegation-bootstrap-stability-test@1.0.0`, lane `stability-test`, `sha256:7ba7c84e…2e78` |
| Broker bootstrap | ✅ `100.109.203.94:39092` (warm probe 2.2s prior) |
| Transport mode = bus-backed | ✅ `final_transport_mode: bus_backed`, probe ready 4808ms (`kafka-readiness-evidence.json`) |
| Fresh correlation ID | ✅ `fa8daa9e…` (run) / `aab65120…` (executor) |
| Selected backend + final provider URL | ✅ `cloud_gemini`; registered `…/v1beta/openai/chat/completions`, called `…/v1/chat/completions` (from the typed error) |
| Non-empty generated node artifact **or typed provider failure** | ✅ typed `SeaGenerationError`, full text in `run.log` |
| Request/response topics with partition/offset | ❌ not captured |
| Terminal bus event + projection state | ❌ not captured / not observable from contractor environment |
| OCC durable evidence receipt | ❌ none (owner-side mechanism) |

## Artifacts in this folder

- `run.log` — full stdout incl. dry-check banner, `[OVERLAY]`/`[READINESS]`
  lines, the complete `SeaGenerationError` traceback, run summary
- `overlay-evidence.json`, `kafka-readiness-evidence.json` — SEA-written
- `event-chain.json` — captured chain (SEA bookend events only)

State restored after the run: `model_registry.local.yaml` back in place
(verified), no repo changes.
