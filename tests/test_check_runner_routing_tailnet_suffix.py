# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""The tailnet-only index check needs no deployment fact (OMN-20939).

The shape rule treats any ``*.ts.net`` host as tailnet-only; a deployment may
narrow it to its own tailnet through ONEX_TAILNET_SUFFIX.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "validation"
    / "check_runner_routing.py"
)
_spec = importlib.util.spec_from_file_location("check_runner_routing", _SCRIPT)
assert _spec is not None
assert _spec.loader is not None
routing = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(routing)

# Invented names: a documentation tailnet and an ordinary public index.
TAILNET_INDEX = "http://cache.example-tailnet.ts.net:3141/root/pypi/+simple/"
PUBLIC_INDEX = "https://pypi.org/simple"


def _violations(value: str) -> list[str]:
    found: list[str] = routing.validate_index_env(
        workflow_path=".github/workflows/x.yml",
        job_name="job",
        env={"UV_INDEX": value},
    )
    return found


@pytest.fixture(autouse=True)
def _no_ambient_suffix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ONEX_TAILNET_SUFFIX", raising=False)


def test_default_shape_flags_any_tailnet_host() -> None:
    found = _violations(TAILNET_INDEX)
    assert len(found) == 1
    assert routing.DEFAULT_TAILNET_SUFFIX in found[0]


def test_default_shape_leaves_a_public_index_alone() -> None:
    assert _violations(PUBLIC_INDEX) == []


def test_a_configured_suffix_narrows_the_rule(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ONEX_TAILNET_SUFFIX", "other-tailnet.ts.net")
    assert _violations(TAILNET_INDEX) == []
    assert len(_violations("http://cache.other-tailnet.ts.net:3141/")) == 1


def test_the_default_suffix_is_the_shape_rule() -> None:
    assert routing.DEFAULT_TAILNET_SUFFIX == ".ts.net"
    assert routing.tailnet_suffix() == ".ts.net"
