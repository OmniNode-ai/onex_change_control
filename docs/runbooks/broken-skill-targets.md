# Broken-Skill Targets Runbook

**Epic:** OMN-9780 — Session-process runbook + adversarial DoD receipts (2026-04-26)
**Created:** 2026-04-27
**Purpose:** Track the 6 skills / targets whose `target_executor` flip the runbook depends on. Each row maps a manual runbook step to its automation target, records current state, and links the tracking ticket. The `manual_count / total_count` ratio must trend toward 0 for the runbook to be retired.

---

## Skill / Target Matrix

| skill / target | current_state | required_behavior | acceptance_criteria | tracking_ticket |
|---|---|---|---|---|
| `set_session` | `omniclaude.services.task_binding.TaskBinding` referenced in SKILL.md does not exist; `.onex_state/active_session.yaml` is never written automatically | `/onex:set_session OMN-XXXX` writes `task_id` to `.onex_state/active_session.yaml`, emits `session.status_changed`, is idempotent | `TaskBinding` importable; file written on invoke; `--clear` removes it; smoke test passes | OMN-10055 |
| `dod_verify` | `node_dod_verify` not registered in omnimarket node registry; `uv run onex run node_dod_verify` fails with NodeNotFound; receipts never written automatically | `/onex:dod_verify OMN-XXXX` runs all `dod_evidence[]` probes and writes adversarial receipts with literal `probe_command` + `probe_stdout` | Node registered; `onex run node_dod_verify` succeeds; receipt at `.evidence/{id}/dod_report.json`; smoke test passes | OMN-10056 |
| `session` | `/onex:session` shim dispatches to `node_session_orchestrator` but Phase 1 health gate silently exits without Phase 2/3 when Kafka unavailable; no session tick installed | Three-phase loop runs (health gate → RSD scoring → dispatch); on Kafka absence falls back to CronCreate tick with explicit log | Phases 1/2/3 complete; fallback CronCreate installed on Kafka failure; `--dry-run` produces structured output | OMN-10057 |
| `dod_sweep` | Skill documented and wired but per-ticket verify silently falls through to legacy `check_dod_compliance.py` when `node_dod_verify` absent; no adversarial receipts written | Batch-queries Linear, runs `dod_verify` per ticket, writes `ModelDodSweepResult` YAML to `drift/dod_sweep/{date}.yaml` | `node_dod_verify` registered (prerequisite); no legacy fallback; output YAML written; `--dry-run` smoke test passes | OMN-10058 |
| `launchd` | Launchd bundle (`omniclaude/scripts/launchd/`) installs four plists but they do not fire on the development Mac (confirmed broken 2026-04-26; diagnosis: `docs/diagnosis-2026-04-26-merge-sweep-launchd-bootstrap-fail.md`) | `ai.omninode.merge-sweep` and `ai.omninode.overseer-verify` plists fire on schedule, survive sleep/exit, install is idempotent | Root cause identified; `launchctl list | grep omninode` shows all 4 loaded; log entry confirmed after one interval; `--verify` exits 0 | OMN-10059 |
| `verify-on-claim` | No automated hook scans inbound `<teammate-message>` content for completion claims; verification is entirely manual | Inbound message scanner detects claim keywords, triggers verify-recipe from `docs/runbooks/verify-recipes.md`, writes receipt to `.evidence/{id}/verify-on-claim.json` (verifier ≠ claimant) | Hook exists; configurable keywords; receipt written on trigger; smoke test with synthetic message passes | OMN-10060 |

---

## Titration Metric

```
manual_count / total_count = 6 / 6 = 1.0  (as of 2026-04-27)
```

Target: `manual_count / total_count = 0.0`

Each time a tracking ticket above is completed and the runbook executor flips from `manual` to `skill` / `launchd` / `hook`, decrement `manual_count` and update this metric.

---

## Flip Protocol

When a tracking ticket above reaches Done:

1. Verify the acceptance criteria listed in the ticket are met (re-fetch the ticket; assert `completedAt` is set).
2. Update the `current_state` cell for that row to reflect the new working state.
3. Update the `manual_count` in the Titration Metric above.
4. If `manual_count == 0`, mark this runbook as **retired** and update OMN-9780.
