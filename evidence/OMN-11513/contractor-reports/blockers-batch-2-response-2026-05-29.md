# Blockers Request Batch 2 Response

Date: 2026-05-29
Audience: Bret / clone45
Scope: OMN-11513 integration evidence and SEA delegation registry blockers

## Summary

All three blockers from the May 29 Batch 2 request have an owner-side response.
Two are fully resolved. The third has a code fix and OCC evidence in place, but
the implementation PR is still waiting on PR-review gate cleanup before merge.

`omni_home` remains private and is not a contractor communication surface. Any
evidence you need should now be published through `onex_change_control` or
another shared repository you can access.

## Blocker 1: Golden-Chain Evidence Bundle

Status: resolved

The May 28 delegation golden-chain evidence bundle has been mirrored from
private `omni_home` into `onex_change_control`.

Shared path:

```text
evidence/OMN-11513/delegation-golden-chain-proof-2026-05-28/
```

PR:

```text
https://github.com/OmniNode-ai/onex_change_control/pull/1880
```

Result:

- PR `#1880` merged into `dev` on 2026-05-29.
- The bundle contains 33 source files plus `publication-manifest.json`.
- `publication-manifest.json` records correlation id
  `bda0b379-3b0a-47f6-b398-d682424bff19`.
- The manifest includes per-file SHA-256 hashes for the 33 mirrored files.

Next step for Bret:

```bash
git fetch origin dev
git checkout dev
git pull --ff-only origin dev
ls evidence/OMN-11513/delegation-golden-chain-proof-2026-05-28/
```

Use the shared `onex_change_control` path above for artifact classification.
Do not depend on `omni_home`.

## Blocker 2: Write Access To onex_change_control

Status: resolved

The `clone45` GitHub account now has active write permission on
`OmniNode-ai/onex_change_control`.

Verification:

```json
{"permission":"write","role_name":"write","user":"clone45"}
```

Next step for Bret:

```bash
git remote -v
git fetch origin dev
git checkout clone45/omn-11513-integration-evidence-days-1-7
git rebase origin/dev
git push -u origin clone45/omn-11513-integration-evidence-days-1-7
```

Then open a PR against `dev` with the Day 5 and Day 7 findings under:

```text
evidence/OMN-11513/integration-test-passes/
```

If your local commit `1f300489` no longer rebases cleanly, keep the evidence
contents and resolve path conflicts in favor of the current `dev` tree.

## Blocker 3: OMN-12434 Model Registry Mismatch

Status: implementation PR open; OCC evidence merged; CodeRabbit gate failing

Owner decision:

The shared SEA registry should match the live served model ids for the current
runtime topology. A model endpoint 404 for "model does not exist" should surface
as registry/config drift, not as an ordinary retryable transport failure that
silently escalates to the next tier.

Implementation PR:

```text
https://github.com/OmniNode-ai/onex-self-extending-agent/pull/166
```

OCC evidence PR:

```text
https://github.com/OmniNode-ai/onex_change_control/pull/1881
```

Current implementation status:

- `onex_change_control#1881` merged into `dev` on 2026-05-29.
- `onex-self-extending-agent#166` is open.
- SEA local verification passed in the worker worktree:
  - `uv run ruff check ...`
  - `uv run pytest tests/unit -q`
  - result reported by worker: `1111 passed, 1 skipped`
- GitHub checks passing on `#166`:
  - lint
  - test
  - typecheck
  - security
  - Receipt Gate
- Current blocker on `#166`:
  - CodeRabbit Thread Check is failing.
  - reject-skip-token scan was still in progress/queued at last status check.

Implemented behavior in `#166`:

- `src/contracts/model_registry.yaml` uses served ids:
  - `Qwen3.6-35B-A3B`
  - `Qwen3.6-27B-MTP-IQ4_XS.gguf`
- Endpoint-specific local network overrides remain outside the base registry.
- Model endpoint 404s are classified as config/registry drift.
- Registry drift is non-retryable and non-escalatable.
- Tests cover registry ids, 404 classification, escalation behavior,
  cost-pricing coverage, and enum mapping.

Next step for owner:

Resolve the CodeRabbit review thread(s) on
`onex-self-extending-agent#166`, wait for the remaining required checks, then
merge the SEA PR.

Next step for Bret:

After `#166` merges, rerun the 7-task delegation regression against the updated
registry and confirm that tier 1 serves at least one request instead of all
tasks escalating to tier 2.

## Private Repository Boundary

New runtime proof bundles, golden-chain artifacts, OCC receipts, and
contractor-facing evidence should not be stored in private `omni_home`.

Owner-side follow-up:

- `OMN-12437` tracks migration/classification of legacy tracked
  `omni_home/docs/evidence` artifacts.
- `omni_home` now has a CI guard planned/added to block new `docs/evidence/**`
  artifacts, with deletions allowed for migration cleanup.
