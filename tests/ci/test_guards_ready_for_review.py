# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Regression pins for required external guards on PR admission."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from scripts.ci.ci_summary_gate import EXPECTED_EXTERNAL_CONTEXTS

_WORKFLOW_PATH = (
    Path(__file__).resolve().parents[2] / ".github" / "workflows" / "guards.yml"
)


def _guards_workflow() -> dict[Any, Any]:
    return dict(yaml.safe_load(_WORKFLOW_PATH.read_text(encoding="utf-8")))


def test_required_external_guards_run_on_ready_for_review() -> None:
    """Admission must execute both contexts CI Summary asserts externally."""

    workflow = _guards_workflow()
    pr_trigger = workflow[True]["pull_request"]
    assert "ready_for_review" in pr_trigger["types"]

    expected_actions = {
        "dep-provenance-gate": (
            "Dep Provenance Gate",
            ("opened", "synchronize", "reopened", "ready_for_review"),
        ),
        "non-dev-base-guard": (
            "non-dev-base-guard",
            ("opened", "edited", "synchronize", "ready_for_review"),
        ),
    }
    for job_id, (context_name, actions) in expected_actions.items():
        job = workflow["jobs"][job_id]
        assert job["name"] == context_name
        assert context_name in EXPECTED_EXTERNAL_CONTEXTS
        condition = str(job["if"])
        for action in actions:
            assert f'"{action}"' in condition
