# OMN-15266 Business-Proof Driver Seam Fix — Probe Summary

Probes executed on `.200` (`stickybeatz-studio`, 24-core Mac Studio, `PYTHONPATH` unset)
per omni_home CLAUDE.md rule 11a, in patch-transfer mode: every changed file was
sha256-verified byte-identical between this Mac and `.200` BEFORE each gate run,
so no gate ran against an unedited copy.

PRODUCT_REPO=OmniNode-ai/omninode_infra
PRODUCT_BRANCH=jonah/omn-15266-business-proof-seam-fix
PRODUCT_HEAD=25bfef234b88e27596f3ce523ac985c8c155de51

## Seam fix — static readback of the driver at PRODUCT_HEAD

DRIVER_XAPIKEY_HEADERS=1
DRIVER_BEARER_TOKEN_HEADERS=0
DRIVER_PAYLOAD_TASK_TYPE=1
DRIVER_TASK_TYPE_DEFAULT=1
WORKFLOW_TASK_TYPE_WIRED=1
WORKFLOW_NARROWING=2
WORKFLOW_REARM_TICKET=3

## Cross-boundary seam test — RED before, GREEN after

The load-bearing evidence. The same unmodified test module was run twice on `.200`,
against two different drivers, changing nothing else:

SEAM_TESTS_TOTAL=9
SEAM_TESTS_GREEN_POSTFIX=9
SEAM_TESTS_RED_PREFIX=5
PREFIX_DRIVER_SHA=f57be0bdffe238529c47401a860e0ab96e0bd980b79816fd61de060104a40cbf
POSTFIX_DRIVER_SHA=37362abfe4ce06c0a0a591b4f6234f1020a74b855c571653355202c0288c5d00

`PREFIX_DRIVER_SHA` is `scripts/post-deploy-business-proof.sh` exactly as it merged in
omninode_infra#699 (`git show origin/dev:...`). Against it the test fails on five
assertions, two independent defects:

    FAILED TestPayloadSeam::test_the_submitted_payload_satisfies_the_shipped_contract
    FAILED TestPayloadSeam::test_the_payload_carries_a_task_type_the_schema_pattern_accepts
    FAILED TestAuthSeam::test_the_credential_is_sent_on_the_gateways_api_key_header
    FAILED TestAuthSeam::test_the_static_key_is_never_sent_as_a_bearer_token
    FAILED TestAuthSeam::test_every_credentialed_request_carries_the_key

The four assertions that stay GREEN pre-fix (workflow_type is allowlisted,
correlation_id rides at top level, the gateway still refuses the Bearer downgrade,
the driver still fails closed on an absent credential) are the control: the test is
discriminating between the two defects and the surrounding behaviour, not failing
wholesale.

This is a real RED-against-exists-but-wrong, not a RED-against-missing: the driver
exists, runs, and completes; it is its REQUEST that violates the gateway contract.

## Payload half executes the gateway's own validator

Not a re-implementation. The test calls
`docker/onex-api/workflow_contracts.py::validate_workflow_payload` — the same function
`POST /v1/workflows` calls at ingress — against the SHIPPED
`docker/onex-api/workflow-contracts.yaml`:

    pre-fix  payload {'prompt': ...}                    -> [{'field': 'task_type', 'message': 'field is required'}]
    post-fix payload {'prompt': ..., 'task_type': ...}  -> []

## Auth half is pinned to the shipped gateway source

The expected header name is read out of `docker/onex-api/auth_api_keys.py`
(`ApiKeySettings.header`) with `ast` rather than hardcoded, so a gateway-side rename
fails this test. The Bearer-refusal branch is asserted still present in
`routers/workflows.py::_resolve_principal`. Stated precisely: this half executes the
DRIVER (the captured request is real) but derives its EXPECTATION from source; it does
not execute `_resolve_principal` itself, because importing `auth_api_keys` pulls in the
psycopg-backed repository layer that the repo-root suite deliberately does not depend on.

## Interim narrowing is ticketed, not silent

The business-proof step is scoped to `workflow_dispatch` only until
`secrets.BUSINESS_PROOF_TOKEN` exists. That narrowing is a VISIBLE workflow condition
carrying an inline re-arm block naming OMN-15267; `tests/k8s/test_deploy_onex_dev_business_proof_gate.py`
fails if the narrowing is present without its re-arm ticket, and separately asserts the
driver gained no credential-absence skip. No runtime silent-skip was added: the script
still exits 2 with `BUSINESS_PROOF_TOKEN is required` when the credential is absent.

## Gate results on .200

FULL_SUITE=384 passed
MYPY=clean (tests/scripts/test_business_proof_request_seam.py)
SHELLCHECK=clean (scripts/post-deploy-business-proof.sh)
PRECOMMIT_ALL_FILES=pass
RUFF_FORMAT_CHANGED_FILES=clean
RUFF_CHECK_CHANGED_FILES=clean

`ruff check .` across the whole repo reports 40 errors and `ruff format --check .`
reports 8 files needing reformat — verified IDENTICAL on a clean `origin/dev` checkout,
so that is a pre-existing repo baseline, untouched and unclaimed by this change.

## Mutation testing — the new assertions are not vacuous

Each mutation was applied to the workflow and caught by exactly the intended test:

  * `OMN-15267` refs stripped -> `test_the_narrowing_names_its_re_arm_ticket` FAILED
  * `BUSINESS_PROOF_TASK_TYPE` env wiring deleted -> `test_required_env_is_wired[BUSINESS_PROOF_TASK_TYPE]` FAILED

## NOT claimed

**No live proof run has occurred.** Nothing was deployed. No cloud resource, cluster,
lane or repo secret was mutated by this lane. Whether the fixed driver authenticates
against live staging cannot be known until `secrets.BUSINESS_PROOF_TOKEN` exists — that
is OMN-15267's re-arm condition, and the live run remains OMN-15101.

No PASS receipts are minted here: this lane implemented the change, so hand-signing its
own receipts would make `verifier == runner` and is exactly the self-authored-evidence
pattern the Receipt Honesty Gate (OMN-12791) rejects.
