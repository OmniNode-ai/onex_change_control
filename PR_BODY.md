## Summary

Operator ruling 2026-10-14T12:04:57Z (OMN-14919): no human-authored commit and no required human merge decision on guarded change-control paths; the standing rule is no required human review until there are full-time employees. A change-control PR deleting the retired skip-token approvals registry file was held for a person because check-bot-authored-authz-guard rejected its bot commit on allowlists/ and the Governance File Advisory Gate required a human merge decision for allowlists/skip_token_approvals.yaml. This change removes the human-authored privileged-PR checks — they only tested who authored a PR and had no content part — and narrows the authz-guard and governance path scope to grants/.

## Changes

- Removed: `.github/workflows/human-authored-privileged-pr-gate.yml`, `src/onex_change_control/scripts/check_human_authored_privileged_pr.py`, `src/onex_change_control/scripts/check_privileged_path_push.py`, their two test files, the two pyproject script entries, the check-privileged-path-push pre-push hook in `.pre-commit-config.yaml`, the two canonical-file-shape baseline lines, and the check-human-authored-privileged-pr entry in SOFT_ALLOWLIST in `scripts/ci/ci_summary_gate.py`. These checks only tested who authored a PR; they had no content part.
- Narrowed: check_bot_authored_authz_guard SENSITIVE_PATH_PREFIXES from `(grants/, allowlists/)` to `(grants/)`; the file patterns of the check-bot-authored-authz-guard hook in `.pre-commit-config.yaml` and `.pre-commit-hooks.yaml` from `^(grants/|allowlists/)` to `^grants/`; GOVERNANCE_PATHS in `scripts/ci/check_governance_paths.py` from two files to `grants/prod_promotion_grants.yaml`. The Governance File Advisory Gate and the auto-merge.yml arming exclusion both read GOVERNANCE_PATHS.
- Kept unchanged (prod promotion trust anchor, out of scope): bot-authored change rejection for `grants/`, the auto-merge arming exclusion and advisory gate for `grants/prod_promotion_grants.yaml`, and validate-prod-promotion-grants. No content validator was removed.
- Docs: the CLAUDE.md privileged-paths section replaced with an authorship section; comments in ci.yml, auto-merge.yml, governance-file-advisory-gate.yml, ci_summary_gate.py, and validate_prod_promotion_grants.py updated.
- Fixed on the way: the unguarded git subprocess in test_check_bot_authored_authz_guard.py now passes `env=scrub_git_location_env(os.environ)` (git-env-scrub hook).

## Proof

Lab: host=h202 lane=omn14919-nohu command=`uv run pytest tests/unit/scripts tests/ci tests/test_prod_promotion_grants.py tests/test_prod_promotion_grants_manifest_kind.py -q --no-cov -p no:cacheprovider` at sha aceb4716db93 observed=1399 passed, 2 skipped, exit 0 (onex-lab-run --via ssh; --via bus was refused with a traceback in _bus_python).

Tests changed first: a bot PR author or machine commit identity on `allowlists/skip_token_approvals.yaml` and `allowlists/omnimarket.yaml` now passes the authz guard; unreadable git history on `allowlists/` passes; bot changes on `grants/` still reject; the skip-token path is not in GOVERNANCE_PATHS and the advisory gate passes it; a combined skip-token plus grants change is still excluded. RED was observed before the code change (`test_non_sensitive_paths[allowlists/omnimarket.yaml]` failed with assert True is False).

Not run on the lab: `mypy src/ --strict` (CI runs it).

After merge: the change-control PR that deletes the retired skip-token approvals registry file needs its checks re-run.
