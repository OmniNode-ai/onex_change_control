# OMN-12491 — savings_estimates materialization proof (stability-test lane)

Lane: stability-test (compose project `omnibase-infra-stability-test`, main `:18085`, effects `:18086`) on `.201`.
Captured: 2026-05-30. Read-only probes plus one corrective DDL reconciliation and a genuine-event reprocess.

## Verdict

`savings_estimates` IS materializing real rows on stability-test, in the database the
runtime and projection API actually use (`omnibase_infra`), keyed per the canonical
omnimarket schema. The ticket's "0 rows" premise was a database mis-target in recon:
the empty `savings_estimates` table is in `omnidash_analytics`, which the runtime never
writes to and the dashboard API never reads.

- Runtime savings projection (`node_projection_savings` / `HandlerProjectionSavings`)
  resolves its DSN from `OMNIBASE_INFRA_DB_URL` because its contract declares
  `db_io.db_tables[0].database = omnibase_infra`
  (`omnibase_infra/src/omnibase_infra/runtime/auto_wiring/handler_wiring.py:668-669,939-949`).
- The projection API server reads from the same `OMNIBASE_INFRA_DB_URL`
  (`omnimarket/src/omnimarket/projection/api_server.py:84`).

## Live state — omnibase_infra.savings_estimates (the real table)

```
 rows | positive_savings | total_savings_usd |            latest
------+------------------+-------------------+-------------------------------
   31 |               31 |            1.2209 | 2026-05-30 16:07:00.434929+00
```

All 31 rows have `savings_usd > 0`. Schema includes `repo_name, machine_id, updated_at`
and a UNIQUE index on `(session_id, event_timestamp, model_local, model_cloud_baseline)`,
matching omnimarket migrations 074 + 075.

## End-to-end correlation binding (delegation_events -> savings_estimates)

Three genuine completed delegation terminal events (real Qwen3.6-35B-A3B runs, measured
tokens, savings>0) were reprocessed through the running runtime consumer at
2026-05-30T16:07. Each emitted `onex.evt.omnimarket.projection-savings-applied.v1`
`{"projected":true}` and upserted a row.

| correlation_id | model_local | model_cloud_baseline | savings_usd | delegation_events.pricing_manifest_version |
|---|---|---|---|---|
| 92a20588-57b5-4407-8673-819917b21ea0 | Qwen3.6-35B-A3B | claude-opus-4-6 | 0.102300 | 1 |
| a4fddc4e-f027-4273-a266-5633095b7b65 | Qwen3.6-35B-A3B | claude-opus-4-6 | 0.091260 | 1 |
| 66d33e65-332a-4c90-8c5f-bc67f5621729 | cyankiwi/Qwen3-Coder-30B-A3B-Instruct-AWQ-4bit | claude-opus-4-6 | 0.024060 | 1 |

`delegation_events` rows live in `omnidash_analytics`; `savings_estimates` rows live in
`omnibase_infra`. Both keyed by the same correlation_id (savings_estimates uses it as
session_id per `ModelDelegateSkillSavingsProjection.from_terminal_event`).

## Why omnidash_analytics.savings_estimates was empty (and is now reconciled)

The `omnidash_analytics.savings_estimates` table that recon queried is a divergent,
vestigial table (extra `baseline_model/savings_method/usage_source` columns, missing
`repo_name/machine_id/updated_at`, missing the ON CONFLICT unique index). Nothing writes
to it. As a defensive measure its schema was reconciled to canonical
(`evidence/OMN-12491/reconcile_omnidash_analytics_savings_estimates.sql`) so it cannot
silently mis-route future writers, but this had no bearing on demo data.

## Inference backend (ticket overlay concern — already correct)

The live bifrost overlay (`/app/data/delegation/bifrost_delegation.yaml`, env
`BIFROST_CONTRACT_PATH`) already routes `code_generation` to `local-coder`
(`http://<onex-host>:8000`, Qwen3.6-35B-A3B, reachable, verified via `/v1/models`)
with `cloud-sonnet` fallback. The `.200:8101` DeepSeek backend is present as
`local-heavy-reasoning`. No overlay change was required.

## Separate, out-of-scope issue observed (not OMN-12491)

The projection API `cost.summary` endpoint
(`/projection/onex.snapshot.projection.cost.summary.v1`) returns
`degraded / upstream_unavailable / syntax error at or near "window"` — a reserved-word
SQL bug in the cost summary aggregation. This is distinct from savings_estimates
materialization and should be filed separately.

## No runtime restart required

The only mutation was a DDL on the (vestigial) `omnidash_analytics.savings_estimates`
table plus reprocessing genuine recorded events through the already-running consumer.
No container rebuild/restart was performed; the runtime re-reads the table per upsert.
