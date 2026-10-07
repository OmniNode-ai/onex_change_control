# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""OMN-15772: the remaining fail-closed acceptance criteria for the
``Migration Inventory Sync`` gate.

Three surfaces, one defect class -- an absent input reading as a clean input:

1. ``check_migration_inventory`` treated an absent peer repo as a
   WARNING-severity ``MISSING_REPO`` and returned EARLY, contributing zero
   findings for that peer. A failed clone and a drift-free peer produced the
   identical downstream signal, which is how three same-head reruns reported
   14 / 17 / 9 errors off an unchanged file set.
2. ``ci.yml``'s ``migration-inventory`` job must run the derivation gate, not
   only the presence validator -- the validator can only judge the files it is
   shown, while the generator judges the committed artifact against the peer
   trees themselves.
3. ``Migration Inventory Sync``'s ``SKIPPABLE_GATE_JOBS`` classification is
   leniency (ii) named in the ticket. It is retained deliberately, and the
   justification is required in writing at the classification site.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from onex_change_control.scripts.check_migration_inventory import validate_inventory

if TYPE_CHECKING:
    from types import ModuleType

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CI_YAML = _REPO_ROOT / ".github" / "workflows" / "ci.yml"
_GATE = _REPO_ROOT / "scripts" / "ci" / "ci_summary_gate.py"


def _load_gate_module() -> ModuleType:
    """Load ``scripts/ci/ci_summary_gate.py`` by path.

    It is a standalone CI script, not part of the installed package, so there
    is no import path to it. Mirrors the loader in
    test_test_typecheck_wiring_omn15731.py, including the ``sys.modules``
    registration Python 3.13's ``@dataclass`` needs.
    """
    spec = importlib.util.spec_from_file_location("ci_summary_gate", _GATE)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _inventory(tmp_path: Path) -> Path:
    path = tmp_path / "inventory.yaml"
    path.write_text(
        "---\n"
        'version: "1"\n'
        "databases:\n"
        "  demo:\n"
        "    connection_env: DEMO_DB_URL\n"
        "    migration_sets:\n"
        "      - source_repo: peer_one\n"
        "        directory: db/migrations\n"
        "        migrations:\n"
        "          - file: 001_a.sql\n",
        encoding="utf-8",
    )
    return path


# ---------------------------------------------------------------------------
# 1. MISSING_REPO is fail-closed
# ---------------------------------------------------------------------------


def test_absent_peer_is_an_error_not_a_warning(tmp_path: Path) -> None:
    """AC: a repos-root missing one expected peer must exit non-zero."""
    result = validate_inventory(_inventory(tmp_path), tmp_path / "repos")
    assert not result.ok
    missing = [f for f in result.findings if f.check == "MISSING_REPO"]
    assert missing, result.findings
    assert all(f.severity == "ERROR" for f in missing), missing


def test_present_peer_still_validates(tmp_path: Path) -> None:
    """GREEN-after control: a fully-present peer set still passes, so the
    fail-closed change above cannot be satisfied by failing unconditionally."""
    repos = tmp_path / "repos"
    migrations = repos / "peer_one" / "db" / "migrations"
    migrations.mkdir(parents=True)
    (migrations / "001_a.sql").write_text("CREATE TABLE a();", encoding="utf-8")
    result = validate_inventory(_inventory(tmp_path), repos)
    assert result.ok, result.findings


def test_absent_tables_annotation_is_not_a_finding(tmp_path: Path) -> None:
    """``tables:`` moved to the optional annotation layer, so its absence from
    the generated artifact is by design and must not produce a finding."""
    repos = tmp_path / "repos"
    migrations = repos / "peer_one" / "db" / "migrations"
    migrations.mkdir(parents=True)
    (migrations / "001_a.sql").write_text("CREATE TABLE a();", encoding="utf-8")
    result = validate_inventory(_inventory(tmp_path), repos)
    assert not [f for f in result.findings if "tables" in f.detail]


# ---------------------------------------------------------------------------
# 2. ci.yml runs the derivation gate
# ---------------------------------------------------------------------------


