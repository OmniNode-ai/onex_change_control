# Required-Gates Rollout — 2026-04-27 (OMN-10041)

**Ticket:** [OMN-10041](https://linear.app/omninode/issue/OMN-10041)
**Goal:** Stop "Receipt-Gate + CodeRabbit Thread Check are advisory" by enrolling them as required status checks on `main` across all 11 OmniNode-ai repos.
**Source investigation:** `omni_home/docs/tracking/2026-04-27-merge-gate-bypass-investigation.md`
**Operator note:** baseline + apply log lives at `omni_home/docs/tracking/2026-04-27-omn-10041-required-gates-baseline.md`.

---

## Canonical context names (probe-verified)

The investigation source doc and the prompt that triggered this rollout both used slightly aspirational context names. Live probes via `gh api repos/<repo>/commits/<sha>/check-runs` show the actual emitted contexts are:

| Gate | Actual context | Notes |
|---|---|---|
| Receipt Gate | `verify / verify` | Caller workflow `Receipt Gate` (`call-receipt-gate.yml`) has job `verify:` that uses reusable `receipt-gate.yml` whose job is also `verify`. The reusable's docstring claims `receipt-gate / verify` — that is wrong; the truly emitted context is `verify / verify`. |
| CodeRabbit Thread Check | `gate / CodeRabbit Thread Check` | Caller workflow has job `gate:` that uses reusable `cr-thread-gate.yml` whose job is named `CodeRabbit Thread Check`. |

---

## Final state per repo

| Repo | Receipt-Gate required? | CR-Thread required? | Final required `contexts` | Why partial? |
|---|---|---|---|---|
| omniclaude | NO | YES | `CI Summary`, `Security Gate`, `gate / CodeRabbit Thread Check` | No Receipt-Gate workflow installed |
| omnibase_core | YES | YES | `CI Summary`, `verify / verify`, `gate / CodeRabbit Thread Check` | full coverage |
| omnibase_infra | YES | YES | `CI Summary`, `Handler Contract Compliance`, `gate / CodeRabbit Thread Check`, `verify / verify` | full coverage |
| omnibase_spi | NO | YES | `CI Summary`, `gate / CodeRabbit Thread Check` | No Receipt-Gate workflow installed |
| omnidash | NO | NO | `CI Tests Gate`, `CI Summary`, `golden-chain` | CR-thread workflow registered but never fires (zero runs); Receipt-Gate not installed |
| omniintelligence | NO | NO | `CI Summary` | same as omnidash |
| omnimemory | NO | YES | `CI Summary`, `gate / CodeRabbit Thread Check` | No Receipt-Gate workflow installed |
| omninode_infra | NO | NO | `CI Summary` | same as omnidash |
| omniweb | NO | NO | `Build and push to ECR`, `CI Summary` | same as omnidash |
| onex_change_control | NO | YES (already) | `CI Summary`, `CodeQL / CodeQL Analysis (python)`, `gate / CodeRabbit Thread Check` | No Receipt-Gate workflow installed; CR-thread already required pre-rollout |
| omnibase_compat | NO | YES | `CI Summary`, `gate / CodeRabbit Thread Check` | No Receipt-Gate workflow installed |

**Coverage:** 7/11 repos now require CR-Thread; 2/11 require Receipt-Gate. This is **as much enforcement as is currently safe.** Requiring a context that is never emitted by the workflow set on a repo would deadlock every PR (GitHub waits for the check forever).

---

## Apply mechanism

Used the additive REST endpoint:

```bash
gh api -X POST "repos/OmniNode-ai/<repo>/branches/main/protection/required_status_checks/contexts" \
  --input - <<<'{"contexts":["<context-name>"]}'
```

This appends to `required_status_checks.contexts` without mutating any other field (`enforce_admins`, `required_linear_history`, `required_pull_request_reviews`, `required_conversation_resolution`, `allow_force_pushes`, `allow_deletions` all preserved). Verified on `omnibase_compat` first as a single-repo dry-run, then rolled out.

One transient `unexpected end of JSON input` on the first POST against `omnibase_core` (CR-thread context). Retried once; succeeded. All other 9 PATCH operations succeeded first try.

---

## Follow-ups (not in this PR)

1. **Land Receipt-Gate workflow** in 9 repos (omniclaude, omnibase_spi, omnidash, omniintelligence, omnimemory, omninode_infra, omniweb, onex_change_control, omnibase_compat). Once each has a working `call-receipt-gate.yml` (omnibase_infra is a working template), require `verify / verify` per repo. Each of these is a separate ticket because each repo has different runtime context (e.g., omniweb is PHP).
2. **Diagnose silent CR-thread-gate** on omnidash, omniintelligence, omninode_infra, omniweb. Workflow files exist and are registered, but zero workflow runs in history. Likely a `workflow_call` `secrets:` schema mismatch (callers pass `secrets: CROSS_REPO_PAT:` but the reusable in omniclaude may expect `secrets: github-token:` — see omniintelligence using `with: github-token:` instead). Once the workflow fires reliably and emits `gate / CodeRabbit Thread Check`, require it on those 4 repos.
3. **Recurring drift check** — a CI gate (per "Enforcement, not detection" operating rule) that diffs the live `required_status_checks.contexts` against an expected canonical list per repo and fails if drift detected. Belongs in this repo's `contracts/` + a workflow.
4. **Rename `verify / verify` to a less generic context name** — Gemini hostile-review flagged the bare `verify` job name as collision-prone with unrelated workflows. Mitigated short-term by branch protection requiring the *exact* `verify / verify` literal (no fuzzy match), but the receipt-gate workflow's own docstring (`receipt-gate / verify`) shows the team intended a more specific name. Follow-up: rename caller job from `verify:` to `receipt-gate:` in `call-receipt-gate.yml` across both repos that emit it; coordinate the branch-protection update in the same change to avoid a deadlock window.

## Hostile review acknowledgements (Gemini, 2026-04-27)

Three medium-confidence observations from Gemini on the initial rollout doc, retained here for traceability:

1. *"`verify / verify` is overly generic"* — accepted as a follow-up (see #4 above). Not a blocker for this rollout because branch protection requires the *exact* string and will not mismatch a coincidentally-named `verify / verify` from a different workflow inside the same repo (GitHub uses `<workflow-name> / <job-id>` and the receipt-gate caller is the only workflow with name `Receipt Gate` and job `verify` in either repo). Verified by `gh api .../check-runs` listing on PR #952 / PR #1424.
2. *"Required-context coupling deadlocks PRs on CI refactor"* — accepted, intentional. The whole point of this PR is to make CI refactors that drop a governance gate fail loudly. The drift-check follow-up (#3 above) is the durable mitigation: any rename of `verify / verify` -> `receipt-gate / verify` ships with a synchronous branch-protection PATCH, not an out-of-band breakage.
3. *"Rolling out enforcement on some repos while 4 others have silently broken workflows"* — partial agreement. The 7 working repos benefit from enforcement immediately; the 4 broken repos are tracked in follow-up #2 above. Waiting for all 11 to have working workflows before requiring any of them is the "opt-in verification never gets adopted" anti-pattern called out in the operating rules. Better to enforce where it works and surface the broken ones with a tracking ticket.
