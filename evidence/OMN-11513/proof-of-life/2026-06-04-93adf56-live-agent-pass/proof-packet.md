# Sanctioned `--agent` run after OMN-12664 closure — evidence packet

**Date:** 2026-06-04, 08:45–08:48 PT
**Classification: DIAGNOSTIC, not proof** (per the OMN-12664 verification-packet
requirement and the 2026-06-04 unified plan Workstream G: any missing packet
field means diagnostic). Missing fields are listed explicitly below.

**Context:** OMN-12664 (cloud-tier 404 — runtime drops the Gemini endpoint's
`/v1beta/openai` path) moved to Done at 2026-06-04 09:26 PT (fix:
omnimarket#1031; infra#1859 was reverted by #1863). Our standing trigger —
"when that fix lands: warm the broker, re-run `--agent`" — fired. This is that
re-run, from the contractor environment, on the sanctioned stability-test lane.

## Result summary

Observation on SEA dev `93adf56` (#206 at head; `main` reference `c04a4d8`):

A sanctioned live `--agent` run completed the full
**generate → validate → register → invoke** chain with `Result: PASS` for the
first time from this environment. A `SentimentClassifier` compute node was
generated, registered (hash `sha256:2a1014…7d33`), and invoked — first
invocation failed on malformed tool-input JSON (trailing `}`; "Extra data"
error), the agent self-corrected, second invocation returned
`{"sentiment": "positive", "confidence": 0.8}`. No `SeaGenerationError`, no
404, exit 0, 1 attempt, 61s latency, reported cost $0.0000.

The new fail-closed readiness path (SEA #206 / OMN-12665) produced a durable
artifact: probe `ready` in 3269ms, `final_transport_mode: bus_backed` —
the silent direct-mode downgrade hazard did not occur (`kafka-readiness-evidence.json`).

## What this run does NOT show

1. **It is not confirmation of the OMN-12664 fix.** Nothing in the run
   records the selected backend or final provider URL. SEA #205 (merged
   2026-06-03) wired the `--agent` scaffold to DelegationExecutor
   **local-first** routing, the local vLLM endpoint
   (`100.109.203.94:8000`, Qwen3.6-35B) was healthy, and reported cost is
   $0.0000 — consistent with the generation being served by the **local
   tier**, not the cloud tier through the runtime's inference effect. The
   Gemini-through-runtime path that OMN-12664 fixed was therefore likely
   **not exercised**. Inconclusive on OMN-12664 — not a pass, not a fail.
   (ADK *orchestration* did call Gemini directly from SEA with the
   contractor key — visible as google-genai SDK warnings — but that is not
   the runtime inference path the ticket covers.)
2. **The event chain is SEA-side bookends, not a runtime round-trip.** The
   captured chain (`event-chain.json`) holds exactly two events —
   `node-generation-requested` / `node-generation-completed` — both with
   `source_node: onex_agent_runner` (SEA itself), emitted **65µs apart**.
   No runtime-sourced event, no partition/offset, no inference
   request/response pair. This is the research-harness evidence shape the
   2026-06-04 unified plan (Workstreams B/G) says cannot be cited as live
   proof.

## Packet fields (per OMN-12664 "Verification packet required post-fix")

| Field | Status |
|---|---|
| SEA repo SHA + branch | ✅ dev `93adf56` (main ref `c04a4d8`) |
| Runtime image digest / hotpatch ID + container identity | ❌ not captured — not observable from contractor environment |
| Overlay path + hash + lane | ✅ `sea-delegation-bootstrap-stability-test@1.0.0`, lane `stability-test`, `sha256:7ba7c84e…2e78` (`overlay-evidence.json`) |
| Broker bootstrap | ✅ `100.109.203.94:39092` (warm probe: 1365 topics in 1.9s immediately prior) |
| Transport mode = bus-backed, not direct | ✅ `final_transport_mode: bus_backed`, probe `ready` 3269ms (`kafka-readiness-evidence.json`) |
| Fresh correlation ID | ✅ `d76664cb-1462-4728-8722-80d9dfc02e57` |
| Final provider URL incl. `/v1beta/openai/chat/completions` | ❌ not captured; cloud tier likely not exercised (local-first routing, $0.00 cost) |
| Non-empty generated node artifact or typed provider failure | 🟡 node generated, registered, invoked successfully — but registration is in-memory MCP; no on-disk node artifact found |
| Request/response topics with partition/offset | ❌ not captured — chain shows only the two SEA-sourced bookend events |
| Terminal bus event + projection state | ❌ not reached / not observable — bookends are SEA-emitted; projection DB not accessible from contractor environment |
| OCC durable evidence receipt | ❌ none (owner-side mechanism) |

## Artifacts in this folder

- `run.log` — full stdout incl. broker-warm probe, `[OVERLAY]` and
  `[READINESS]` lines, run summary, event-chain print
- `overlay-evidence.json` — overlay id/version/hash/lane/producer (SEA-written)
- `kafka-readiness-evidence.json` — #206 fail-closed readiness artifact
- `event-chain.json` — the captured 2-event chain for the correlation ID

## Invocation (for reproducibility)

Per `CONTRACTOR_SAFE_COMMANDS.md` §2, broker warmed immediately prior via
`confluent_kafka` AdminClient metadata probe:

```bash
env -u PYTHONPATH \
  KAFKA_BOOTSTRAP_SERVERS=100.109.203.94:39092 \
  KAFKA_API_VERSION=2.8.0 \
  SEA_DELEGATION_BUS_BOOTSTRAP=src/contracts/delegation_bootstrap_overlay.stability-test.yaml \
  SEA_LOCAL_INFERENCE_BASE_URL=http://100.109.203.94:8000 \
  ONEX_TRACK_A_API_KEY="$ONEX_TRACK_A_API_KEY" \
  .venv/bin/python -m src --agent
```

## Addendum (same day, after reading the #205 routing code)

Two refinements from reading `src/delegation/executor.py`,
`src/delegation/config.py`, `src/pipeline/inference.py`, and
`src/contracts/model_registry.{py,yaml,local.yaml}` at dev `93adf56`:

1. **Tier selection is an escalate-on-failure ladder, not a choice.** Tiers
   come from `model_registry.yaml` (`coding_primary` local Qwen →
   `reasoning_fallback` local DeepSeek → `cloud_fallback` Gemini); a tier
   that passes returns immediately (`executor.py:178`), and the cloud tier
   is reached only when both local tiers fail with retryable+escalatable
   errors. Today's run passed at tier 1 (local Qwen), so the cloud tier was
   never attempted — confirming the "inconclusive on OMN-12664" call above.
2. **The inference leg is designed to ride the bus to the runtime.**
   `src/pipeline/inference.py` replaces direct httpx calls with
   `publish_and_wait` on `TOPIC_DELEGATION_INFERENCE_REQUEST` (the
   overlay's bootstrap topic), meaning even local-tier generation should
   traverse the runtime's inference effect. This *refines* finding #2
   above: the captured chain holding only SEA-sourced bookends does not
   imply the run avoided the runtime — but no inference request/response
   envelopes, offsets, or runtime-sourced events were captured in evidence,
   so a runtime round-trip remains unproven for this run. The missing-field
   classification (diagnostic) is unchanged.

## Open questions this packet surfaces (observations, no disposition)

- Whether a contractor-side run can exercise the cloud tier at all under
  #205's local-first routing while the local endpoint is healthy — i.e.,
  what selects the cloud tier now. (Workstream E of the unified plan keeps
  Gemini "a valid selected backend, not a mandatory path"; selection
  mechanics from the contractor seat are not documented yet.)
- Whether runtime identity (image digest / hotpatch ID) can be made
  observable to the contractor environment, since the packet requires it
  and we cannot currently capture it.
