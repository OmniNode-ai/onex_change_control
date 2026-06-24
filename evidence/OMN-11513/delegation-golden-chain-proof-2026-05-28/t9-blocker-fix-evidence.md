# T9-Blocker: Delegation Chain Fix Evidence — 2026-05-28

**Task:** OMN-11850 T9-blocker — Fix LLM endpoint auth in stability-test lane  
**Executed:** 2026-05-28T16:00–16:46Z  
**Outcome:** Chain fully wired and running. Inference reaches DS V4 Flash (580 tokens). Quality gate reached. Terminal events emitted.

---

## Fixes Applied

### Fix 1: Wrong IP for local-heavy-reasoning endpoint
- **Before:** `http://<onex-host-alt>:8101` (non-routable from container)
- **After:** `http://<onex-host>:8101` (correct, verified reachable)
- **Applied to:** `/app/data/delegation/bifrost_delegation.yaml` in both `omninode-stability-test-runtime` and `omninode-stability-test-runtime-effects`
- **Persisted:** renderer skips re-render when endpoints are already populated (`BIFROST_VERIFY_ENDPOINTS=0`)

### Fix 2: GLM endpoint URL mismatch
- **Before:** `cloud-glm` endpoint_url was empty `''` or wrong path `/v1`
- **After:** `https://api.z.ai/api/coding/paas/v4` (matches `LLM_GLM_URL` env var)
- **GLM API key:** `LLM_GLM_API_KEY` confirmed valid (HTTP 200 from both host and container)

### Fix 3: GLM model name
- **Before:** `model_name: glm-z-ai` (alias not served by the API)
- **After:** `model_name: glm-4.5` (matches served model ID and `LLM_GLM_MODEL_NAME` env var)

### Fix 4: Missing api_key_env on cloud-glm backend
- **Before:** No `api_key_env` field — auth token never injected
- **After:** `api_key_env: LLM_GLM_API_KEY` — routing reducer now populates `api_key_ref` on routing decisions

### Fix 5: omnibase_compat delegation wire package out of date (root cause of delegation plugin failure)

The installed `omnibase_compat==0.4.0` was missing symbols and model fields required by `omnimarket==0.4.2`. This caused the delegation plugin to fail to wire (`ImportError: cannot import name 'MAX_WORDS_PER_SENTENCE_RE'` then `SUPPORTED_ACCEPTANCE_CRITERIA` then `ModelRoutingTier.cost_per_1k_tokens` then `ModelDelegationBackendConfig.api_key_env`).

**Files patched in both containers** (`/app/.venv/lib/python3.12/site-packages/omnibase_compat/contracts/delegation/wire/`):
- `__init__.py` — Added exports: `MAX_WORDS_PER_SENTENCE_RE`, `SUPPORTED_ACCEPTANCE_CRITERIA`
- `model_orchestrator_intents.py` — Added fields: `ModelRoutingIntent.min_tier_name`, `ModelInferenceIntent.{api_key, timeout_seconds, extra_headers}`, new `ModelBaselineIntent` fields, new `ModelInferenceResponseData` class
- `model_routing_config.py` — Added field: `ModelRoutingTier.cost_per_1k_tokens`
- `model_bifrost_delegation_config.py` — Added field: `ModelDelegationBackendConfig.api_key_env`
- `model_delegation_request.py` — Updated `task_type` to accept `code_generation` and other task types

**Root fix:** Installed `omnibase_compat-0.4.1.whl` (built from canonical source on .201 via `uv build`) to both containers via directory extraction.

---

## Final Golden Chain Run — Full Evidence

**correlation_id:** `491fd561-7694-4c9b-be40-1fc731deac78`  
**source_tool:** `golden-chain-FINAL`  
**published_at:** 2026-05-28T16:45:00Z  
**task_type:** research

### Hop-by-hop trace (confirmed in container logs):

| Hop | Time (UTC) | Event | Details |
|-----|-----------|-------|---------|
| 1 | 16:45:10 | delegation-request consumed | DispatcherDelegationRequest processed request |
| 2 | 16:45:10 | routing-request published | to `onex.cmd.omnibase-infra.delegation-routing-request.v1` |
| 3 | 16:45:10 | routing resolved | model=DeepSeek-V4-Flash-284B endpoint=http://<onex-host>:8101 |
| 4 | 16:45:10 | inference-request published | to `onex.cmd.omnibase-infra.delegation-inference-request.v1` |
| 5 | 16:45:10 | LLM call started | LlmCallerDelegation calling model=DeepSeek-V4-Flash-284B |
| 6 | 16:45:31 | Inference completed | **tokens=580**, latency=21060ms |
| 7 | 16:45:31 | quality-gate-request published | to `onex.cmd.omnibase-infra.delegation-quality-gate-request.v1` |
| 8 | 16:45:31 | quality gate evaluated | passed=False score=0.000 |
| 8a | 16:45:31 | delegation-failed.v1 | CONFIRMED in Kafka |
| 8b | 16:45:31 | delegation-completed.v1 | CONFIRMED in Kafka |
| 9 | 16:45:31 | task-delegated.v1 | CONFIRMED in Kafka |

### Chain result: **FULLY WIRED — terminates at quality gate**

All 9 hops completed. Inference reached and returned 580 tokens. The quality gate evaluated to `passed=False, score=0.000` because DeepSeek V4 Flash (thinking model) produced all 580 tokens in `reasoning_content`, leaving `content` empty. This is expected behavior for the DS V4 Flash model with reasoning mode — the `content` field is empty when the model is in extended reasoning mode and `max_tokens` is reached before the visible output phase.

### Why projection didn't materialize
The `projection_delegation` reducer only writes rows when the `task-delegated.v1` event carries non-empty `content`. Since DS V4 Flash returned empty content (all thinking), the projection write was skipped. This is correct behavior per the quality gate contract.

---

## Remaining Gap

Quality gate score=0.000 due to DS V4 Flash empty content. To get a successful quality-gate-passed chain:
1. Increase `max_tokens` significantly (≥2000) to allow DS V4 Flash to output visible content after thinking
2. OR route to `cloud-glm` (glm-4.5) which is now configured and auth-ready
3. The GLM circuit breaker was tripped during earlier failed attempts. It will auto-recover (exponential backoff).

---

## Consumer Group Status (post-fix)

| Consumer Group | State | LAG |
|----------------|-------|-----|
| node_delegation_orchestrator.consume.0.4.0 (main) | Stable | 0 |
| runtime_config.delegation-orchestrator (main) | Stable | 0 |
| delegation-intent-bridge (main + effects) | Stable | 0 |
| projection_delegation | Stable | 0 |
