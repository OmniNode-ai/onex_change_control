# Stage the prod-promotion grant for the OMN-14201 production scale-down

**This PR must not be merged by any agent.** It is effective only when a human CODEOWNERS operator
lands it on `main`. That merge is the approval act (OMN-13418), and it is the anti-self-issue anchor
an agent structurally cannot complete.

## What it authorizes

Exactly one gated `deploy-onex-prod.yml` dispatch, run from `refs/heads/main` with
`deploy_target=runtime-only`, `include_onex_public=false`, `reclaim_api_hostname=false`.

That target scopes the apply to the namespace bootstrap and `k8s/onex-prod/runtime`. Its entire
blast radius is seven Deployments and two CronJobs.

## What it changes: replica counts, and nothing else

The digest is `sha256:13c96028…`, which `k8s/onex-prod/runtime/kustomization.yaml` **already pins**
on `omninode_infra` `main` at `bc451167`, and which the seven pods are **already running**. This is
a same-digest authorization. No image changes, no layer is pulled, no version rolls.

It exists only because the OMN-14209 gate step "Enforce prod-promotion grant — runtime image"
carries no `if:` guard and blocks every dispatch before anything else is evaluated.

The underlying change is `omninode_infra#1310`: `replicas: 0` on `omninode-runtime`, `-effects`,
`-worker`, `agent-actions-consumer`, `skill-lifecycle-consumer`, `contract-resolver` and
`omnibase-intelligence-api`, plus `suspend: true` on the `baselines-batch-compute` and
`cross-repo-validation` CronJobs.

Operator ruling, 2026-09-10, firm, attributed paraphrase: *production does not matter until staging
works; production is burning cloud cost; shut down everything in production except what the website
requires.* The website (`onex-prod/omniweb`, serving omninode.ai, www and app) is outside this tree
and is untouched by a `runtime-only` dispatch.

## One entry, not three — and the reason matters

The other six workloads in the scale-down sit outside `k8s/onex-prod/runtime` and can only be
reached by `deploy_target=all`, which would additionally require grants on the onex-api and omniweb
digests. **Those are deliberately not requested here**, because on `omninode_infra` `main` at
`bc451167` both pins are **ahead of what production is running**:

| Component | Pinned on the release branch | Running in production |
| --- | --- | --- |
| onex-api | `sha256:bc95b9bc…` | `sha256:b4c32554…` |
| omniweb | `sha256:a0c624d5…` | `sha256:eef16942…` |

So an `all` dispatch is **not a scale-down** — it is a scale-down plus two image promotions that
have been sitting staged on the release branch, including the OMN-16753 API cutover. Folding those
into a cost-reduction authorization would move two versions past an approver who was asked about
replica counts. Separate decision, separate grant.

## Read this before approving: the promotion is refused today

The OMN-17357 staging-green premise is evaluated **before** the grant by the same consumer script,
and it currently fails. Measured, not asserted — both runs are in
`receipts/dod-omn14201-consumer-gate-blocks-on-staging-green/`:

- With no report: `BLOCKED (absent)`, exit 1.
- With the real current report (bar revision `r10`, collected 2026-09-10T16:44:13Z):
  `BLOCKED (not_green)` — the bar is `BLOCKED`, 2 PASS / 3 FAIL / 3 UNPROVEN — exit 1.

Production health is HEALTHY, so the confirmed-unhealthy waiver does not apply.

**Merging this authorizes a promotion that would still be refused today, for want of a green staging
bar.** That is not a defect in the grant. It is the governing ruling's own logic enforced
mechanically: production does not move until staging works.

## The schema cannot express a scale-down

The required fields are `grant_id`, `runtime_lane`, `image_digest`, `promotion_batch_id`,
`approved_by`, `expires_at`, `created_at`, `reason`. There is no replica, scale or desired-state
field, and the consumer matches on digest equality alone. **Approving this means approving a digest
in order to authorize a replica count.** That gap is real and is recorded as a residual rather than
papered over.

## Proposals the approver owns

- **`approved_by`** is a proposal. It is a real login, not a placeholder, because the consumer
  enforces `approved_by != requested_by` against `github.actor` — a synthetic value would compare
  unequal to every dispatcher and silently disable the only mechanical anti-self-approval check.
  With `jonahgabriel` staged, the eventual dispatcher must be someone else.
- **`expires_at`** is a proposal of 7 days, chosen because the staging bar this waits on is not
  expected to go green within 72 hours. Shorten it before merging if preferred; merging is
  approving. If it lapses unmerged, the OMN-13424 at-rest invariant fails CI for every subsequent PR
  to `main` and the block must be pruned and re-staged.

## Rollback needs no grant

```
kubectl -n onex-prod scale deploy/<name> --replicas=1
kubectl -n onex-prod patch cronjob/<name> -p '{"spec":{"suspend":false}}'
```

Caveat recorded in the plan: eleven `onex-prod` workloads use pull policy `Always` and need a valid
registry credential to restart. The `onex-prod` `ecr-token-refresh` CronJob has failed its last
three runs and is deliberately left **enabled** by `omninode_infra#1310` so that failure stays
visible; its `onex-dev` twin currently writes the credential into both namespaces.

Nothing is pruned by this PR: `main` is already at rest (`entries: []`).

Full evidence and cost analysis: `beta/plans/2026-09-10-prod-scale-down-to-website-plan.md` in the
internal knowledge base.

Related: OMN-14201.
