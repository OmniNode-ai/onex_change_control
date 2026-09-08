**REVOCATION. This PR performs no production mutation of any kind.** It removes two
prod-promotion grant entries from `grants/prod_promotion_grants.yaml`, returning the registry
to `entries: []` at rest. It dispatches nothing, deploys nothing, rolls back nothing and
restarts nothing. `onex-prod` keeps running exactly the images it is running now — what is
removed is permission, not workload.

Unlike the staging PR it reverses, **landing this is the point**: an open revocation revokes
nothing, and both entries stay live on `main` until it lands. It is still CODEOWNERS-reviewed
like any other change to this file.

## What is removed

__GRANT_IDS__ — the pair staged by the App and merged in OCC#8571 (`5df6a9d6bb`,
2026-09-07T15:31Z), together with their shared `promotion_batch_id`
`batch-3c59f394-5a40-4b44-b8e7-ed869d24c2cb`.

**Neither was ever consumed.** No `deploy-onex-prod.yml` dispatch ran against either digest
between that merge and this revocation.

## Authorisation

Operator consent, recorded as a durable row rather than claimed in a dispatch prompt:

> "__CONSENT_QUOTE__"

Cited by file and line: **`__CONSENT_CITATION__`**. That row names the approved scope (revoke
the two live prod-promotion grants through the sanctioned change path) and, as importantly,
the scope it excludes — any prod mutation, any new grant, the judge and collaborator lanes,
both clusters, credential rotation. The citation is carried in the reviewed request bundle,
not supplied at dispatch time, so it was reviewed as ordinary CI-gated work on `dev`.

## Why the App authored it

Operator ruling, 2026-09-08, firm: revocation is the same file, the same gate and the same
lifecycle as staging, so it goes through the same App-authored process.

The first attempt at this revocation was opened by hand under the CODEOWNER's identity. Not
by preference — `stage-prod-promotion-grant.yml` was **add-only by construction**: it refuses
to render unless the registry already reads `entries: []`, which is precisely the state a
revocation is trying to restore, so there was no revoke path to invoke. That hand-opened PR
recreated exactly the self-approval deadlock the 2026-09-06 ruling exists to end — GitHub does
not record an approval by a PR's own author, so the review half of the OMN-13418 gate could
not be exercised on it at all.

The gap is now closed **in the workflow**, not by remembering to do it correctly next time.
`mode: revoke` (landed on `dev` in __WORKFLOW_PR__) reads a revocation manifest from the same
ticket bundle and:

- **requires every named entry to be present on `main`** and refuses otherwise — a revocation
  that silently no-ops on an absent id reads exactly like one that worked;
- **refuses a result that would leave any expired entry at rest** (OMN-13424), so a partial
  revocation cannot trade one problem for a permanently red `main`;
- **preserves the header bytes exactly** — the removal is textual and never a YAML round-trip,
  because the 506 comment lines above `entries:` are prose the approver reads;
- **refuses `approved_by` and `expires_at`** rather than accepting and ignoring them, and
  requires the consent citation and the operator's verbatim words in their place.

The removal itself is `src/onex_change_control/scripts/revoke_prod_promotion_grants.py`, unit
tested in `tests/test_prod_promotion_grants.py`, not an inline heredoc.

## Diff shape

A pure removal: **29 deletions, 1 insertion**. The 506 header comment lines above `entries:`
hash identically before and after
(`58183a8476c3c03d1632bcab0493c889978ec5705be4ca6d5c092884294b7e34`). This is the OCC#5382 /
OCC#7554 prune shape.

## Why now — three reasons, none of them a defect in the grant

1. **The ruling.** The operator's word is the authority on this file.
2. **The premise had eroded.** The `.201` stability-test lane is the surface a
   `stability-proven` claim resolves from, and it was OOM-looping. The premise the entries were
   approved against no longer described the lane.
3. **The entries were blocking the repair.** The sanctioned stability refresh path refuses to
   run while live prod grants sit in the registry — so the entries were obstructing the fix to
   the very lane their own premise depends on. Revoking them is the precondition for restoring
   the evidence base any future grant would rest on.

## dod_evidence

- **Registry state, with a positive control.** The `dod_evidence` check_value chain exits 0
  against the file this run produces and exits 1 against `onex_change_control@main` at
  `5df6a9d6bb`, where both entries are live. A silently broken chain would have exited 0 on
  both trees.
- **Producer side.** `uv run pytest tests/test_prod_promotion_grants.py -q` → `40 passed`, run
  with the exact post-revocation bytes in place: 18 pre-existing schema/at-rest cases plus 22
  new revoke-mode cases (every named entry present, no expired entry left behind, non-named
  entries survive byte-identically, header bytes unchanged).
- **Consumer side, the item that proves the revocation took EFFECT.**
  `omninode_infra/scripts/validate_prod_promotion_grant.py` — the exact script
  `deploy-onex-prod.yml` runs — against this branch's file via its own `--grants-file` offline
  path, from an omninode_infra checkout at `main` `bc451167`:
  ```
  LEG B (grant leg isolated behind a synthetic GREEN staging-green fixture)
  [daniyalabbas96 13c96028] exit=1 [prod-grant] BLOCKED (absent): no grant entry found
  [daniyalabbas96 bc95b9bc] exit=1 [prod-grant] BLOCKED (absent): no grant entry found
  [jonahgabriel   13c96028] exit=1 [prod-grant] BLOCKED (absent): no grant entry found
  [jonahgabriel   bc95b9bc] exit=1 [prod-grant] BLOCKED (absent): no grant entry found

  POSITIVE CONTROL — the same probe against main 5df6a9d6bb
  [daniyalabbas96 13c96028] exit=0 [prod-grant] PASS: grant-80201a19-…
  [daniyalabbas96 bc95b9bc] exit=0 [prod-grant] PASS: grant-3c187f1e-…
  [jonahgabriel   13c96028] exit=1 [prod-grant] BLOCKED (self_granted)
  [jonahgabriel   bc95b9bc] exit=1 [prod-grant] BLOCKED (self_granted)
  ```
  Leg A — how a dispatch would actually run today, with no staging-green report — blocks all
  four combinations on the OMN-17357 premise *before* the grant anchor is read, and is reported
  rather than leaned on. The leg-B fixture exists only to isolate the grant anchor; it is not
  evidence that staging is green, which is part of why these grants were revoked. Full verbatim
  output, all three blocks, in
  `drift/dod_receipts/OMN-16753/dod-grant-consumer-gate-omn16753-app/command.yaml`.
- **Author identity read back from the API**, not asserted: see
  `drift/dod_receipts/OMN-16753/dod-grant-author-identity-omn16753-revoke/command.yaml`.

Refs: OMN-16753, OMN-13418, OMN-13424, OMN-17357

Evidence-Ticket: OMN-16753

## `main`-target fields (main-target-guard)

- **`hotfix-evidence: OCC-__HOTFIX_EVIDENCE_NUM__`** — OCC#8571, the App-authored staging PR
  this revocation reverses. It is the evidence for what is being removed.
- **`backmerge: #__BACKMERGE_NUM__`** — the same PR, and this needs saying plainly: **there is
  no content backmerge of the grants file into `dev`, deliberately.** The registry lives on
  `main` on purpose — that is the branch the consumer resolves from. `dev` has read
  `entries: []` throughout, so there is no dev-side revocation to perform. This is the same
  convention OCC#7418, OCC#7731, OCC#7899 and OCC#8571 used on this identical base.

hotfix-evidence: OCC-__HOTFIX_EVIDENCE_NUM__
backmerge: #__BACKMERGE_NUM__
