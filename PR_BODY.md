Ticket: OMN-20503

## What changed
- `src/onex_change_control/scripts/contract_compliance_check.py` gains a per-run `_QuotaBreaker` held on `_CheckContext` (no module-global state).
- A command/test_passes check whose full output (stdout+stderr, not the 200-char snippet) matches the primary rate-limit message (`\brate limit exceeded\b`, same regex as commit_sha_resolver) and not the secondary one trips it.
- While tripped, `_run_single_check` returns BLOCK "QUOTA_EXHAUSTED -- GitHub API quota for this token is spent; gh check not executed. First: <message>" for any executed-command check whose binaries include gh, without calling `_run`.
- Non-gh checks still run.
- A secondary rate limit body and "Resource not accessible by integration" do not trip it.
- The summary prints one extra line counting QUOTA_EXHAUSTED checks.
- No retry, no sleep, PASS/WARN/NOT_EVALUATED semantics unchanged; no change to ci.yml or any contract.
- New test file `tests/test_contract_compliance_quota_halt.py` (4 tests).
- `PR_BODY.md` on the branch is this body (writer-App dispatch path); it is the only other file.

## Proof
- **Why:** run 37171017353 job 111343811981 (PR #12717) passed 1753 checks, hit "API rate limit exceeded for installation ID 148180820" at 02:38:50Z, then made 952 more gh calls, every one a 403 (summary 1753/2823 PASS, 118 WARN, 952 BLOCK).
- **Proof RED:** test committed alone at 28b3efed3e; pytest exit 1, 2 failed 2 passed; the failing assertion was 5 gh calls where 1 was expected.
- **Proof GREEN at ec46047584:** focused file pytest exit 0 (4 passed); with `tests/test_credential_preflight_omn18118.py`, `tests/test_omn16824_test_passes_semantics.py`, `tests/test_evidence_admissibility.py`: 115 passed. ruff format and check clean, mypy --strict clean on the changed module, pre-commit hooks passed on commit.
- **Proof fake gh (no GitHub contact):** a fake gh first on PATH answers pr view and pulls files, answers success to the first 3 other calls, then the quota 403 body with exit 1. Command: `uv run python scripts/ci/run_contract_compliance_check.py --pr 12717 --repo OmniNode-ai/onex_change_control --contracts-dir contracts`, against `contracts/OMN-14888.yaml` (2,710 dod_evidence items).
  - **Before (package at 28b3efed3e):** 2813 fake-gh invocations total, 2692 of them check calls; SUMMARY line: `[SUMMARY] OMN-14888: 3/2811 PASS, 118 WARN, 2690 BLOCK`
  - **After (ec46047584):** 48 fake-gh invocations total (2 setup, 42 pr view checks that come before the first commits check in the contract, 4 commits api checks: 3 success plus the one that tripped); SUMMARY lines: `[SUMMARY] OMN-14888: 3/2811 PASS, 118 WARN, 2690 BLOCK` and `[SUMMARY] 2765 QUOTA_EXHAUSTED check(s) not executed: GitHub API quota spent.`
  - **Note:** the 2765 count includes skipped checks that the existing INERT demotion reports as WARN, so it is larger than the BLOCK count.

## Lab
- Host h201, lane app-quota-sink, head ec46047584 (code head; this PR_BODY.md commit follows it), command: the fake-gh run above plus the focused pytest files; observed the counts above.

## Out of scope
- Per-run re-probe of the OMN-14888 log (about 2,700 calls per observation PR, about 35,000 estimated 23:00Z-03:08Z) stays with parent OMN-19096.
