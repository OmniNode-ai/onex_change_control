# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""OMN-15411 round 2: the corpus ratchets must stay wired into a REQUIRED context.

The ratchets in ``test_lint_contract_check_values_corpus_baseline.py`` were
warn-tier-adjacent in practice, not enforcement: ci.yml's ``test`` job is
skipped on every PR targeting ``dev``, the ``tests+coverage (shadow)`` job that
also ran them is deliberately non-required, and OCC dev's required contexts are
exactly ``["CI Summary", "required-check-skip-guard / check-skip-vectors"]``.

``scripts/validation/check_corpus_ratchet_wiring.py`` is the anti-removal anchor for the
repair. These tests prove it fires on each removal vector rather than asserting
that it exists.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CI_YAML = _REPO_ROOT / ".github" / "workflows" / "ci.yml"


def _load_wiring_module() -> Any:
    """Load the validator by path, matching the repo pattern for scripts/validation.

    ``scripts`` is on ``mypy_path`` but ``scripts/validation`` is not, so a bare
    ``import check_corpus_ratchet_wiring`` type-checks locally and fails
    ``mypy src/ tests/`` in CI with ``import-not-found``. Every other test that
    exercises a ``scripts/validation`` module (e.g. test_check_ai_slop.py) uses
    this loader for the same reason.
    """
    script_path = (
        _REPO_ROOT / "scripts" / "validation" / "check_corpus_ratchet_wiring.py"
    )
    spec = importlib.util.spec_from_file_location(
        "check_corpus_ratchet_wiring", script_path
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


wiring: Any = _load_wiring_module()


def _load() -> dict[str, Any]:
    data = yaml.safe_load(_CI_YAML.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _write(tmp_path: Path, data: dict[str, Any]) -> Path:
    path = tmp_path / "ci.yml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


@pytest.mark.unit
def test_live_ci_yaml_wiring_is_intact() -> None:
    """The real ci.yml in this repo passes. GREEN-after control."""
    assert wiring.check_wiring(_CI_YAML) == []


@pytest.mark.unit
def test_removing_the_job_fails(tmp_path: Path) -> None:
    data = _load()
    data["jobs"].pop(wiring._JOB_ID)
    failures = wiring.check_wiring(_write(tmp_path, data))
    assert failures
    assert "is absent" in failures[0]


_GATE_MODULE = _REPO_ROOT / "scripts" / "ci" / "ci_summary_gate.py"


def _write_gate_module(tmp_path: Path, source: str) -> Path:
    path = tmp_path / "ci_summary_gate.py"
    path.write_text(source, encoding="utf-8")
    return path


@pytest.mark.unit
def test_ci_summary_declaring_needs_is_a_regression(tmp_path: Path) -> None:
    """OMN-15768: ci-summary must be the no-needs poller; a `needs:` here is
    a REGRESSION to the needs-graph-omission bug class (OCC#6346), not a
    legitimate wiring shape -- the opposite of what this anchor used to test.
    """
    data = _load()
    data["jobs"]["ci-summary"]["needs"] = [wiring._JOB_ID]
    failures = wiring.check_wiring(_write(tmp_path, data))
    assert any("declares `needs:`" in f for f in failures), failures


@pytest.mark.unit
def test_job_not_registered_in_strict_gate_jobs_fails(tmp_path: Path) -> None:
    """The job's display name must be registered in ci_summary_gate.py's
    STRICT_GATE_JOBS -- an unregistered job is invisible to the poller's
    explicit strict check (the generic default-deny sweep alone is not
    sufficient proof of intent, per the OMN-15768 design).
    """
    mutated_gate = _GATE_MODULE.read_text(encoding="utf-8").replace(
        '"Contract Corpus Ratchets (OMN-15411)",\n', ""
    )
    assert mutated_gate != _GATE_MODULE.read_text(encoding="utf-8")
    gate_path = _write_gate_module(tmp_path, mutated_gate)
    failures = wiring.check_wiring(_CI_YAML, gate_module_path=gate_path)
    assert any("STRICT_GATE_JOBS" in f for f in failures), failures


@pytest.mark.unit
def test_job_registered_in_strict_gate_jobs_passes(tmp_path: Path) -> None:
    """GREEN-after control for the STRICT_GATE_JOBS registration check."""
    gate_path = _write_gate_module(tmp_path, _GATE_MODULE.read_text(encoding="utf-8"))
    failures = wiring.check_wiring(_CI_YAML, gate_module_path=gate_path)
    assert failures == []


@pytest.mark.unit
def test_making_the_job_conditional_fails(tmp_path: Path) -> None:
    """An `if:` is precisely how the `test` job came to skip on dev PRs."""
    data = _load()
    data["jobs"][wiring._JOB_ID]["if"] = "github.base_ref != 'dev'"
    failures = wiring.check_wiring(_write(tmp_path, data))
    assert any("`if:`" in f for f in failures), failures


@pytest.mark.unit
def test_adding_a_needs_chain_to_the_job_fails(tmp_path: Path) -> None:
    data = _load()
    data["jobs"][wiring._JOB_ID]["needs"] = ["zone-filter"]
    failures = wiring.check_wiring(_write(tmp_path, data))
    assert any("`needs:`" in f for f in failures), failures


@pytest.mark.unit
def test_job_that_no_longer_runs_the_corpus_module_fails(tmp_path: Path) -> None:
    """A green context that stopped executing the ratchets proves nothing."""
    data = _load()
    for step in data["jobs"][wiring._JOB_ID]["steps"]:
        if "run" in step and "corpus_baseline" in step["run"]:
            step["run"] = "uv run pytest tests/unit/scripts/test_x.py -q\n"
    failures = wiring.check_wiring(_write(tmp_path, data))
    assert any("does not run" in f for f in failures), failures


@pytest.mark.unit
def test_job_that_drops_its_own_wiring_step_fails(tmp_path: Path) -> None:
    """The job must re-assert wiring on every PR, not only ci.yml-touching PRs."""
    data = _load()
    job = data["jobs"][wiring._JOB_ID]
    job["steps"] = [
        s
        for s in job["steps"]
        if not ("run" in s and "check_corpus_ratchet_wiring.py" in s["run"])
    ]
    failures = wiring.check_wiring(_write(tmp_path, data))
    assert any("check_corpus_ratchet_wiring.py" in f for f in failures), failures


@pytest.mark.unit
def test_missing_ci_yaml_raises_never_exits_zero(tmp_path: Path) -> None:
    """Absent gate must be indistinguishable from a failing one (OMN-14666).

    This test found a real defect in the first draft: ``main()`` filtered argv
    on ``endswith("ci.yml")`` and fell back to the canonical repo path when
    nothing matched, so a missing or renamed target printed PASSED and exited 0
    for a file the caller never named.
    """
    with pytest.raises(wiring.CiWorkflowUnreadableError):
        wiring.check_wiring(tmp_path / "does-not-exist.yml")
    assert wiring.main([str(tmp_path / "does-not-exist.yml")]) == 1


@pytest.mark.unit
def test_unparseable_ci_yaml_exits_nonzero(tmp_path: Path) -> None:
    bad = tmp_path / "ci.yml"
    bad.write_text("jobs: [not, a, mapping]\n", encoding="utf-8")
    assert wiring.main([str(bad)]) == 1


@pytest.mark.unit
def test_main_returns_zero_on_the_live_file() -> None:
    assert wiring.main([str(_CI_YAML)]) == 0


@pytest.mark.unit
def test_hook_is_registered_in_precommit_config() -> None:
    """A validator nobody invokes is not a mechanism."""
    cfg = yaml.safe_load(
        (_REPO_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    )
    hooks = [
        hook
        for repo in cfg.get("repos", [])
        for hook in (repo.get("hooks") or [])
        if hook.get("id") == "check-corpus-ratchet-wiring"
    ]
    assert len(hooks) == 1, "check-corpus-ratchet-wiring hook must be registered once"
    hook = hooks[0]
    assert "check_corpus_ratchet_wiring.py" in hook["entry"]
    assert hook["stages"] == ["pre-commit"]
    # Must match ci.yml, and only ci.yml -- broader scope would run the check on
    # unrelated files; narrower (or absent) means edits to ci.yml go unchecked.
    pattern = re.compile(hook["files"])
    assert pattern.search(".github/workflows/ci.yml")
    assert not pattern.search(".github/workflows/product-readiness-shadow.yml")


@pytest.mark.unit
@pytest.mark.parametrize("flag", ["-h", "--help"])
def test_help_flag_exits_zero_and_prints_usage(
    flag: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """The Pre-commit changed-validator step runs every modified gate with --help.

    CI caught this: without an explicit branch, `--help` was treated as a path
    and the gate failed itself with "CORPUS-RATCHET WIRING GATE FAILED (--help):
    --help does not exist", which the step reports as "the gate itself is
    broken".
    """
    assert wiring.main([flag]) == 0
    out = capsys.readouterr().out
    assert "usage: check_corpus_ratchet_wiring.py" in out
    assert wiring._JOB_ID in out
