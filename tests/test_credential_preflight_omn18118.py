# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""OMN-18118 -- a check that needs an absent credential is NOT_EVALUATED.

Why this file exists (measured, not inferred):

OMN-18118 was filed saying live-cluster and Linear facts are *unexpressible*
as ``dod_evidence`` checks. Its own correction comment (``eda02919``,
2026-09-10) measured that against the admissibility classifier and found the
opposite -- the ALLOW vocabulary already carries a live-probe class, and three
contracts (``OMN-15954``, ``OMN-17740``, ``OMN-17809``) ALREADY author
``aws ssm get-command-invocation`` inside an executed ``check_value``.

What actually blocks them is one layer down: the compliance job's environment
is exactly two entries, ``EMERGENCY_BYPASS`` and ``GH_TOKEN``. So an
admissible live probe is authored green and runs red there -- and, worse, it
runs red in a way that is INDISTINGUISHABLE from the product being broken.
A BLOCK reading ``Command failed (exit 255)`` is an answer to a different
question wearing the check's name.

The fix in this ticket is therefore not a new item kind. It is:

  1. the credential's ABSENCE is reported by the CHECK, naming the credential,
  2. as ``NOT_EVALUATED`` -- which the runner already treats as "proved
     nothing" and never folds into the PASS count,
  3. and NEVER as PASS, on any path.

``test_ordinary_failure_is_still_block`` is the negative control. A classifier
that turned every red into a polite NOT_EVALUATED would be strictly worse than
the bug it replaces, so a genuine product failure with no credential marker
must still BLOCK. A zero needs a positive control; a "this is now handled
gracefully" needs a negative one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from onex_change_control.scripts.contract_compliance_check import (
    _AWS_CREDENTIAL_ENV_VARS,
    _CREDENTIAL_ABSENT_PREFIX,
    _LINEAR_CREDENTIAL_ENV_VARS,
    _RESULT_BLOCK,
    _RESULT_NOT_EVALUATED,
    _RESULT_PASS,
    _aws_credential_available,
    _check_command,
    _credential_absent_reason,
    _credential_denial_reason,
    _non_hermetic_reason,
)

if TYPE_CHECKING:
    from pathlib import Path

# Every env var that could satisfy either preflight, cleared together so a
# developer's ambient AWS profile or Linear key cannot turn a RED test green
# on their laptop while it stays RED in CI.
_ALL_CREDENTIAL_ENV_VARS = tuple(_AWS_CREDENTIAL_ENV_VARS) + tuple(
    _LINEAR_CREDENTIAL_ENV_VARS
)


@pytest.fixture
def _no_credentials(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """Remove EVERY credential source, not merely the environment variables.

    Clearing env vars alone is not enough and the omission was a real defect,
    caught by this file's own positive control rather than by review: an SSO or
    named-profile credential resolves through ``~/.aws/config`` with none of
    those variables set. On a developer's machine that made the preflight see a
    credential the CI job does not have, so the RED tests below would have gone
    green locally for the wrong reason. HOME is redirected at an empty
    directory so "absent" means absent.
    """
    for name in _ALL_CREDENTIAL_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    for name in (
        "AWS_SECRET_ACCESS_KEY",
        "AWS_CONFIG_FILE",
        "AWS_SHARED_CREDENTIALS_FILE",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path_factory.mktemp("no-aws-home")))


# ---------------------------------------------------------------------------
# AC1 -- the live-readback shape that is true today and unprovable in CI
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_no_credentials")
def test_aws_ssm_readback_without_credential_is_not_evaluated(tmp_path: Path) -> None:
    """The exact shape already authored in OMN-15954 / OMN-17740 / OMN-17809."""
    check_value = (
        "aws ssm get-command-invocation "
        "--command-id 73aba9ab-0000-0000-0000-000000000000 "
        "--instance-id i-06169517a92b45f86 --region us-east-1 "
        "--query StandardOutputContent"
    )

    result, detail = _check_command(check_value, tmp_path)

    assert result == _RESULT_NOT_EVALUATED
    assert result != _RESULT_PASS
    assert detail.startswith(_CREDENTIAL_ABSENT_PREFIX)
    # The credential must be NAMED. "something is missing" is the diagnosis
    # this ticket exists to replace.
    assert "occ-compliance-ssm-readonly" in detail


@pytest.mark.usefixtures("_no_credentials")
def test_linear_relation_read_maps_to_the_linear_credential() -> None:
    """AC5-of-OMN-17512 shape: re-resolve a Linear relation over the API.

    Asserted at the preflight, not through ``_check_command`` -- see
    ``test_linear_read_is_blocked_by_the_hermetic_guard`` for why the real path
    never reaches the preflight for this shape.
    """
    check_value = (
        "curl -sS -X POST https://api.linear.app/graphql "
        '-H "Authorization: $LINEAR_API_KEY"'
    )

    reason = _credential_absent_reason(check_value)

    assert reason is not None
    assert reason.startswith(_CREDENTIAL_ABSENT_PREFIX)
    assert "occ-compliance-linear-readonly" in reason


def test_linear_read_is_blocked_by_the_hermetic_guard() -> None:
    """The measured limit, pinned so it cannot be quietly assumed away.

    OMN-18118's correction comment lists a live Linear read as ADMISSIBLE. That
    is true of ``classify_evidence_item`` and FALSE of this runner's execution
    path: ``_non_hermetic_reason`` refuses ``curl`` egress to any non-loopback
    host, so the Linear shape is rejected before the credential preflight is
    ever consulted. Provisioning a Linear key alone therefore does NOT make
    this shape runnable here.

    This test exists so that fact is a red test rather than a rediscovery.
    Lifting the guard for public routable hosts is a separate decision.
    """
    reason = _non_hermetic_reason(
        {
            "check_type": "command",
            "check_value": "curl -sS https://api.linear.app/graphql",
        }
    )
    assert reason is not None
    assert "non-loopback host" in reason

    # Control: the two shapes this ticket DOES unblock clear the same guard.
    for runnable in (
        "aws ssm get-command-invocation --command-id x "
        "--instance-id i-06169517a92b45f86",
        "gh api repos/OmniNode-ai/omninode_infra/actions/runs/1/jobs",
    ):
        assert (
            _non_hermetic_reason({"check_type": "command", "check_value": runnable})
            is None
        ), runnable


