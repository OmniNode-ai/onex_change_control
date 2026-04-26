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

Foreground performs in this order:

- [ ] Every headline ticket's dod_evidence has at least one PASS receipt with `verifier ≠ runner`
- [ ] Receipts written to `drift/dod_receipts/<TICKET>/<ITEM_ID>/<check_type>.yaml` (e.g. `file_exists.yaml`, `command.yaml`); the aggregated `.evidence/<ticket_id>/dod_report.json` is a separate report and does not replace individual item receipts
- [ ] Receipts committed to onex_change_control via PR (one PR per session; title `chore(OMN-XXXX): session {{date}} adversarial DoD receipts`)
- [ ] `manual_count / total_count` recorded in handoff; computed as count of executor-table rows where `current_executor` starts with "manual" divided by total rows in the executor table
- [ ] broken-skill targets list updated: every row where current_executor != target_executor has a `tracking_ticket` populated; if any row has empty tracking_ticket, create the Linear ticket via `mcp__linear-server__save_issue` and fill in
- [ ] Handoff doc written to `docs/handoffs/{{date}}-session-handoff.md` referencing this session's runbook by absolute path
- [ ] `CronDelete <id>` for the 1hr tick (CronCreate is session-bound; cron must be removed before exit to avoid stale prompts)
- [ ] Memory updated: if any new doctrine emerged, write a `feedback_*.md` file and reference it from `MEMORY.md`
- [ ] Verifier identity recorded in handoff: which foreground session bound and verified the receipts
