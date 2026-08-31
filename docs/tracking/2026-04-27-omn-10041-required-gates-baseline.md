# OMN-10041 — Required-Gates Rollout Baseline

**Date:** 2026-04-27
**Ticket:** [OMN-10041](https://linear.app/omninode/issue/OMN-10041)
**Goal:** Enroll Receipt-Gate (`verify / verify`) and CodeRabbit Thread Check (`gate / CodeRabbit Thread Check`) as required status checks on `main` across all 11 OmniNode-ai repos.

---

## Canonical context names (probe-verified, 2026-04-27)

The investigation source doc and the prompt referenced two slightly aspirational names. Live probes against `gh api repos/<repo>/commits/<sha>/check-runs` show the actual emitted contexts are:

| Gate | Actual emitted context | Aspirational context (do NOT require this) |
|---|---|---|
| Receipt Gate | `verify / verify` | `verify / Run Receipt-Gate` (a step name, not a check name) |
| CodeRabbit Thread Check | `gate / CodeRabbit Thread Check` | `gate / CodeRabbit Thread Check` (correct) |

Cross-reference: `omnibase_core/.github/workflows/receipt-gate.yml` line 11 says `Required status-check name: receipt-gate / verify`, also wrong. The truly emitted context is `verify / verify` (caller-job-id `verify` -> reusable-job-name `verify`). The receipt-gate workflow file's docstring has been wrong since at least 2026-04-25.

---

## Baseline (before)

| Repo | Receipt-Gate workflow exists? | Receipt-Gate context emitted on PR? | CR-Thread-Gate workflow exists? | CR-Thread context emitted on PR? | Currently required `contexts` |
|---|---|---|---|---|---|
| omniclaude | NO | n/a | YES (cr-thread-check.yml + caller) | YES `gate / CodeRabbit Thread Check` | `CI Summary`, `Security Gate` |
| omnibase_core | YES | YES `verify / verify` | YES (caller) | YES `gate / CodeRabbit Thread Check` | `CI Summary` |
| omnibase_infra | YES (calls remote omnibase_core) | YES `verify / verify` | YES (caller) | YES `gate / CodeRabbit Thread Check` | `CI Summary`, `Handler Contract Compliance` |
| omnibase_spi | NO | n/a | YES (caller) | YES `gate / CodeRabbit Thread Check` | `CI Summary` |
| omnidash | NO | n/a | YES (uses remote `omniclaude/cr-thread-gate.yml@main`) | NO (workflow registered but never fires; zero runs) | `CI Tests Gate`, `CI Summary`, `golden-chain` |
| omniintelligence | NO | n/a | YES (uses remote) | NO (zero runs) | `CI Summary` |
| omnimemory | NO | n/a | YES (caller) | YES `gate / CodeRabbit Thread Check` | `CI Summary` |
| omninode_infra | NO | n/a | YES (uses remote) | NO (zero runs) | `CI Summary` |
| omniweb | NO | n/a | YES (uses remote) | NO (zero runs) | `Build and push to ECR`, `CI Summary` |
| onex_change_control | NO | n/a | YES (uses remote) | YES `gate / CodeRabbit Thread Check` | `CI Summary`, `CodeQL / CodeQL Analysis (python)`, `gate / CodeRabbit Thread Check` (already required!) |
| omnibase_compat | NO | n/a | YES (caller) | YES `gate / CodeRabbit Thread Check` | `CI Summary` |

### Probe commands

```bash
# Required contexts:
gh api repos/OmniNode-ai/<repo>/branches/main/protection --jq '.required_status_checks.contexts'

# Actual emitted check names on latest PR head:
PR=$(gh pr list --repo OmniNode-ai/<repo> --state all --limit 1 --json number --jq '.[0].number')
SHA=$(gh pr view "$PR" --repo OmniNode-ai/<repo> --json headRefOid --jq '.headRefOid')
gh api "repos/OmniNode-ai/<repo>/commits/$SHA/check-runs" --paginate --jq '.check_runs[].name' | sort -u
```

---

## Rollout decisions

### Receipt-Gate (`verify / verify`)

Only **2 repos** currently emit this check: `omnibase_core` and `omnibase_infra`. The other 9 repos have no Receipt-Gate workflow installed. Branch protection cannot require a context that is never emitted — GitHub holds the PR forever waiting for it.

**Action:** require `verify / verify` on `omnibase_core` and `omnibase_infra` only. File a follow-up to land the Receipt-Gate workflow in the other 9 repos before requiring it there. Tracked as **OMN-10041 follow-up** (see "Follow-up tickets" below).

### CodeRabbit Thread Check (`gate / CodeRabbit Thread Check`)

Eligible (workflow fires + check emitted): omniclaude, omnibase_core, omnibase_infra, omnibase_spi, omnimemory, onex_change_control, omnibase_compat.

Workflow file present but doesn't fire (zero workflow runs in history; emits no check): omnidash, omniintelligence, omninode_infra, omniweb. All four use a remote `OmniNode-ai/omniclaude/.github/workflows/cr-thread-gate.yml@<ref>` reusable. The local `cr-thread-gate.yml` is a caller, not a worker — but the workflow has never registered runs. Likely cause: the reusable's `workflow_call` schema mismatch with how the caller passes secrets (e.g., `secrets: github-token:` vs `secrets: CROSS_REPO_PAT:`), or pipeline disabled by repo policy. **Out of scope for this ticket** — will not require there until the workflow actually fires.

**Action:** require `gate / CodeRabbit Thread Check` on the 7 eligible repos. File a follow-up ticket per non-emitting repo.

---

## Apply (after) — verified 2026-04-27

| Repo | Receipt-Gate required? | CR-Thread required? | Contexts added | Final required contexts |
|---|---|---|---|---|
| omniclaude | N | **Y** | `gate / CodeRabbit Thread Check` | `CI Summary`, `Security Gate`, `gate / CodeRabbit Thread Check` |
| omnibase_core | **Y** | **Y** | `verify / verify`, `gate / CodeRabbit Thread Check` | `CI Summary`, `verify / verify`, `gate / CodeRabbit Thread Check` |
| omnibase_infra | **Y** | **Y** | `verify / verify`, `gate / CodeRabbit Thread Check` | `CI Summary`, `Handler Contract Compliance`, `gate / CodeRabbit Thread Check`, `verify / verify` |
| omnibase_spi | N | **Y** | `gate / CodeRabbit Thread Check` | `CI Summary`, `gate / CodeRabbit Thread Check` |
| omnidash | N | N (workflow never fires) | (none) | `CI Tests Gate`, `CI Summary`, `golden-chain` |
| omniintelligence | N | N (workflow never fires) | (none) | `CI Summary` |
| omnimemory | N | **Y** | `gate / CodeRabbit Thread Check` | `CI Summary`, `gate / CodeRabbit Thread Check` |
| omninode_infra | N | N (workflow never fires) | (none) | `CI Summary` |
| omniweb | N | N (workflow never fires) | (none) | `Build and push to ECR`, `CI Summary` |
| onex_change_control | N | **Y** (already) | (none — already required) | `CI Summary`, `CodeQL / CodeQL Analysis (python)`, `gate / CodeRabbit Thread Check` |
| omnibase_compat | N | **Y** | `gate / CodeRabbit Thread Check` | `CI Summary`, `gate / CodeRabbit Thread Check` |

### Other protection fields (verified preserved on all touched repos)

`enforce_admins`, `required_linear_history`, `required_conversation_resolution`, `allow_force_pushes`, `allow_deletions`, `lock_branch` all unchanged from pre-apply state. Confirmed via post-apply probe.

### Operator note

One transient `unexpected end of JSON input` on the first POST against `omnibase_core` (CR-thread add). Retried once; succeeded. Not a Two-Strike — different repo would have a different attempt count, and this was a single retry that worked. Likely GitHub API hiccup.

### Apply mechanism

Used the additive contexts endpoint (preserves all other protection fields without us having to send the full protection JSON):

```bash
gh api -X POST "repos/OmniNode-ai/<repo>/branches/main/protection/required_status_checks/contexts" \
  --input - <<<'{"contexts":["<context-name>"]}'
```

The `POST` endpoint **appends** to `required_status_checks.contexts`. It does not touch `enforce_admins`, `required_linear_history`, `required_pull_request_reviews`, `required_conversation_resolution`, `allow_force_pushes`, `allow_deletions`, or any other field. Verified via dry-run on `omnibase_compat` before rolling out.

---

## Follow-up tickets

1. **Land Receipt-Gate workflow** in: omniclaude, omnibase_spi, omnidash, omniintelligence, omnimemory, omninode_infra, omniweb, onex_change_control, omnibase_compat. Each needs a `call-receipt-gate.yml` caller (omnibase_infra is a working template — uses `OmniNode-ai/omnibase_core/.github/workflows/receipt-gate.yml@main` reusable).
2. **Diagnose silent CR-thread-gate** on: omnidash, omniintelligence, omninode_infra, omniweb. All four have `cr-thread-gate.yml` registered but zero runs. Likely a `workflow_call` secrets schema mismatch (omnidash/omninode_infra/omniweb pass `secrets: CROSS_REPO_PAT:`, omniintelligence passes `with: github-token:`). Need to align all four with the reusable's expected secret name.
3. **Recurring drift check** for required-context coverage. A weekly job that diffs the live `required_status_checks.contexts` per repo against an expected canonical list, files a Linear ticket on drift. Per the "Enforcement, not detection" operating rule, should be a CI gate not an advisory sweep.
