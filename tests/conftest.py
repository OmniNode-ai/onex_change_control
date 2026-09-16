# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Shared fixtures for contract drift tests."""

from __future__ import annotations

import os
from typing import Any

import pytest

from onex_change_control.handlers.handler_drift_analysis import compute_canonical_hash
from onex_change_control.models.model_contract_drift_input import (
    ModelContractDriftInput,
)


@pytest.fixture
def base_compute_contract() -> dict[str, Any]:
    """A minimal ONEX COMPUTE contract dict for testing."""
    return {
        "name": "node_transform_data",
        "version": "1.0.0",
        "type": "COMPUTE",
        "description": "Transforms input records.",
        "algorithm": {
            "algorithm_type": "default_transform",
            "deterministic": True,
        },
        "input_schema": "ModelTransformInput",
        "output_schema": "ModelTransformOutput",
        "metadata": {
            "owner": "platform-team",
            "sla_ms": 100,
        },
    }


@pytest.fixture
def pinned_hash(base_compute_contract: dict[str, Any]) -> str:
    return compute_canonical_hash(base_compute_contract)


@pytest.fixture
def drift_input_no_change(
    base_compute_contract: dict[str, Any],
    pinned_hash: str,
) -> ModelContractDriftInput:
    return ModelContractDriftInput(
        contract_name="node_transform_data",
        current_contract=base_compute_contract,
        pinned_hash=pinned_hash,
    )


# OMN-15669 REMEDIATION r1: there is deliberately NO `collect_ignore_glob` here.
#
# The first build excluded `fixtures/contract_shape_v1/conformant/tests/*.py`
# from the outer suite on the reasoning that the tree is "data the gate
# collects, not a test of this repo". The gate collects it with
# `pytest --collect-only`, which never EXECUTES a line — so the conformant
# fixture's `assert_seam_shape` calls ran nowhere in CI, and an adversarial
# replay confirmed that mutating `MockWidgetStore` to return a shape the seam
# schema forbids left the suite 51/51 green. The reference implementation of
# "the mock is validated against the real seam schema" was itself unvalidated.
#
# The fixture module is a real, passing, self-contained test module: it resolves
# its schema from its own FIXTURE_ROOT, so it runs correctly from the outer
# rootdir. Collecting it normally is what makes the reference shape load-bearing
# — break the mock and the suite goes red. `test_conformant_fixture_executes_
# and_catches_a_divergent_mock` in tests/test_contract_shape_v1_legs.py is the
# anti-regression anchor for this decision.


# ---------------------------------------------------------------------------
# Git-environment isolation (OMN-18434, generalising OMN-14891 / OMN-16584)
# ---------------------------------------------------------------------------
# Git exports repository- and command-scoped ``GIT_*`` variables into the
# environment of every hook it runs, and those variables OVERRIDE both ``cwd=``
# and ``git -C``. A test that builds a disposable repository therefore mutates
# the repository whose hook launched pytest, not its fixture.
#
# On 2026-09-16 that re-initialized a SHARED CANONICAL clone in this fleet:
# ``core.bare`` set true, a fixture identity written into that clone's local
# config, the clone unable to run ``git status``, and the lane's own commit
# landing on ``refs/heads/main`` with the wrong author. The class is older --
# OMN-14744 fixed one file, OMN-14891 fixed one repo systemically, OMN-16584
# closed the real-``~/.gitconfig`` half -- and OMN-18434 is the generalisation
# OMN-14891's own body asked for and never got.
#
# This is byte-for-byte the shape ``omnibase_infra`` carries, deliberately: a
# repo whose scrub differs from the fleet's is a per-repo configuration, and a
# per-repo configuration is how one rule becomes several.


def _strip_inherited_git_environment() -> None:
    """Remove caller-owned git process state before tests are collected.

    Called from ``pytest_configure`` -- earlier than any fixture -- so a module
    that shells out to git at IMPORT time is covered too.
    """
    for key in tuple(os.environ):
        if key.startswith("GIT_"):
            os.environ.pop(key)


@pytest.fixture(autouse=True)
def _strip_test_local_git_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stop one test's git process state leaking into the next, and blind git
    to the developer's real user-level configuration.

    Deleting the leaked ``GIT_*`` overrides is not sufficient on its own: with
    none set, git falls back to its default global-config resolution and reads
    the caller's actual ``~/.gitconfig``. A contributor's legitimate ``[tag]
    gpgsign = true`` forces a bare ``git tag <name>`` to upgrade to an annotated
    tag -- GPG can only sign annotated tag objects -- which then has no message
    and opens an editor for ``TAG_EDITMSG`` under a runner with no terminal
    attached (OMN-16584). ``GIT_CONFIG_GLOBAL=/dev/null`` plus
    ``GIT_CONFIG_NOSYSTEM=1`` make git ignore the global and system files
    entirely rather than override one key; ``GIT_EDITOR=true`` is the second
    line of defence against any other path that still tries to launch one.
    """
    for key in tuple(os.environ):
        if key.startswith("GIT_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_EDITOR", "true")


def pytest_configure(config: pytest.Config) -> None:
    """Scrub the git environment before collection (OMN-18434)."""
    del config  # the hook's signature, not a parameter this needs
    _strip_inherited_git_environment()
