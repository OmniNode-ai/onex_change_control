## OMN-18434

Git exports `GIT_DIR` / `GIT_WORK_TREE` / `GIT_INDEX_FILE` / `GIT_COMMON_DIR` into the environment of every hook it runs, and those variables override **both** `cwd=` **and** `git -C`. A test that builds a disposable repository is therefore safe from a shell and destructive from a pre-commit or pre-push hook: the child git targets the repository whose hook launched pytest.

On 2026-09-16 that re-initialized a shared canonical clone in this fleet. `core.bare` was set true, a fixture identity was written into that clone's local config, the clone could not run `git status`, and the committing lane's own commit landed on the wrong branch with the wrong author while the working tree did not contain its files.

The class is not new and neither is the remedy. OMN-14744 fixed one file. OMN-14891 then fixed one repository systemically — `omnibase_core` — and its own body asked for that fix to be applied "as the general policy". It never was. This pull request is that generalisation for this repository.

## What this changes

Two layers, and both are the shape `omnibase_infra` already carries. Matching it byte-for-byte is deliberate: a repository whose scrub differs from the fleet's is a per-repo configuration, and a per-repo configuration is how one rule becomes several.

1. **`tests/conftest.py`** — `_strip_inherited_git_environment()` called from `pytest_configure`, which is earlier than any fixture and so covers a module that shells out to git at import time, plus an autouse fixture that repeats it per test and isolates git from the developer's real user-level configuration. This is the primary remedy: it neutralises every call site already written, whether or not that call site remembered to pass `env=`. Remembering is exactly what failed.
2. **The gate** — the canonical `omnibase_core` validator (OMN-14891) wired as the `git-env-scrub` pre-commit hook **and** a CI job, so a new unguarded call site is refused rather than discovered by a later incident. Both surfaces install the same pinned version range, so the local verdict and the CI verdict cannot diverge on the same file.

## Evidence

**The proof is a discriminator pair, not an absence assertion.** `tests/test_git_env_isolation_omn18434.py` runs the same child program against the same decoy repository twice, and the scrub under test is the committed function from this repository's own conftest, not a re-typed stand-in.

| Half | What it asserts |
| -- | -- |
| negative control | with `GIT_DIR` planted and no scrub, the decoy **is** corrupted and the intended directory is never initialized — the incident, reproduced |
| positive | with the committed scrub applied, the decoy is untouched and the intended directory is the one written |

If the negative control ever stops holding, the harness has stopped reproducing the defect and the suite goes red rather than quietly green. Every decoy descends from `tmp_path`; no canonical clone is touched by any test here.

Also pinned: that the scrub is defined by the whole `GIT_` prefix rather than by a named list, proven with a variable that is not a real git variable — a list goes stale the first time git adds one.

**Focused local run:** `8 passed` for the new proof file, and the conftest change is exercised by every test in the suite.

**Residual, recorded rather than allowlisted.** The canonical validator reports `66` unguarded call sites under `tests/` in this repository. Every one of them is neutralised at runtime by layer 1. The gate is scoped to the files a change adds or modifies — a ratchet, so the count can only fall — and the wiring carries no suppression annotation, no skip input and no path exception. `test_the_gate_carries_no_escape_hatch` fails if one is added.

Rewriting all `66` call sites to pass an explicit `env=` is a second layer of defence behind a fixture that already holds, across enough test files to put the suites it protects at risk. That is deliberately not done here, and the count is published rather than hidden.

## Scope

`omnibase_core` is untouched: it already carries both halves and already reports zero. The other repositories in this ticket are handled in their own pull requests.

dod_evidence: the discriminator pair above, and the validator reporting zero findings on every file this change adds or modifies.

— orchestrator, on the operator's behalf
