# W1-1B FINAL RUN (ATTEMPT 3): Delegation Golden Chain Proof Summary

**Date:** 2026-05-28
**Lane:** stability-test (<onex-host>)
**correlation_id:** `ec7466b9-ec90-405e-b1f9-3bddee05c806`
**Overall Result:** BLOCKED

## Deployment Context

| Item | Value |
|------|-------|
| Runtime health | healthy |
| Runtime containers uptime | Up 2 minutes (fresh redeploy) |
| omnibase_infra version | 0.37.2 |
| omnimarket version | 0.4.2 |
| PR #941 (OMN-12254) in image | YES — ESCALATING state + InfraAuthenticationError handling confirmed in handler_delegation_workflow.py |
| PR #944 (code_review enum) in image | NO — ModelDelegationRequest.task_type = Literal["test","document","research"] only |
| task_type used | research (valid enum value) |

## Chain Steps

- **1. Command envelope published to Kafka**: PASS
- **2. Routing decision received**: MISSING — No routing-decision event within timeout
- **3. LLM call effect completed**: MISSING — No inference-response/call-completed within timeout
- **4. Quality gate evaluated**: MISSING — No quality-gate-result within timeout
- **5. Escalation chain (optional)**: NOT_TRIGGERED — No escalation events (expected for happy path)
- **6. Terminal event received**: MISSING — No terminal event within timeout
- **7. Projection row written to Postgres**: MISSING — No projection row found (may still be in-flight)
- **8. Runtime identity captured**: PASS

## Key Values

- **quality_gate_passed:** None
- **Model selected:** N/A
- **Tier:** N/A
- **Tokens used:** N/A
- **Inference error:** N/A

## Gaps / Missing Surfaces

- 2. Routing decision received: not received within timeout (90.0s)
- 3. LLM call effect completed: not received within timeout (90.0s)
- 4. Quality gate evaluated: not received within timeout (90.0s)
- 6. Terminal event received: not received within timeout (90.0s)
- 7. Projection row written to Postgres: not received within timeout (90.0s)

## Runtime Error (Root Cause of BLOCKED)

```
ERROR omnimarket.nodes.node_delegation_orchestrator.wiring:
DelegationIntentBridge: routing intent failed: 'ModelRoutingIntent' object has no attribute 'min_tier_name'

  File ".../node_delegation_orchestrator/wiring.py", line 176, in _on_routing_intent
    await bridge.handle_routing_intent(intent)
  File ".../node_delegation_orchestrator/delegation_intent_bridge.py", line 122, in handle_routing_intent
    decision = routing_delta(intent.payload, min_tier_name=intent.min_tier_name)
AttributeError: 'ModelRoutingIntent' object has no attribute 'min_tier_name'
```

Fires 3 times (3 consumer group subscriptions on routing-request topic).

## Root Cause Analysis

PR #941 added `min_tier_name: str | None = Field(default=None, ...)` to `ModelRoutingIntent`
and updated `delegation_intent_bridge.py` to call `routing_delta(..., min_tier_name=intent.min_tier_name)`.

The `_parse_envelope_payload` in `wiring.py` does:
```python
raw = json.loads(message.value)
payload = raw.get("payload", raw)  # extracts ModelRoutingIntent dict from envelope
return model_class.model_validate(payload)
```

`model_validate` in isolation correctly handles `min_tier_name=None` (confirmed by test). However at runtime, the returned object raises `AttributeError` on `intent.min_tier_name`. This is a regression introduced by PR #941 — the pre-PR-941 image (run #2) did NOT call `intent.min_tier_name` and completed the full chain.

Likely cause: the `omnibase_compat` wheel in the built image contains a version of `ModelRoutingIntent` WITHOUT `min_tier_name`, while the `omnimarket` wheel (which contains `delegation_intent_bridge.py`) was updated to reference the new field. Version skew between packages within the same Docker image.

## What Run #2 Proved (pre-PR-941 image, for reference)

Correlation_id: `3ef21eb7-207e-4ec0-90f4-641858d16072`
- Full pipeline wiring: command → routing → LLM call → quality gate → terminal → projection
- Quality gate reducer fires; quality_gate_passed=false (score=0.0, 512-token truncation)
- Projection writes within 9ms
- GLM key provisioning works
- Bifrost routing selects local .200 tier
- Result: DEGRADED (quality gate failed, PR #941 not deployed at that time)

## Next Required Actions

1. Fix `min_tier_name` AttributeError: Ensure `omnibase_compat` wheel in the Docker image
   includes the updated `ModelRoutingIntent` with `min_tier_name`. The current image has
   a package version skew between `omnimarket` (reads the field) and `omnibase_compat` (defines it).
   Rebuild image with aligned package versions and redeploy.

2. After fix + redeploy: Re-run proof with `task_type=research`, `max_tokens=1024`.
   Research task class legacy quality gate requires only >60 chars + no refusal → should PASS.

## Evidence Artifacts

All artifacts in: `docs/evidence/delegation-golden-chain-proof-2026-05-28/`

| Artifact | File Status | Step Status |
|----------|-------------|-------------|
| command_envelope.json | written (this run) | PASS |
| routing_decision.json | stale (run #2) | MISSING (not received this run) |
| call_effect_result.json | stale (run #2) | MISSING |
| quality_gate_result.json | stale (run #2) | MISSING |
| escalation_chain.json | missing | NOT_TRIGGERED |
| terminal_event.json | stale (run #2) | MISSING |
| projection_row.json | stale (run #2) | MISSING |
| runtime_identity.json | written (this run) | PASS |
