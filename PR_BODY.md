## Summary
Ticket OMN-20939; operator ruling 2026-10-10: lab configuration does not ship in public repositories. This PR only removes lab values.

## Changes
- check_runner_routing.py: TAILNET_SUFFIX constant replaced by tailnet_suffix(), default ".ts.net" (shape rule), narrowed by ONEX_TAILNET_SUFFIX; the repo's own workflows still pass the check.
- run_proof.py: Kafka bootstrap, Postgres host and port, runtime base URL and lane label come from ONEX_PROOF_KAFKA_BOOTSTRAP, ONEX_PROOF_PG_HOST, ONEX_PROOF_PG_PORT, ONEX_PROOF_RUNTIME_BASE_URL, ONEX_PROOF_LANE_LABEL; a missing one stops the run naming the variable. The manifest digest recorded for this file already differed from the file before this change.
- dod_report.json (OMN-12491, OMN-8336) and enum_compliance_violation.py docstring: lab address replaced by 192.0.2.10.
- New tests: tests/test_check_runner_routing_tailnet_suffix.py, 4 passed. contracts/OMN-20939.yaml validates with validate-yaml.
- This PR touches src/ and scripts/, so it is opened as the writer App through open-pr-as-writer-app.yml; the branch's PR_BODY.md carries this body.
- Not in this PR: the public knowledge_base runbooks named in the ticket (that clone is not on the lab host this lane ran on).

## Verification
check_runner_routing.py exercised with a probe tailnet index host with ONEX_TAILNET_SUFFIX unset and set, plus run_proof.py with ONEX_PROOF_KAFKA_BOOTSTRAP unset, observed: unset gives suffix ".ts.net" and 1 violation for the probe host; set to a different tailnet gives 0 violations; the repo's workflows pass; run_proof.py stops with "ONEX_PROOF_KAFKA_BOOTSTRAP is not configured".

Lab: host=h201, lane=config-out-7-9f8a, command=check_runner_routing.py exercised with a probe tailnet index host with ONEX_TAILNET_SUFFIX unset and set, plus run_proof.py with ONEX_PROOF_KAFKA_BOOTSTRAP unset, observed: unset gives suffix ".ts.net" and 1 violation for the probe host; set to a different tailnet gives 0 violations; the repo's workflows pass; run_proof.py stops with "ONEX_PROOF_KAFKA_BOOTSTRAP is not configured".
