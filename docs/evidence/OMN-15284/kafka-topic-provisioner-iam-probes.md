# OMN-15284 — tenant topic provisioner admin client / ModelKafkaClientConfig seam

Adversarial-verifier probes against `OmniNode-ai/omninode_infra#712`
(branch `jonah/omn-15284-kafka-topic-provisioner-iam`), re-derived independently
of the implementer's own report. All commands below were run against the
actual PR diff / PR head, not against restated claims.

```
PRODUCT_HEAD=a38d52382aa2499ba7c580bdc5f51b7d08a07795
PRODUCT_REPO=OmniNode-ai/omninode_infra
PRODUCT_PR=712
```

## Diff-content probes (`gh pr diff 712` saved to `/tmp/pr712.diff`)

```
SEAM_IMPORT_LOAD_KAFKA_CLIENT_CONFIG_TOTAL=3
SEAM_KWARGS_SPREAD_COUNT=1
SEAM_BARE_BOOTSTRAP_SERVERS_KWARG_REMOVED=1
TEST_MSK_IAM_FN_PRESENT=1
TEST_DEFAULT_ENV_FN_PRESENT=1
TEST_SASL_MECHANISM_ABSENT_ASSERTION_PRESENT=1
FILES_CHANGED=2
FILES_CHANGED_LIST=docker/onex-api/kafka_topic_provisioner.py,docker/onex-api/tests/test_kafka_topic_provisioner.py
```

- `SEAM_IMPORT_LOAD_KAFKA_CLIENT_CONFIG_TOTAL`: `grep -c 'from kafka_client_config import load_kafka_client_config' /tmp/pr712.diff` across the whole diff (3 occurrences: one module-level import in the production file, plus one local re-import inside each of the two new test functions) — the provisioner and both new tests import the same loader `workflow_publisher.py` uses; no parallel config path introduced anywhere in the diff.
- `SEAM_KWARGS_SPREAD_COUNT`: `grep -c '\*\*kafka_config.kafka_python_kwargs()' /tmp/pr712.diff` — the admin client is constructed via kwargs spread, not a hand-copied dict.
- `SEAM_BARE_BOOTSTRAP_SERVERS_KWARG_REMOVED`: the diff removes the line `bootstrap_servers=servers,` (added `-` line) inside the `KafkaAdminClient(` call — the seam this ticket exists to close.
- `FILES_CHANGED`: `gh pr diff 712 --repo OmniNode-ai/omninode_infra --name-only | wc -l` — only the provisioner module and its colocated test file changed; no parallel config path introduced.

## Independent re-run on `.200` (`stickybeatz-studio`), detached worktrees off the same commits, patch-transfer discipline

```
BEFORE_REF=origin/dev (25f160c)
AFTER_REF=jonah/omn-15284-kafka-topic-provisioner-iam (a38d523)
RED_BEFORE=2 failed, 8 passed, 1 skipped
GREEN_AFTER=15 passed, 1 skipped
RED_BEFORE_FAILURE_MODE=KeyError: 'security_protocol' (both new tests, against origin/dev's kafka_topic_provisioner.py with the new test file copied over unmodified)
```

The new test file `tests/test_kafka_topic_provisioner.py` (PR-head byte contents) was copied unmodified onto a detached worktree of `origin/dev` (commit `25f160c`) and run with `pytest -m unit`: both new tests (`test_msk_iam_env_carries_iam_auth_into_owned_admin_client`, `test_default_env_owned_admin_client_kwargs_unchanged`) fail with `KeyError: 'security_protocol'` because the pre-fix admin client is constructed with only `bootstrap_servers=`/`client_id=` and never carries a `security_protocol` key — proof this is RED-against-exists-but-wrong (the pre-fix `ensure_tenant_topics` function exists, runs, and completes; its admin-client kwargs are what's wrong), not RED-against-missing. The same file at the PR head (`a38d523`) passes all 15 unit tests (1 pre-existing skip, unrelated — `kafka-python` install-dependent).

## Live PR CI state at verification time (`gh pr checks 712`)

```
CHECKS_PASS_COUNT=34
CHECKS_PENDING=pytest
PYTEST_JOB_ID=90161795171
PYTEST_ELAPSED_AT_VERIFY_MINUTES=25
COMPARABLE_SUCCESSFUL_RUN_DURATION_MINUTES=2-3
```

All 34 non-`pytest` required/advisory checks on PR #712 report `pass`, including
`verify/verify`, `OCC Companion Merged Gate`, `deploy-gate`, `pre-commit`, and
`Python Tests` (a separate, faster job from the full `pytest` job). The
`pytest` job (full onex-api suite) was still `in_progress` at the
"Run tests with coverage" step after 25 minutes of elapsed wall time at last
poll. This is anomalous, not normal-length: `gh run list --workflow
"onex-api-tests" --limit 10` shows every other run of this workflow on this
repo completing in 2-3 minutes wall time (both successful runs and the two
cancelled runs on an unrelated branch that were cancelled, not slow-successes).
No completed, successful run of this workflow anywhere in the last 10 runs
took anywhere near 25 minutes. The implementer's build-report caveat citing
"PR #710's evidence cites 1,682 tests" as a comparable precedent for a
long-but-healthy run is not supported by this run-history sample — PR #710's
own CI runs for this same workflow were `cancelled`, not observed to complete
successfully at a comparable duration. **The `pytest` check must be re-polled
and confirmed green (or its hang root-caused) before this PR is treated as
merge-ready; it was NOT green at verification time.**
