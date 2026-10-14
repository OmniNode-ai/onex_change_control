## Summary
This PR re-opens the change of onex_change_control#13751 (commit 031dc33505, ticket OMN-18595) through the writer-App dispatch path, because #13751 was opened under a human account and its check-human-authored-privileged-pr check is red. #13751 is to be closed once this PR is open. The original head f97d41e3b29d also carried a bot evidence commit minted for #13751; that commit is not carried over.

## Changes
Content, unchanged from #13751: .github/workflows/ci.yml and .pre-commit-config.yaml invoke the check_imperative_contracts module explicitly (`uv run python -m onex_change_control.scripts.check_imperative_contracts ...`) for the scripts scan instead of the `check-imperative-contracts` console script; .github/workflows/call-receipt-gate.yml no longer passes the retired allowlist argument (the ALLOWLIST_ARG variable and its file test are removed); tests/unit/test_check_gate_cli_omn18595.py is added (128 lines, 6 test cases).
Added here: one comment line in ci.yml and one in call-receipt-gate.yml naming OMN-18595, because the writer-App dispatch guardrail (check-app-pr-branch-allowed) requires each changed workflow file to cite the dispatching ticket in a line the branch adds.

## Proof
Lab: host=h202 lane=omn18327-wapp command=`uv run pytest tests/unit/test_check_gate_cli_omn18595.py -q` at code commit b660fa5b87 observed=6 passed. `uv run check-app-pr-branch-allowed --base-ref 076264beb8 --head-ref HEAD --ticket OMN-18595` exited 0 with "PASS: all 2 changed workflow file(s) cite OMN-18595 in a line this branch adds."
Caveat: running the same test file against the pre-image of ci.yml and .pre-commit-config.yaml on h202 also gave 6 passed, because the console script is installed in that environment, so the failure the change repairs was not reproduced on h202.
