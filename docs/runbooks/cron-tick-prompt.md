# CronCreate tick prompt — session DoD probe

You are a foreground tick. Re-probe every dod_evidence item for every headline ticket bound to this session.

## Inputs
- Session runbook: `docs/runbooks/{{date}}-session.md`
- For each headline ticket: read `onex_change_control/contracts/<TICKET>.yaml`
- For each `dod_evidence[].checks[]`: extract probe_command (or fall back to `evidence_requirements[].command` if no executable check_type)

## Behavior

1. For each probe, execute the literal command via Bash. Capture literal stdout, exit_code, run_timestamp.
2. Write a receipt to `onex_change_control/drift/dod_receipts/<TICKET>/<ITEM_ID>/<run_timestamp>.yaml` with fields: ticket_id, evidence_item_id, check_type, check_value, probe_command, probe_stdout, exit_code, status (PASS|FAIL|ADVISORY), runner (current worker if any, else "manual"), verifier (this session's foreground identity), run_timestamp.
3. If verifier == runner, status downgrades to ADVISORY automatically.
4. Update the session runbook's tick log with one row.

## Silence rule

You are silent ONLY if every receipt this tick is PASS AND no new FAIL appeared since last tick. Any FAIL or new ADVISORY → escalate (write a single sentence to chat naming the ticket and the failing item).

## Active-drive lesson (F92)

- Terminal state for a dod_evidence item is PASS, not "queued" or "covered by worker"
- Do not trust agent self-reports of "merged" or "deployed" — verify before silent
- A PR self-reported as MERGED is unverified until the receipt's probe confirms `gh pr view --json state` returns MERGED on the actual PR
- "All items have a worker assigned" is NOT silent-justifying; only PASS receipts justify silence
- A receipt where verifier == runner is ADVISORY, not authoritative; treat as unverified
- A `check_type: file_exists` where check_value points at the receipt itself is tautological; flag the contract as needing stronger proof

## Output

Either: (a) silent (zero new FAIL/ADVISORY this tick), or (b) one-sentence escalation per failing item, or (c) on persistent FAIL across 2+ ticks, dispatch a fixer worker (TeamCreate + named worker) per the session's active dispatch policy.
