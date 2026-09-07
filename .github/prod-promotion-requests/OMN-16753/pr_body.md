**PREP ONLY — this PR performs no production mutation of any kind.** It stages two
prod-promotion grant entries. They become effective only when a human CODEOWNERS operator
lands this on `onex_change_control@main` — that merge **is** the approval act (OMN-13418).
No agent can complete it.

**The standing operator restriction on dispatching `deploy-onex-prod.yml` is unchanged, and
landing this does not lift it.** A grant is a digest-scoped authorization; dispatching is a
separate decision. Both are the operator's, and this PR asks for only the first.

## Why this PR exists at all, and why the App authored it

The two prior stagings of this same authorization — OCC#7418 (merged 2026-08-28, never
consumed, lapsed at its own `2026-08-30T16:00:00Z` expiry, pruned) and OCC#7899 (opened
2026-08-31, never merged, lapsed at its own `2026-09-02T16:00:00Z` expiry) — were both opened
by `jonahgabriel`, who is also the CODEOWNER. GitHub does not let an author approve their own
pull request, so the review half of the OMN-13418 gate could never actually be exercised on
either one.

Operator ruling, 2026-09-06, firm: the staging PR is to be opened by the `onexbot-occ-writer`
App so the CODEOWNER's review and merge is a genuine second party. This PR is that. Its author
is the App identity (app id 4361937), minted inside GitHub Actions from the org secrets by
`.github/workflows/stage-prod-promotion-grant.yml`.

Stated plainly rather than implied, because it is the obvious objection: the *content* of this
request was authored by an agent lane and reviewed as ordinary CI-gated work on `dev` (the
request bundle at `grants/requests/OMN-16753/` and the workflow that renders it, PR
__WORKFLOW_PR__). This PR is that reviewed bundle rendered onto `main` by the App. The App is
the author of record and the operator is the approver of record; neither role is being
laundered through the other, and what changed versus OCC#7899 is that the approval is now
structurally *possible*.

This PR supersedes OCC#7899, which has been closed.

## What it authorizes

Exactly one `deploy-onex-prod.yml` dispatch from `refs/heads/main`:

```
deploy_target=onex-api
commit_sha=8a4ebe6919a52f8de272fb908e90866e8514e3cd
reclaim_api_hostname=true
include_onex_public=false
```

`include_onex_public` stays false — that is the OMN-16702 lane, a different anchor and a
different blast radius.

## Two entries, both mechanically required

| Entry | Digest | Why |
| --- | --- | --- |
| `grant-80201a19-…` | `sha256:13c96028…` (omninode-runtime) | The "Enforce prod-promotion grant — runtime image" step carries **no `if:` guard**. It resolves `RUNTIME_IMAGE_REF` from `k8s/onex-prod/runtime/kustomization.yaml`, re-read on omninode_infra `main` at `bc451167` for this re-stage and **unchanged**, and it is the digest `onex-prod/omninode-runtime` is already running. A same-digest authorization: it changes no workload, it only lets the unconditional gate resolve. Without it the dispatch blocks before reaching the onex-api gate. |
| `grant-3c187f1e-…` | `sha256:bc95b9bc…` (omnicloud/core) | The real promotion. `main-sha-8a4ebe6`. |

## Digest re-verification for this re-stage

omninode_infra `main` advanced since OCC#7899 was staged — HEAD is now `bc451167`, the
OMN-17350 promotion squash of 2026-09-01. That advance produced **no new `main`-lineage
build**. Re-checked 2026-09-06 by a full scan rather than a recent-window sample:
`aws ecr describe-images --repository-name omnicloud/core` returns **52** images, of which
**exactly 1** carries a `main-sha-*` tag — `main-sha-8a4ebe6` = `sha256:bc95b9bc…`, pushed
2026-08-28T10:26:09Z. Every image pushed since is dev-lineage, and `deploy-onex-prod.yml`
refuses any dispatch whose ref is not `refs/heads/main`. So the digest is unchanged because it
is still the only candidate, not merely because nobody looked.

## Read this before approving: a grant is no longer sufficient on its own

OMN-17357 landed on omninode_infra **after** OCC#7899 was staged. It added a second,
independent precondition to the same consumer script: a GREEN staging-green report
(`scripts/evaluate_staging_green_bar.py`) that is fresh, matches the engine's current bar
revision, and is **bound to the digest being promoted**. It is evaluated **before** the grant,
so a dispatch with no such report never reaches the grant anchor at all.

Proven for this re-stage rather than inferred — run offline against this exact staged file
with no report supplied, all four dispatcher/digest combinations exit 1 on
`[staging-green] BLOCKED (absent)`.

Landing this PR therefore authorizes a promotion that **would still be refused today** for want
of a staging-green bar. That is a fact about the current state of the lane, not a defect in
this grant, and it is stated here so it is not a surprise afterwards.

