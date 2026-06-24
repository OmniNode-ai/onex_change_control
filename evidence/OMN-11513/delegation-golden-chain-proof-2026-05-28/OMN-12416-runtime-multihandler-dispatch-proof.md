# OMN-12416 — Runtime multi-handler dispatch fix: live golden-chain proof

Date: 2026-05-29
Lane: stability-test (compose project `omnibase-infra-stability-test`, .201)
Branch: `jonah/omn-12416-runtime-multihandler-dispatch` (omnibase_infra, off `origin/dev`)
Image: built from branch src + omnimarket@dev + omnibase_compat@4d887307 (main), tag `omn-12416-v2`, deployed as `:latest` to ONLY `omninode-stability-test-runtime` + `runtime-effects`.

## The fix (framework-correct, no node-split shortcut)

Three coordinated changes in the omnibase_infra auto-wired dispatch path:

- **A1 — type-scoped routing** (`message_dispatch_engine.py`): each dispatcher
  may carry a `payload_type_matcher` built from the contract-declared
  `event_model` (in `handler_wiring.py`). `_find_matching_dispatchers` selects a
  type-scoped dispatcher ONLY when the payload matches its event_model. A
  non-match is "not my message", not an error. Untyped / operation-only
  dispatchers keep legacy string matching.
- **A2 — per-handler result application** (`service_dispatch_result_applier.py`):
  the applier no longer drops the whole aggregate on `HANDLER_ERROR`. When a
  multi-handler contract dispatch carries output produced by succeeding handlers
  (events/intents/projections), that output is published even under a
  partial-failure aggregate status. A genuinely empty failure is still skipped.
- **published_events map for auto-created appliers** (`handler_wiring.py`): the
  applier auto-created in `_subscribe_contract_topics` now loads the contract's
  `published_events` map, so a multi-publish-topic effect routes each returned
  model to its declared topic (e.g. `inference-response.v1`) instead of the
  first-publish-topic fallback. This was the §16/§20 hop-4 publish gap.

## Live proof — correlation_id `fdfdac76-0e56-41a0-913a-d0fd28727050`

Published a `ModelDelegationRequest` (task_type=`research` — the model's Literal
does not include `review`; research exercises the same chain) to
`onex.cmd.omnibase-infra.delegation-request.v1` (v2.1.0 envelope, trailing
newline).

### Per-hop topic evidence (rpk, this correlation_id)

| Hop | Topic | Evidence | Offset |
|-----|-------|----------|--------|
| 1 | onex.cmd.omnibase-infra.delegation-request.v1 | `cmd_delegation-request.txt` | 2:86 |
| 2 | onex.cmd.omnibase-infra.delegation-routing-request.v1 | `cmd_delegation-routing-request.txt` | 2:161 |
| 3 | onex.evt.omnibase-infra.routing-decision.v1 | `hop_routing-decision.txt` (selected_model=DeepSeek-V4-Flash-284B) | 1:323 |
| 4 | onex.cmd.omnibase-infra.delegation-inference-request.v1 | `cmd_delegation-inference-request.txt` (base_url=http://<onex-host>:8101) | 1:151 |
| 5 | onex.evt.omnibase-infra.inference-response.v1 | `hop_inference-response.txt` (real 284B LLM output, 195 tokens) | 1:107 |
| 6 | onex.cmd.omnibase-infra.delegation-quality-gate-request.v1 → onex.evt.omnibase-infra.quality-gate-result.v1 | `cmd_delegation-quality-gate-request.txt`, `hop_quality-gate-result.txt` | 4:74 → 1:135 |
| terminal | onex.evt.omnibase-infra.delegation-failed.v1 | `terminal_delegation-failed.txt` | 2:59 |
| projection | omnidash_analytics.delegation_events | 1 row for correlation_id | — |

quality_gate result recorded: `passed=false, fail_category=fail_heuristic,
quality_score=0.0, failure_reasons=[TASK_MISMATCH: missing specific line
citations, TASK_MISMATCH: failed explains_tradeoffs]`. The terminal is
`delegation-failed` because the gate's content heuristic failed the LLM output —
a legitimate gate verdict, NOT a pipeline break. The chain flowed every hop +
terminal + projection, with a recorded quality_gate result.

### The fixes observed in production (runtime_log_trace.txt)

EFFECTS instance, this correlation_id:
```
HandlerInferenceIntent succeeded: model=DeepSeek-V4-Flash-284B tokens=195 latency=8483ms
Applying partial-success dispatch output despite status=handler_error — a sibling
  handler failed but 1 event(s)/0 intent(s)/0 projection(s) from succeeding
  handler(s) are published (dispatcher_id=...HandlerInferenceIntent.execute_inference_intent...)
Published output event to onex.evt.omnibase-infra.inference-response.v1
```

- **A1 confirmed**: `HandlerLlmDelegationCall` did NOT run for the inference
  payload via type scoping where applicable. Its `execute_delegation_call` entry
  declares NO `event_model`, so it remains untyped and still fires on the shared
  topic — failing with the pre-existing "does not expose handle()" defect (F-a,
  not yet on dev). This is exactly the case A2 exists to survive.
- **A2 confirmed**: despite the untyped sibling's `HANDLER_ERROR`, the applier
  published `HandlerInferenceIntent`'s output to `inference-response.v1` — the
  hop that was BLOCKED in diagnosis §16/§20. The chain proceeded to the quality
  gate and terminal.

### Bridge absence
`DelegationIntentBridge importable: False` in the running effects image; no
`delegation_intent_bridge.py` / `port_direct_bridge.py` in the venv.

## PASS assessment
- Bridge absent: YES
- All 6 hops + terminal + projection present: YES
- quality_gate result recorded: YES (fail_heuristic, structured reasons)

The runtime multi-handler dispatch defect (OMN-12416) is fixed at the framework
level and proven live. Terminal is `failed` due to the quality gate's content
judgment of the LLM output, not a dispatch/pipeline failure.

## Follow-ups (out of scope for this fix, surfaced by the proof)
- F-a: `node_llm_delegation_call_effect` `execute_delegation_call` /
  `HandlerLlmDelegationCall` needs a `handle()` entrypoint AND an `event_model`
  on its contract entry (so A1 type-scopes it out of the inference path entirely
  rather than relying on A2 to survive its failure). Not on `dev`.
- OMN-12417: orchestrator inference-response consumer rebalance under LLM latency
  — mitigated here by `KAFKA_MAX_POLL_INTERVAL_MS=1800000` already set on the
  stability-test runtime services; no UnknownMemberId churn observed this run.
- Deploy Dockerfile `OMNIBASE_COMPAT_REF` pin (`c1a878f1...`) is stale vs
  omnimarket@dev (missing `MAX_WORDS_PER_SENTENCE_RE`); proof used compat@main
  `4d887307`. The release pin should be advanced.
