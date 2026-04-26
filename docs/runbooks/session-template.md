# Session Runbook — {{date}}

> Canonical session runbook template (OMN-9781). Copy this file to
> `docs/runbooks/sessions/<YYYY-MM-DD>-<slug>.md` at session start, fill in
> the headline tickets, and append tick rows as cron fires.

## Session metadata
- session_id: <uuid>
- started_at: <iso8601>
- bound_by: foreground-claude
- mode: build | close-out | reporting

## Headline tickets
- OMN-XXXX: <one-line description>
  - source: linear | manual
  - contract_path: onex_change_control/contracts/OMN-XXXX.yaml

## DoD evidence cache
> Materialized from each headline ticket's contract `dod_evidence` and `evidence_requirements`.

| ticket | item_id | check_type | probe_command | expected_output |
|---|---|---|---|---|

## Per-step executor table

| step | current_executor | target_executor | tracking_ticket | notes |
|---|---|---|---|---|
| bind headline | manual: foreground reads contract YAML | /onex:set_session | OMN-YYYY | |
| initial probe | manual: foreground runs probe via Bash | /onex:dod_verify | OMN-YYYY | |
| 1hr tick | manual: CronCreate prompt | /onex:session orchestrator | OMN-YYYY | |
| verify-on-claim | manual: foreground re-probes | deterministic verify hook | OMN-YYYY | |
| session end | manual: foreground writes handoff | /onex:session --phase end | OMN-YYYY | |

## Tick log
> One row per cron tick. Foreground appends; receipts written to onex_change_control/drift/dod_receipts/.

| tick_at | items_probed | items_pass | items_fail | items_advisory | escalation |
|---|---|---|---|---|---|

## Session-end checklist
- [ ] Every headline ticket's dod_evidence has at least one PASS receipt with verifier ≠ runner
- [ ] Receipts committed to onex_change_control via PR
- [ ] Manual-step count + total-step count recorded in handoff
- [ ] Each manual step has a tracking_ticket linking to the broken-skill follow-up
- [ ] Cron deleted (CronCreate is session-bound; record IDs in this section before exit)