def _job(job_id: str) -> dict[str, Any]:
    jobs = yaml.safe_load(_CI_YAML.read_text(encoding="utf-8"))["jobs"]
    assert job_id in jobs, f"{job_id} is not a job in ci.yml"
    return dict(jobs[job_id])


def test_ci_runs_the_derivation_gate() -> None:
    """The job must invoke the generator in check mode against the same
    repos-root the clone step produced."""
    run_bodies = " ".join(
        str(step.get("run", "")) for step in _job("migration-inventory")["steps"]
    )
    assert "generate-migration-inventory" in run_bodies
    assert "--check" in run_bodies
    assert "MIGRATION_INVENTORY_REPOS_ROOT" in run_bodies


def test_ci_still_runs_the_presence_validator() -> None:
    """The derivation gate is additive: the per-file validator stays, because
    its findings name individual files while the derivation gate reports a
    whole-file difference."""
    run_bodies = " ".join(
        str(step.get("run", "")) for step in _job("migration-inventory")["steps"]
    )
    assert "check-migration-inventory" in run_bodies


def test_derivation_gate_is_not_soft_failed() -> None:
    """No ``continue-on-error`` or ``|| true`` may re-open the fail-open this
    ticket exists to close."""
    for step in _job("migration-inventory")["steps"]:
        body = str(step.get("run", ""))
        if "generate-migration-inventory" in body:
            assert step.get("continue-on-error") is not True
            assert "|| true" not in body
            assert "2>/dev/null" not in body
            return
    msg = "no step invokes generate-migration-inventory"
    raise AssertionError(msg)


# ---------------------------------------------------------------------------
# 3. SKIPPABLE classification carries its justification in writing
# ---------------------------------------------------------------------------


def test_migration_inventory_sync_classification_is_justified_in_writing() -> None:
    """AC: reclassify STRICT, or justify SKIPPABLE in writing.

    SKIPPABLE is retained. STRICT would treat a ``skipped`` conclusion as a
    failure, and this job legitimately skips on the docs_only evidence-only
    fast lane -- promoting it would wedge every evidence-companion PR in a repo
    whose merge traffic is mostly evidence companions. The leniency the ticket
    recorded ("its failure does not fail CI Summary") does not describe the
    current aggregator: ``evaluate()`` fails the run on any SKIPPABLE gate
    whose conclusion is not in ``GOOD_CONCLUSIONS``. Only a *skip* is
    tolerated, and the reason is pinned here so the next reader does not
    re-derive it.
    """
    body = _GATE.read_text(encoding="utf-8")
    marker = '    "Migration Inventory Sync",'
    assert marker in body
    head, _, _ = body.partition(marker)
    # The justification must sit in the comment block immediately above the
    # entry, not somewhere else in the file.
    preamble = head.rsplit('    "Migration Conflict Check",', 1)[-1]
    assert "OMN-15772" in preamble, (
        "the Migration Inventory Sync entry must carry its OMN-15772 "
        "SKIPPABLE-retention justification immediately above it"
    )


def test_a_failing_skippable_gate_still_fails_ci_summary() -> None:
    """The load-bearing half of the justification above, proven rather than
    asserted: a FAILED ``Migration Inventory Sync`` fails CI Summary today."""
    gate = _load_gate_module()
    jobs: list[dict[str, object]] = [
        {"name": name, "status": "completed", "conclusion": "success"}
        for name in gate.GATE_JOBS
    ]
    all_green, _ = gate.evaluate(list(jobs))
    assert all_green == 0, "control: an all-green snapshot must pass"

    reddened = [
        {**job, "conclusion": "failure"}
        if job["name"] == "Migration Inventory Sync"
        else job
        for job in jobs
    ]
    exit_code, report = gate.evaluate(reddened)
    assert exit_code != 0, report


def test_migration_inventory_sync_is_classified_exactly_once() -> None:
    """Drift pin: the job stays in exactly one tier."""
    body = _GATE.read_text(encoding="utf-8")
    assert body.count('    "Migration Inventory Sync",') == 1
