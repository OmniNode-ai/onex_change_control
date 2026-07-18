# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Regression guard for divergent automation PRs (OMN-14778).

Proves the ``check_no_divergent_automation_pr`` gate:

* PASSES on the live repo workflow set (the retired generator is gone), and
* FAILS (RED) against a synthetic reproduction of the exact defect — a
  push-to-main workflow that cuts a fresh branch off main and opens
  ``gh pr create --base dev`` — and does NOT false-positive on the corrected
  form that rebases onto ``origin/dev`` first (proving the guard discriminates
  wrong-present vs. correct, not merely absence).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[1]
_MODULE_PATH = (
    REPO_ROOT / "scripts" / "validation" / "check_no_divergent_automation_pr.py"
)


def _load_checker() -> ModuleType:
    name = "check_no_divergent_automation_pr"
    spec = importlib.util.spec_from_file_location(name, _MODULE_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Register before exec so dataclass() can resolve cls.__module__ under
    # `from __future__ import annotations` (string annotations).
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


checker = _load_checker()


# The exact OMN-14778 defect: push to main, branch cut off the main checkout,
# PR targeted at the divergent `dev` base, no rebase onto origin/dev.
BAD_WORKFLOW = """\
name: bad-divergent-pr
on:
  push:
    branches: [main]
jobs:
  emit:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - name: open state PR
        run: |
          git checkout -B automation/state-${GITHUB_SHA:0:8}
          git commit -am "state"
          git push --force-with-lease origin HEAD
          gh pr create --base dev --head automation/state --title x --body y
"""

# Corrected form: same intent, but the fresh branch is reset onto origin/dev
# before the PR is opened, so the diff is minimal. Must NOT be flagged.
GOOD_REBASED_WORKFLOW = """\
name: good-rebased-pr
on:
  push:
    branches: [main]
jobs:
  emit:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - name: open state PR onto dev
        run: |
          git fetch origin dev
          git checkout -B automation/state origin/dev
          cp /tmp/state.yaml drift/state.yaml
          git commit -am "state"
          git push --force-with-lease origin HEAD
          gh pr create --base dev --head automation/state --title x --body y
"""

# PR to main (same branch it triggers on) is fine — no divergence.
GOOD_SAME_BASE_WORKFLOW = """\
name: good-same-base
on:
  push:
    branches: [main]
jobs:
  emit:
    runs-on: ubuntu-latest
    steps:
      - run: |
          git checkout -B automation/state
          gh pr create --base main --head automation/state --title x --body y
"""

# Variable base cannot be statically proven divergent — must NOT be flagged.
GOOD_VARIABLE_BASE_WORKFLOW = """\
name: good-variable-base
on:
  schedule:
    - cron: "0 6 * * *"
jobs:
  emit:
    runs-on: ubuntu-latest
    steps:
      - run: |
          git checkout -B promo/$repo
          gh pr create --base "$base" --head "$head" --title x --body y
"""


def _write(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


def test_live_repo_workflows_are_clean() -> None:
    """The real .github/workflows set must pass — the generator is retired."""
    workflows = sorted((REPO_ROOT / ".github" / "workflows").glob("*.y*ml"))
    assert workflows, "expected workflow files to exist"
    violations = checker.find_violations(workflows)
    assert violations == [], f"unexpected violations in live workflows: {violations}"


def test_retired_generator_is_deleted() -> None:
    for name in checker.RETIRED_WORKFLOW_BASENAMES:
        assert not (REPO_ROOT / ".github" / "workflows" / name).exists(), (
            f"{name} must stay retired (OMN-14778)"
        )


def test_bad_workflow_is_flagged(tmp_path: Path) -> None:
    """RED: the exact defect must be caught."""
    path = _write(tmp_path, "bad-divergent-pr.yml", BAD_WORKFLOW)
    violations = checker.find_violations([path])
    kinds = {v.kind for v in violations}
    assert "divergent-automation-pr" in kinds, violations


def test_rebased_workflow_is_not_flagged(tmp_path: Path) -> None:
    """The corrected (rebased-onto-origin/dev) form must pass."""
    path = _write(tmp_path, "good-rebased-pr.yml", GOOD_REBASED_WORKFLOW)
    assert checker.find_violations([path]) == []


def test_same_base_workflow_is_not_flagged(tmp_path: Path) -> None:
    path = _write(tmp_path, "good-same-base.yml", GOOD_SAME_BASE_WORKFLOW)
    assert checker.find_violations([path]) == []


def test_variable_base_workflow_is_not_flagged(tmp_path: Path) -> None:
    path = _write(tmp_path, "good-variable-base.yml", GOOD_VARIABLE_BASE_WORKFLOW)
    assert checker.find_violations([path]) == []


def test_retired_basename_tombstone_is_flagged(tmp_path: Path) -> None:
    """A returning retired workflow trips the tombstone even if content changes."""
    path = _write(tmp_path, "occ-rerun-downstream.yml", GOOD_SAME_BASE_WORKFLOW)
    violations = checker.find_violations([path])
    assert any(v.kind == "retired-workflow-returned" for v in violations), violations


@pytest.mark.parametrize("content", [BAD_WORKFLOW, GOOD_REBASED_WORKFLOW])
def test_on_key_yaml_boolean_gotcha_handled(tmp_path: Path, content: str) -> None:
    """PyYAML parses ``on:`` as boolean True; the checker must still see the trigger."""
    data = checker._load_yaml(_write(tmp_path, "wf.yml", content))
    assert data is not None
    triggers_push, _ = checker._push_branches(checker._get_on(data))
    assert triggers_push, "push trigger must be detected despite the on->True gotcha"


def test_cli_exit_codes(tmp_path: Path) -> None:
    bad = _write(tmp_path, "bad.yml", BAD_WORKFLOW)
    assert checker.main([str(bad)]) == 1
    good = _write(tmp_path, "good.yml", GOOD_REBASED_WORKFLOW)
    assert checker.main([str(good)]) == 0
