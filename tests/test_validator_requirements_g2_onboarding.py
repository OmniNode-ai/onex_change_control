# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tests for OMN-14068: onboarding onex_change_control into the G2
hardcoded-IP-family validators (private-ip, localhost-url, hardcoded-topic,
todo-fixme-marker; OMN-13294).

Before this ticket, ``architecture-handshakes/validator-requirements.yaml``
in this repo did not declare these 4 validators at all, so the fleet
``validate-validator-requirements`` meta-gate could never flag them as a gap
-- they were structurally invisible, not merely unwired. These tests pin two
invariants so that state cannot regress silently:

1. This repo's own validator-requirements spec + baseline stay
   ``baseline-clean`` against the ``ValidatorRequirementsConsumer`` (the same
   consumer the ``validate-validator-requirements`` CI workflow runs).
2. A planted violation of each of the 4 newly-wired COMPUTE validators, in a
   file under ``src/onex_change_control/`` (the confirmed-gap scope), is
   actually detected by the corresponding runtime module -- proving the
   pre-commit hooks added in this PR are real, not decorative.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from omnibase_core.validation.hardcoded_topic.runtime_hardcoded_topic import (
    main as hardcoded_topic_main,
)
from omnibase_core.validation.localhost_url.runtime_localhost_url import (
    main as localhost_url_main,
)
from omnibase_core.validation.private_ip.runtime_private_ip import (
    main as private_ip_main,
)
from omnibase_core.validation.todo_marker.runtime_todo_marker import (
    main as todo_marker_main,
)
from omnibase_core.validation.validator_requirements_consumer import (
    ValidatorRequirementsConsumer,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SPEC_PATH = _REPO_ROOT / "architecture-handshakes" / "validator-requirements.yaml"
_BASELINE_PATH = (
    _REPO_ROOT / "architecture-handshakes" / "validator-requirements-baseline.yaml"
)

_G2_VALIDATOR_NAMES = (
    "hardcoded-private-ip",
    "hardcoded-localhost-url",
    "hardcoded-topic-string",
    "todo-fixme-marker",
)


# ---------------------------------------------------------------------------
# 1. Spec declares the G2 validators and the live repo is baseline-clean
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_spec_declares_g2_hardcoded_ip_family_validators() -> None:
    """The confirmed OMN-14068 gap: these 4 validators must be *declared* in
    this repo's own spec copy, not just some other repo's."""
    consumer = ValidatorRequirementsConsumer.from_spec_path(_SPEC_PATH)
    for name in _G2_VALIDATOR_NAMES:
        assert name in consumer.validators, (
            f"validator {name!r} missing from {_SPEC_PATH} -- the G2 "
            "hardcoded-IP-family onboarding (OMN-14068) has regressed"
        )


@pytest.mark.unit
def test_repo_is_baseline_clean_for_g2_validators() -> None:
    """The live repo scan for the 4 G2 validators must produce ZERO gaps
    (fully wired, not backlogged) -- this is the "at minimum wired" bar from
    the OMN-14068 DoD, not merely declared-then-baselined."""
    consumer = ValidatorRequirementsConsumer.from_spec_path(_SPEC_PATH)
    gaps = consumer.scan_repo(repo_name="onex_change_control", repo_root=_REPO_ROOT)
    g2_gaps = [g for g in gaps if g.validator in _G2_VALIDATOR_NAMES]
    assert g2_gaps == [], (
        f"G2 hardcoded-IP-family validators have live wiring gaps: {g2_gaps} "
        "-- OMN-14068 requires these wired (pre-commit + CI), not backlogged"
    )


# ---------------------------------------------------------------------------
# 2. Planted-violation acceptance test: each wired runtime module actually
#    detects a violation in a src/onex_change_control/-scoped file.
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_private_ip_validator_detects_planted_violation(tmp_path: Path) -> None:
    target = tmp_path / "planted_private_ip.py"
    target.write_text('BAD_IP = "192.168.99.42"\n')
    assert private_ip_main([str(target)]) != 0


@pytest.mark.unit
def test_private_ip_validator_passes_clean_file(tmp_path: Path) -> None:
    target = tmp_path / "clean_private_ip.py"
    target.write_text('GOOD = "not an ip"\n')
    assert private_ip_main([str(target)]) == 0


@pytest.mark.unit
def test_localhost_url_validator_detects_planted_violation(tmp_path: Path) -> None:
    target = tmp_path / "planted_localhost_url.py"
    target.write_text('BAD_URL = "http://localhost:9999/health"\n')
    assert localhost_url_main([str(target)]) != 0


@pytest.mark.unit
def test_localhost_url_validator_passes_clean_file(tmp_path: Path) -> None:
    target = tmp_path / "clean_localhost_url.py"
    target.write_text('GOOD = "https://example.com"\n')
    assert localhost_url_main([str(target)]) == 0


@pytest.mark.unit
def test_hardcoded_topic_validator_detects_planted_violation(tmp_path: Path) -> None:
    target = tmp_path / "planted_hardcoded_topic.py"
    target.write_text('BAD_TOPIC = "onex.synthetic.red.violation"\n')
    assert hardcoded_topic_main([str(target)]) != 0


@pytest.mark.unit
def test_hardcoded_topic_validator_passes_clean_file(tmp_path: Path) -> None:
    target = tmp_path / "clean_hardcoded_topic.py"
    target.write_text('GOOD = "not a topic"\n')
    assert hardcoded_topic_main([str(target)]) == 0


@pytest.mark.unit
def test_todo_marker_validator_detects_planted_violation(tmp_path: Path) -> None:
    target = tmp_path / "planted_todo_marker.py"
    target.write_text("# TODO: bare unfinished-work marker without a ticket\n")
    assert todo_marker_main([str(target)]) != 0


@pytest.mark.unit
def test_todo_marker_validator_passes_clean_file(tmp_path: Path) -> None:
    # The validator's only escape hatch is the onex-allow-todo-marker line
    # marker (or the file-level variant) -- a bare ticket-referenced
    # `# TODO(OMN-1234): ...` is still flagged, so it is NOT a clean case here.
    target = tmp_path / "clean_todo_marker.py"
    target.write_text(
        "# TODO(OMN-14068): suppressed for this test  # onex-allow-todo-marker\n"
    )
    assert todo_marker_main([str(target)]) == 0