@pytest.mark.usefixtures("_no_credentials")
def test_credential_absent_is_never_pass(tmp_path: Path) -> None:
    """Fail-closed, stated as its own assertion rather than left implied."""
    result, _ = _check_command(
        "aws ssm list-command-invocations --instance-id i-06169517a92b45f86",
        tmp_path,
    )
    assert result != _RESULT_PASS


# ---------------------------------------------------------------------------
# The preflight must step aside once the credential IS present
# ---------------------------------------------------------------------------


def test_aws_preflight_steps_aside_when_credential_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With a credential in env the check EXECUTES; the decline is not taken.

    This is the positive control for the preflight: without it, a preflight
    that declined unconditionally would pass every test above while making the
    credential wiring pointless.
    """
    for name in _ALL_CREDENTIAL_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test-only-not-a-real-key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test-only-not-a-real-key")

    assert (
        _credential_absent_reason("aws ssm get-command-invocation --command-id x")
        is None
    )


def test_linear_preflight_steps_aside_when_credential_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in _ALL_CREDENTIAL_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LINEAR_API_KEY", "test-only-not-a-real-key")

    assert _credential_absent_reason("curl https://api.linear.app/graphql") is None


# ---------------------------------------------------------------------------
# Post-hoc: a scope refusal the preflight cannot see statically
# ---------------------------------------------------------------------------


def test_github_app_actions_scope_refusal_is_not_evaluated() -> None:
    """OMN-15320's live cause: 403 on ``.../actions/...`` from the App token.

    The token is PRESENT, so no preflight can catch this -- the refusal is only
    visible in the failing command's own output. OCC PR #8863's CI run
    (job 102709538516) is the recorded instance.
    """
    cmd = "gh api repos/OmniNode-ai/omninode_infra/actions/runs/34297859000/jobs"
    stderr = "gh: Resource not accessible by integration (HTTP 403)"

    reason = _credential_denial_reason(cmd, stderr)

    assert reason is not None
    assert reason.startswith(_CREDENTIAL_ABSENT_PREFIX)
    assert "actions: read" in reason
    assert "onexbot-occ-writer" in reason


def test_aws_missing_credential_message_is_recognised_post_hoc() -> None:
    """Belt and braces: the CLI's own diagnosis also maps to NOT_EVALUATED."""
    reason = _credential_denial_reason(
        "aws ssm get-command-invocation --command-id x",
        "Unable to locate credentials. You can configure credentials by running "
        '"aws configure".',
    )

    assert reason is not None
    assert "occ-compliance-ssm-readonly" in reason


# ---------------------------------------------------------------------------
# NEGATIVE CONTROL -- the classifier must not swallow real failures
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_no_credentials")
def test_ordinary_failure_is_still_block(tmp_path: Path) -> None:
    """A red with no credential marker stays BLOCK.

    Without this, "everything is NOT_EVALUATED" would pass every other test in
    this file and silently retire the gate.
    """
    result, _ = _check_command("exit 1", tmp_path)
    assert result == _RESULT_BLOCK


def test_unrelated_403_is_not_a_credential_decline() -> None:
    """A 403 that is not the App-scope refusal must not be laundered."""
    assert (
        _credential_denial_reason(
            "gh api repos/OmniNode-ai/omninode_infra/contents/README.md",
            "HTTP 403: rate limit exceeded",
        )
        is None
    )


@pytest.mark.usefixtures("_no_credentials")
def test_command_merely_mentioning_aws_in_a_string_is_not_declined(
    tmp_path: Path,
) -> None:
    """Binary-position analysis, not substring matching.

    ``grep -c 'aws ssm' file`` observes a committed file and needs no
    credential; declining it would remove a real check from the corpus.
    """
    target = tmp_path / "runbook.md"
    target.write_text("aws ssm get-command-invocation --command-id abc\n")

    result, _ = _check_command("grep -c 'aws ssm' runbook.md", tmp_path)

    assert result == _RESULT_PASS


def test_shared_config_file_counts_as_a_credential_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Regression for the defect the positive control caught.

    An env-var-only test reported a FALSE "absent" for an SSO or named-profile
    credential, which would have silently declined a check that would have run
    -- the failure direction this block exists to prevent, pointed the other
    way. Pinned so the shared-config branch cannot be dropped as redundant.
    """
    for name in (*_ALL_CREDENTIAL_ENV_VARS, "AWS_SECRET_ACCESS_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("AWS_SHARED_CREDENTIALS_FILE", raising=False)
    config = tmp_path / "config"
    config.write_text("[default]\nregion = us-east-1\n")
    monkeypatch.setenv("AWS_CONFIG_FILE", str(config))

    assert _aws_credential_available() is True
    assert (
        _credential_absent_reason("aws ssm get-command-invocation --command-id x")
        is None
    )


def test_no_credential_source_at_all_is_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The other half of the same pair -- an empty HOME really does read absent."""
    for name in (*_ALL_CREDENTIAL_ENV_VARS, "AWS_SECRET_ACCESS_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("AWS_CONFIG_FILE", raising=False)
    monkeypatch.delenv("AWS_SHARED_CREDENTIALS_FILE", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))

    assert _aws_credential_available() is False