## `approved_by` and the dispatcher constraint

`approved_by: __APPROVED_BY__` is a **proposal**, not an approval; the CODEOWNERS approver
confirms or replaces it at merge time. It is a real login and not a placeholder for the reason
OCC#7213 recorded: the consumer enforces `approved_by != requested_by` against `github.actor`,
so a synthetic placeholder would compare unequal to *every* dispatcher and would silently
disable the only mechanical anti-self-approval check on this path.

**Consequence, stated plainly: with the staged value, the eventual `workflow_dispatch` actor
MUST NOT be `jonahgabriel`.** It must be another admin on `OmniNode-ai/omninode_infra` —
`daniyalabbas96` dispatched the two prior attempts. If `approved_by` changes at merge time,
re-derive the permitted dispatcher from the new value.

`requested_by` is deliberately **not** a YAML field: the grant schema rejects unknown keys
(`validate-prod-promotion-grants` fails on `unexpected fields`), so the requester is recorded
by this PR's author identity and by the file header, not by a field.

`expires_at: __EXPIRES_AT__` is also a proposal — 72h from staging. The approver may shorten it.
If it lapses unmerged, the OMN-13424 at-rest invariant fails CI for every subsequent PR to
`main` and the block must be pruned and re-staged, which is exactly what happened to OCC#7899.

## dod_evidence

- **Producer side.** `uv run pytest tests/test_prod_promotion_grants.py -q` → `23 passed`,
  including the OMN-13424 at-rest invariant that refuses any expired entry.
- **Consumer side, both legs, four directions each.**
  `omninode_infra/scripts/validate_prod_promotion_grant.py` — the exact script the workflow
  runs — against this staged file via its own `--grants-file` offline path, from an
  omninode_infra worktree at `main` `bc451167`:
  ```
  LEG A (no staging-green report — how it would run today)
  [daniyalabbas96 13c96028] exit=1 [staging-green] BLOCKED (absent)
  [daniyalabbas96 bc95b9bc] exit=1 [staging-green] BLOCKED (absent)
  [jonahgabriel   13c96028] exit=1 [staging-green] BLOCKED (absent)
  [jonahgabriel   bc95b9bc] exit=1 [staging-green] BLOCKED (absent)

  LEG B (synthetic GREEN report supplied ONLY to isolate the grant leg — a fixture, not
  evidence about staging)
  [daniyalabbas96 13c96028] exit=0 [prod-grant] PASS: grant-80201a19-… approved_by=__APPROVED_BY__ expires_at=__EXPIRES_AT__
  [daniyalabbas96 bc95b9bc] exit=0 [prod-grant] PASS: grant-3c187f1e-… approved_by=__APPROVED_BY__ expires_at=__EXPIRES_AT__
  [jonahgabriel   13c96028] exit=1 [prod-grant] BLOCKED (self_granted) — self-approval is rejected
  [jonahgabriel   bc95b9bc] exit=1 [prod-grant] BLOCKED (self_granted) — self-approval is rejected
  ```
  Full verbatim output is in
  `drift/dod_receipts/OMN-16753/dod-prod-promotion-grant-consumer-gate-omn16753-app/command.yaml`.
- **Author identity read back from the API**, not asserted: see
  `drift/dod_receipts/OMN-16753/dod-prod-promotion-grant-author-identity-omn16753-app/command.yaml`.
- **Nothing is pruned by this PR**: `main` is already at rest (`entries: []`).

Refs: OMN-16753, OMN-16620, OMN-16799, OMN-16757, OMN-17357, OMN-13418, OMN-13424

Evidence-Ticket: OMN-16753

## `main`-target fields (main-target-guard)

`main-target-guard` accepts a `main`-targeting PR from a `hotfix/*` head only when the body
carries both a `hotfix-evidence:` and a `backmerge:` line. Both are stated literally rather
than satisfied with a placeholder:

- **`hotfix-evidence: OCC-__HOTFIX_EVIDENCE_NUM__`** — the evidence companion for
  `omninode_infra#1116` on this same ticket (merged 2026-08-31), which advances the
  `k8s/onex-prod/api/deployment.yaml` pin off the 22-route March build and adds the CI gate
  that keeps it off. This grant authorises the live half of the same change.
- **`backmerge: #__BACKMERGE_NUM__`** — the same PR, and this needs saying plainly: **there is
  no content backmerge of the grants file into `dev`, deliberately.** The registry lives on
  `main` on purpose — that is the branch the consumer resolves from. Backmerging grant rows
  into `dev` would create a second registry that no gate reads and that the OMN-13424 at-rest
  expiry invariant would then start failing on independently. This is the same convention
  OCC#7418, OCC#7731 and OCC#7899 used on this identical base.

hotfix-evidence: OCC-__HOTFIX_EVIDENCE_NUM__
backmerge: #__BACKMERGE_NUM__
