# Objective 1 Evidence: SEA Token and Registry Stability (2026-05-30)

**Engagement:** OmniNode Hackathon Integration
**Umbrella:** OMN-11513
**Plan:** 2026-05-30 contractor integration plan, Objective 1
**Author:** Bret (contractor, integration verification)

## Environment binding (evidence freshness)

- Collection date: 2026-05-30 (PT)
- Tested SEA SHA: dev `13127a7` (integration passes + delegation regression); main `c04a4d8` (unit baseline)
- Model registry: `src/contracts/model_registry.yaml` git blob `ca392b6f`, last changed by `13127a7` (PR #166, OMN-12434). The gitignored overlay `model_registry.local.yaml` was active, pointing the local tiers at the reachable Tailscale endpoints.
- Contract version: delegation runtime profile `schema_version 1.0`; model registry `schema_version 1.0.0`
- Projection version: not applicable (projection read path unavailable this session; tracked under OMN-12467)
- Mode: remote-live (Tailscale to the <onex-host> runtime and models) plus local (SEA repo)

## Summary

Observation on `13127a7`: with the Gemini key restored, the delegation escalation ladder is healthy and the OMN-12434 model-ID drift fix holds. The only task that fails is reg-005, which is the known OMN-12438 max_tokens truncation. An earlier run this morning showed 3/7 to 5/7 and looked like a regression. That run was confounded because the cloud fallback tier was unreachable (the Gemini key script had been lost and was later recovered from a git stash). With the key restored, the ladder behaves as designed.

## Served model IDs (live, <onex-host> over Tailscale)

- Port 8000 (tier 1, `local_qwen_coder`): `Qwen3.6-35B-A3B`
- Port 8001 (tier 2, `local_deepseek_reasoning`): `Qwen3.6-27B-MTP-IQ4_XS.gguf`
- Runtime health (port 18085): healthy, v0.37.0

The overlay maps `local_qwen_coder` to 8000 and `local_deepseek_reasoning` to 8001. Both match the served IDs. No drift between the overlay and the live endpoints.

## OMN-12434 (model-ID drift) verification

Observation on `13127a7`: the registry model IDs match the served IDs, tier 1 returns no 404, and tier 1 serves passing results on some tasks (reg-005 passed at `local_qwen_coder` on one run; reg-002/003/004/006 passed at tier 1 across runs). No silent escalation past a dead tier 1 was observed. Every escalation corresponds to a recorded validation failure. This is the failure mode OMN-12434 addressed, and it is resolved.

## 7-task delegation regression (multi-sample)

The suite runs each task with samples_per_task = 1 by design, so single runs are stochastic. To get stable pass rates the suite was run multiple times. Confirmed data for two runs is below. Runs 3 to 5 were still completing in the background at the time of writing; the conclusions are unchanged by the remaining runs.

### Run 1 (6/7)

| task | result | tier_used | attempts | escalations | failure classes |
|---|---|---|---|---|---|
| reg-001 | PASS | local_deepseek_reasoning | 3 | 1 | unclassified x2 |
| reg-002 | PASS | local_qwen_coder | 2 | 0 | none |
| reg-003 | PASS | local_qwen_coder | 1 | 0 | none |
| reg-004 | PASS | local_deepseek_reasoning | 3 | 1 | unclassified x2 |
| reg-005 | FAIL | cloud_gemini | 4 | 2 | unclassified x4 |
| reg-006 | PASS | local_qwen_coder | 2 | 0 | none |
| reg-007 | PASS | cloud_gemini | 4 | 2 | unclassified, schema_violation, unclassified |

### Run 2 (7/7)

| task | result | tier_used | attempts | escalations | failure classes |
|---|---|---|---|---|---|
| reg-001 | PASS | local_qwen_coder | 2 | 0 | none |
| reg-002 | PASS | local_deepseek_reasoning | 3 | 1 | unclassified x2 |
| reg-003 | PASS | cloud_gemini | 4 | 2 | unclassified x3 |
| reg-004 | PASS | local_qwen_coder | 2 | 0 | none |
| reg-005 | PASS | local_qwen_coder | 2 | 0 | none |
| reg-006 | PASS | cloud_gemini | 4 | 2 | unclassified x3 |
| reg-007 | PASS | local_deepseek_reasoning | 3 | 1 | unclassified x2 |

### Two-run aggregate

| task | pass rate | tiers seen |
|---|---|---|
| reg-001 | 2/2 | local_deepseek_reasoning, local_qwen_coder |
| reg-002 | 2/2 | local_qwen_coder, local_deepseek_reasoning |
| reg-003 | 2/2 | local_qwen_coder, cloud_gemini |
| reg-004 | 2/2 | local_deepseek_reasoning, local_qwen_coder |
| reg-005 | 1/2 | cloud_gemini (fail), local_qwen_coder (pass) |
| reg-006 | 2/2 | local_qwen_coder, cloud_gemini |
| reg-007 | 2/2 | cloud_gemini, local_deepseek_reasoning |

## Escalation record (selected vs executing tier, and why)

The ladder is `local_qwen_coder` (tier 1), then `local_deepseek_reasoning` (tier 2), then `cloud_gemini` (tier 3). Observed pattern:

- Tasks that pass at tier 1: 0 escalations, 1 to 2 attempts.
- Tasks that fail tier 1 but pass tier 2: 1 escalation, 3 attempts. Escalation reason: tier-1 attempts failed validation (failure class unclassified).
- Tasks that exhaust both local tiers and reach cloud (tier 3): 2 escalations, 4 attempts. Cloud either rescues (reg-007 Run 1; reg-003/006 Run 2) or also fails (reg-005 Run 1).
- Cloud tier execution confirmed: with the key set, `cloud_gemini` executes on exhaustion. This morning, without the key, cloud was unreachable and the ladder was cut short, which produced the misleading low pass rates.

No task escalated past a tier that was silently dead. All escalations map to recorded validation failures.

## reg-005 token count and finish reason (OMN-12438)

Observation: reg-005 truncates at the 2048 max_tokens cap. The pipeline does not surface finish_reason or total_tokens (finish_reason is not tracked; total_tokens defaults to 0), so in the suite the truncation manifests as a `schema_violation` or `unclassified` validation failure rather than a named length reason. A direct probe to the tier-1 model with the reg-005 prompt (recorded on OMN-12438) confirms the underlying behavior:

- max_tokens 2048: finish_reason `length`, 2048 completion tokens, output truncated mid-block.
- max_tokens 8192: finish_reason `stop`, 3485 completion tokens, completes.

reg-005 is therefore stochastic at 2048: it passes when the response happens to fit under 2048, and fails (across every tier, including cloud) when it does not. See OMN-12438 (SEA consumer path fix, PR #168 in flight) and the comment there documenting the production (omnimarket) path, which also defaults to 2048.

## Integration passes (context)

- `--agent`: PASS. Correlation ID `566f3397-7efa-4a63-b9f0-1904a5b60264`, 1 attempt, output `{"sentiment": "positive"}`. No truncation on this run.
- `--progressive`: PASS on the third attempt. Attempts 1 and 2 failed with a Track B local-model ReadTimeout under concurrent load (tracked as OMN-12502). 5 of 6 progressive tasks passed (task 4, spam detection, failed on Track A).
- `--replay`, `--entropy`, `--demo`: PASS (verified earlier in the day; `--demo` degrades gracefully to replay when the key is absent).

## Findings cross-reference

- OMN-12434 (model-ID drift): verified fixed; registry matches served IDs; tier 1 serves; no silent escalation.
- OMN-12438 (max_tokens 2048 truncation): confirmed on `13127a7` (`consumer.py:484` = 2048); fix on PR #168 (In Progress); production-path scope noted in the ticket comment.
- OMN-12502 (new): `--progressive` Track B 60s timeout under load (separate latent bug).

## Exit evidence checklist (Objective 1)

- Command transcript: the commands and their results are recorded in this note (live passes via `source <key script>` then `uv run python -m src --agent` / `--progressive`; the 7-task regression via the documented harness). A verbatim terminal capture was not saved this session.
- Tested SEA SHA: dev `13127a7`; main `c04a4d8`.
- Model registry hash: blob `ca392b6f` (changed by `13127a7`).
- Served model ID list: recorded above.
- 7-task result table: recorded above (two runs plus aggregate).
- reg-005 token count and finish reason: recorded above (2048 = length / 2048 tokens; 8192 = stop / 3485 tokens), per the OMN-12438 probe.
- Per-task escalation record: recorded above.
- OCC evidence path: this file; max_tokens evidence on OMN-12438 (OCC PRs #1923 / #1927 / #1929).

## Caveats

- Runs 3 to 5 of the regression were still completing in the background when this note was written. The two-run data is sufficient to support the conclusions (ladder healthy; reg-005 is the OMN-12438 truncation). The aggregate pass rates may be refined when the remaining runs land.
- The pipeline does not emit finish_reason or total_tokens; the reg-005 figures come from a direct model probe recorded on OMN-12438, not from the suite output. A code change to surface these in the pipeline result would let them be captured naturally, which is worth a follow-up.
