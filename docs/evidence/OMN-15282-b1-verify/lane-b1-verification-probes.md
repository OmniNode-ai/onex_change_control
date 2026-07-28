# Lane B1-migrate — independent adversarial verification probes

Verifier session: independent of the implementer. All probes re-derived live
against `gh`/remote git state on 2026-07-28, never against the implementer's
self-report. Covers both tickets in Lane B1 (repo `OmniNode-ai/omninode_infra`):
OMN-15282 (PR #713, merged) and OMN-15281 (PR #716, open, green, mergeable).

## OMN-15282 — PR #713 (merged 25f160ccb2fca02db99f6cb8b5cee136c02be4fb)

Content-bound greps against `gh pr diff 713 --repo OmniNode-ai/omninode_infra`
(the actual merged diff, not a bare existence check):

```
NODE_DISCOVERY_MARKER_COUNT=7
```
`grep -c 'BEGIN node-owned migration discovery (OMN-15282)'` — 6 manifests +
1 string-literal reference inside the new test module.

```
VALIDATE_NODE_MIGRATION_ID_COUNT=9
```
`grep -c 'validate_node_migration_id() {'` — 6 manifests + 3 references inside
the new test module (extractor + docstring + assertion).

```
IDEMPOTENCY_TEST_PRESENT=2
```
`grep -c 'test_node_loop_is_idempotent_against_a_real_database'` — the test
function definition + its own call reference in the module.

RED-before/GREEN-after, re-executed independently on `.200`
(`ssh stickybeatz-studio`), holding `tests/scripts/test_node_migration_discovery.py`
byte-identical and swapping only the manifest tree it runs against:

```
RED_AT_PRE_713=4 failed, 2 passed
```
Ran against `bb43769` (dev tip immediately before #713), with the PR's own
(unmodified) test file copied in. 4 of 6 tests fail — the manifests at that
commit have no node-discovery block at all.

```
GREEN_AT_DEV_TIP=6 passed
```
Ran the same unmodified test file against `origin/dev` (post-#713-merge,
includes the real ephemeral-Postgres idempotency proof). 6 of 6 pass.

```
SCHEMA_CONSISTENCY_CHECK_STILL_PASS=3 PASS lines
```
`python3 scripts/check-migration-runner-schema-consistency.py` on `origin/dev`
— all 3 checks (checksum NOT NULL, advisory lock, readiness probe) still PASS,
confirming the shared LOCK block claim (untouched by #713).

```
PR_713_CHECKS=all pass, merged 2026-07-28T02:33:54Z
```
`gh pr checks 713 --repo OmniNode-ai/omninode_infra` — every job pass
(verify/verify, OCC Companion Merged Gate, Repo Scripts Tests, pre-commit,
deploy-gate, etc.); `gh pr view 713 --json state,mergedAt` confirms MERGED.

## OMN-15281 — PR #716 (open, head a467cc69d49eaf510d2618fa8c35c7e8ac9d8b4f)

Content-bound greps against `gh pr diff 716 --repo OmniNode-ai/omninode_infra`:

```
ENV_DRIVEN_DB_HOST_COUNT=6
```
`grep -c 'DB_HOST="${DB_HOST:?DB_HOST not set}"'` — exactly the 6 manifests,
proving DB_HOST is env-driven with a fail-fast default-read in every one, not
a subset.

```
MANIFEST_RENDER_ERROR_DEFINED=1
```
`grep -c 'class ManifestRenderError'` — the renderer's dedicated fail-loud
exception class exists exactly once.

```
RDS_FAILFAST_PATTERNS=2
```
`grep -c 'resolved to nothing -- refusing to fall back'` — both the
`rds_endpoint` and `rds_port` SSM-resolution failures name a refusal to
silently fall back to the in-cluster host/port (2 occurrences: one per
parameter), matching the "never silent fallback" requirement verbatim.

Independently re-run on `.200` (`ssh stickybeatz-studio`), fresh clone of PR
#716's head (a467cc69, a merge commit already containing origin/dev tip):

```
NEW_TEST_MODULES_PASS=20/20
```
`uv run pytest tests/scripts/test_render_migration_manifest_for_target.py
tests/scripts/test_run_migrations_rds_target.py -v` — 14 + 6 = 20 passed.

```
FULL_SUITE_ON_PR_716=446 passed
```
`env -u PYTHONPATH uv run pytest tests/ -q --tb=short` at the PR head.

```
SCHEMA_CONSISTENCY_CHECK_STILL_PASS_716=3 PASS lines
```
`python3 scripts/check-migration-runner-schema-consistency.py` at the PR head
— unchanged, all 3 checks PASS.

```
SHELLCHECK_RUN_MIGRATIONS_SH=clean
```
`shellcheck scripts/run-migrations.sh` — zero findings.

```
PR_716_CHECKS=all pass, mergeStateStatus=CLEAN, mergeable=MERGEABLE, state=OPEN
```
`gh pr checks 716` — every job pass (verify/verify, OCC Companion Merged Gate,
Repo Scripts Tests, pre-commit, deploy-gate); `gh pr view 716 --json
mergeStateStatus,mergeable,state`. Not merged as of this verification —
awaiting Codex per the lane's stated merge-sequencing note.

## Pre-existing autobind OCC evidence superseded by this companion

`contracts/OMN-15282.yaml` and `contracts/OMN-15281.yaml` already carried
autobind-generated `dod_evidence` entries (OCC#5223, OCC#5227) whose only
check is a bare `gh pr view ${PR_NUMBER} --repo ${REPO} --json number,state`
— an existence check, not a content-bound assertion against either PR's
actual diff. This companion APPENDS falsifiable, diff-content-bound entries
to both existing contracts (net-new receipt files only; the pre-existing
autobind entries are left in place, not removed) so the ticket's DoD evidence
is not solely a PR-exists check.
