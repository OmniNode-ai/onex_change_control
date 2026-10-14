## Summary
Ticket OMN-20074. This PR is the writer-App half of onex_change_control#13686, split because #13686 mixed an allowlist deletion that must be human-authored with edits under .github and src that must come from the writer App (check-human-authored-privileged-pr refuses a human-authored PR touching src/, scripts/ or .github/). #13686 is to be closed once both halves are open. The bot evidence commit that #13686 carried (minted for #13684) is not carried over.

## Changes
This half carries every file of #13686 except allowlists/skip_token_approvals.yaml: .github/CODEOWNERS (the review-owner entry for the registry is removed), .github/workflows/call-receipt-gate.yml (the gate is no longer handed a registry path; an added comment cites OMN-20074), .pre-commit-hooks/reject-deploy-gate-skip-token.sh (the hook rejects a skip token whatever comment accompanies it), src/onex_change_control/models/model_script_exception.py, src/onex_change_control/scripts/validate_precommit_fail_loud.py, src/onex_change_control/validators/arch_handler_contract_compliance.py (stale pointers to the registry corrected), and tests/unit/test_reject_deploy_gate_skip_token_hook.py. 7 files.

The registry file allowlists/skip_token_approvals.yaml (its content is `entries: []`) is deleted in a separate human-authored PR that touches only that path. Governance path lists keep the registry path.

Consent cited by the original commit: docs/tracking/ROLLING_WORK_LEDGER.md:66460, item 8.

## Proof
Lab: host=h202 lane=omn18327-wapp command=`uv run pytest tests/unit/test_reject_deploy_gate_skip_token_hook.py tests/unit/validators/test_arch_handler_contract_compliance_capture.py tests/unit/scripts/test_check_scripts_canonical.py -q` at code commit 9d847850b8 observed=49 passed; `bash .pre-commit-hooks/reject-deploy-gate-skip-token.sh --self-test` observed=10 passed, 0 failed; `uv run check-app-pr-branch-allowed --base-ref 076264beb8 --head-ref HEAD --ticket OMN-20074` exited 0.
