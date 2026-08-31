# Verify-on-Terminal-Claim Recipes

Foreground operators (and agents) MUST verify every terminal claim with a literal probe before reporting completion. "Believe iff" defines the exact condition under which the claim is acceptable; anything else is a fail signal.

This runbook backs the rule "no terminal claim without a probe" — every entry below specifies:

- **Claim phrase** — the natural-language assertion the agent / operator made.
- **Probe command** — the literal shell command to run (no paraphrasing, no "verify it works").
- **Believe iff** — the exact match condition on probe output.
- **Fail signal** — what the probe returns when the claim is false.

Use these recipes when:
- A worker reports a PR merged, a deploy landed, tests pass, runners are healthy, etc.
- Ticking down a DoD checklist before marking a Linear ticket Done.
- Auditing prior session output (overseer, autopilot, build-loop, merge-sweep).

If a claim does not have a recipe row below, write one before accepting the claim.

---

## Recipes

| Claim | Probe command | Believe iff | Fail signal |
|---|---|---|---|
| "PR `<N>` merged" | `gh pr view <N> --repo OmniNode-ai/<repo> --json state,mergeCommit` | `state == "MERGED"` AND `mergeCommit.oid` non-null | `state` in (`OPEN`, `CLOSED`) OR `mergeCommit` null — claim is false; treat as un-merged. |
| "auto-merge enabled on PR `<N>`" | `gh pr view <N> --repo OmniNode-ai/<repo> --json autoMergeRequest` | `autoMergeRequest != null` | `autoMergeRequest == null` — auto-merge is NOT enabled; re-run `gh pr merge <N> --auto` (bare flag, no merge strategy). |
| "PR `<N>` CI green" / "test passing" | `gh pr checks <N> --repo OmniNode-ai/<repo>` (or with `--json` and parse) | every check `state == "SUCCESS"` (no `PENDING`, no `FAILURE`, no `CANCELLED`) | any row shows `FAILURE` / `CANCELLED` / `PENDING` (after `--watch` settle) — claim is false; do not report green. |
| "test passing" (local) | `cd <worktree> && uv run pytest <path> -v` | exit code `0` AND collected test count `> 0` AND no `xfail`/`skipped` for required cases | non-zero exit OR `collected 0 items` OR ImportError during collection — claim is false. |
| "deployed to the runtime host" | `ssh <user>@<onex-host> 'docker inspect <container> --format {{.Config.Image}}'` AND `ssh <user>@<onex-host> 'docker inspect <container> --format {{.State.Health.Status}}'` | image SHA matches the expected commit SHA AND `Health.Status == "healthy"` | image SHA is older than expected commit OR health is `starting` / `unhealthy` / empty — deploy did not land or container is unhealthy. |
| "runner pool healthy" | `gh api orgs/OmniNode-ai/actions/runners --paginate` AND `ssh <user>@<onex-host> 'docker ps --filter name=omninode-runner'` AND `ssh <user>@<onex-host> 'docker ps -a --filter name=omninode-runner'` | `online_count >= expected_count` AND no exited containers in `docker ps -a` AND no runner `busy_for_hours > N` | any runner `status != "online"` OR exited containers present OR runner stuck busy beyond threshold — pool is degraded; do not claim healthy. |
| "service healthy on the runtime host" | `curl -fsS http://<onex-host>:<port>/health` | exit code `0` AND response body contains the expected status field (e.g. `"status":"ok"` or `"healthy":true`) | non-zero exit (connection refused, timeout, 5xx) OR body lacks the expected status field — service is not healthy. |

---

## Notes

- **Never substitute a softer probe.** "I saw the PR page in the UI" is not a substitute for `gh pr view --json state,mergeCommit`. The terminal claim recipe is the literal command above.
- **`psql` is the canonical DB probe.** When a recipe needs DB-backed evidence (e.g. "row landed in projection table"), the probe is `psql -h <onex-host> -p 5436 -U postgres -d omnibase_infra -c "SELECT ..."`. Wire this into the relevant recipe row when the claim involves a projection / migration / ticket-store row.
- **Bare `gh pr merge --auto` only.** Per memory `feedback_merge_queue.md`, OmniNode-ai queue repos silently drop the queue entry if a strategy flag (`--squash` / `--merge` / `--rebase`) is passed. Recipe rows above intentionally use bare `--auto`.
- **`ssh <user>@<onex-host>`** is the canonical entry point for any container / host probe — never assume local Docker, never poll `localhost`.
- **Probes are deterministic.** If the probe is flaky (network blip, eventual consistency), re-run twice and require both to pass. Do not weaken the "Believe iff" condition.

---

## When a probe disagrees with the claim

1. The claim is the lie. The probe is the truth.
2. Record a friction event (`/onex:record_friction`) tagged with the claim source (worker name, skill, ticket).
3. Reopen the work — do NOT mark the ticket Done, do NOT merge, do NOT report green.
4. If two consecutive fix attempts fail to make the probe pass, follow Two-Strike Diagnosis Protocol — write `docs/diagnosis-<issue-slug>.md` and stop.
