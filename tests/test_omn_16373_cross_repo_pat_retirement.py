# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Ratchet: OMN-16373's CROSS_REPO_PAT retirement in OCC's own workflows.

OMN-16373 migrated this repo's cross-repo CI reads off the org-wide
``CROSS_REPO_PAT`` secret onto a short-lived ``onexbot-occ-writer`` GitHub App
installation token minted per job with ``actions/create-github-app-token``.
The change lives entirely in ``.github/workflows/`` — the workflow definition
*is* the product here, so exercising it means asserting against the parsed
workflow documents, not against a PR's merge state.

Every assertion below is falsifiable by reverting the migration:

* re-add a ``secrets.CROSS_REPO_PAT`` read to any migrated job and
  ``test_cross_repo_pat_reads_confined_to_sanctioned_job`` goes red;
* delete or unpin a mint step and ``test_migrated_workflows_mint_app_token``
  goes red;
* mint a token nothing consumes (a swap that only *looks* migrated) and
  ``test_every_minted_token_is_consumed_in_its_own_job`` goes red;
* drop the ``|| github.token`` degrade path on the one sanctioned residual and
  ``test_sanctioned_residual_still_degrades_to_github_token`` goes red.

The single sanctioned residual is ``check-platform-leads-review-tripwire``:
its two GitHub calls need ``Organization -> Members: read`` and
``Repository -> Administration: read``, neither of which the App's permission
set (``contents:write`` / ``metadata:read`` / ``pull_requests:write``) grants.
Migrating it is blocked on an org-owner permission grant on installation
148180820, so it is allowlisted here **by exact count** — the allowlist is a
ratchet, not an amnesty: a third reference in that job fails too.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

import pytest
import yaml

if TYPE_CHECKING:
    from collections.abc import Iterator

REPO_ROOT: Final[Path] = Path(__file__).parent.parent.resolve()
WORKFLOWS_DIR: Final[Path] = REPO_ROOT / ".github" / "workflows"

#: The pinned App-token minting action. Pinning to a 40-hex commit SHA (not a
#: tag) is the supply-chain requirement for an action handed an App private key.
#: The S105 suppressions below are on names, not values: these constants hold an
#: action reference and two secret *names*, never a credential.
APP_TOKEN_ACTION: Final[str] = "actions/create-github-app-token"  # noqa: S105

#: Workflows OMN-16373 migrated in this repo (OCC's own carrier, PR #6844).
MIGRATED_WORKFLOWS: Final[tuple[str, ...]] = (
    "ci.yml",
    "nightly-promote.yml",
    "staleness-monitor.yml",
)

#: The only ``secrets.CROSS_REPO_PAT`` reads that may survive, keyed by
#: ``(workflow file, job id)`` with the exact number of referencing values.
#: Both live in the tripwire job's step ``env`` (``GH_TOKEN`` and the
#: ``TOKEN_SOURCE`` diagnostic).
SANCTIONED_PAT_READS: Final[dict[tuple[str, str], int]] = {
    ("ci.yml", "check-platform-leads-review-tripwire"): 2,
}

_PAT_REFERENCE: Final[str] = "secrets.CROSS_REPO_PAT"
_SHA_PIN: Final[re.Pattern[str]] = re.compile(
    rf"^{re.escape(APP_TOKEN_ACTION)}@[0-9a-f]{{40}}$"
)
_APP_ID_SECRET: Final[str] = "secrets.ONEXBOT_OCC_APP_ID"  # noqa: S105
_PRIVATE_KEY_SECRET: Final[str] = "secrets.ONEXBOT_OCC_PRIVATE_KEY"  # noqa: S105


def _workflow_paths() -> list[Path]:
    """Every workflow definition in this repo, sorted for stable failure text."""
    return sorted(
        path
        for path in WORKFLOWS_DIR.iterdir()
        if path.is_file() and path.suffix in {".yml", ".yaml"}
    )


def _load(path: Path) -> dict[str, Any]:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(document, dict), f"{path.name} did not parse to a mapping"
    return document


def _jobs(document: dict[str, Any]) -> dict[str, Any]:
    jobs = document.get("jobs") or {}
    return jobs if isinstance(jobs, dict) else {}


def _strings(node: Any) -> Iterator[str]:
    """Yield every string scalar reachable from ``node``.

    Comments never survive ``yaml.safe_load``, so prose *about* the retired
    secret cannot make this ratchet fire — only a live template expression can.
    """
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from _strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from _strings(value)


def _steps(job: Any) -> list[dict[str, Any]]:
    if not isinstance(job, dict):
        return []
    steps = job.get("steps") or []
    return (
        [step for step in steps if isinstance(step, dict)]
        if isinstance(steps, list)
        else []
    )


def _mint_steps(job: Any) -> list[dict[str, Any]]:
    return [
        step
        for step in _steps(job)
        if isinstance(step.get("uses"), str)
        and step["uses"].split("#", 1)[0].strip().startswith(f"{APP_TOKEN_ACTION}@")
    ]


@pytest.mark.unit
def test_cross_repo_pat_reads_confined_to_sanctioned_job() -> None:
    """No workflow job reads ``secrets.CROSS_REPO_PAT`` outside the allowlist."""
    observed: dict[tuple[str, str], int] = {}
    for path in _workflow_paths():
        document = _load(path)
        for job_id, job in _jobs(document).items():
            count = sum(1 for value in _strings(job) if _PAT_REFERENCE in value)
            if count:
                observed[(path.name, str(job_id))] = count
        # A read declared above the job level (workflow ``env:``, reusable
        # ``with:``) would leak to every job, so it is never sanctioned.
        top_level = {key: value for key, value in document.items() if key != "jobs"}
        leaked = [value for value in _strings(top_level) if _PAT_REFERENCE in value]
        assert not leaked, (
            f"{path.name}: workflow-level CROSS_REPO_PAT reference(s) outside any job "
            f"(OMN-16373 retired these): {leaked}"
        )

    assert observed == SANCTIONED_PAT_READS, (
        "OMN-16373 CROSS_REPO_PAT retirement regressed.\n"
        f"  expected: {SANCTIONED_PAT_READS}\n"
        f"  observed: {observed}\n"
        "Every job outside the allowlist must mint an onexbot-occ-writer App "
        "token instead. The allowlist entry is exact-count: adding a third "
        "reference to the sanctioned job fails too. Only an org-owner grant of "
        "Organization->Members:read + Repository->Administration:read on "
        "installation 148180820 can retire the remaining entry."
    )


@pytest.mark.unit
@pytest.mark.parametrize("workflow_name", MIGRATED_WORKFLOWS)
def test_migrated_workflows_mint_app_token(workflow_name: str) -> None:
    """Each migrated workflow mints the App token from a SHA-pinned action."""
    path = WORKFLOWS_DIR / workflow_name
    document = _load(path)

    mint_steps = [step for job in _jobs(document).values() for step in _mint_steps(job)]
    assert mint_steps, (
        f"{workflow_name}: no {APP_TOKEN_ACTION} step. OMN-16373 replaced this "
        "workflow's CROSS_REPO_PAT reads with a minted onexbot-occ-writer App token."
    )

    for step in mint_steps:
        uses = step["uses"].split("#", 1)[0].strip()
        assert _SHA_PIN.match(uses), (
            f"{workflow_name}: {APP_TOKEN_ACTION} must be pinned to a 40-hex commit "
            f"SHA (it receives the App private key); found {uses!r}"
        )
        step_id = step.get("id")
        assert isinstance(step_id, str), (
            f"{workflow_name}: the mint step needs an `id` — nothing can reference "
            "`steps.<id>.outputs.token` without one"
        )
        assert step_id, f"{workflow_name}: the mint step's `id` must not be empty"
        inputs = step.get("with") or {}
        rendered = " ".join(_strings(inputs))
        assert _APP_ID_SECRET in rendered, (
            f"{workflow_name}: mint step {step_id!r} must read the App id from "
            f"{_APP_ID_SECRET}; got with: {inputs!r}"
        )
        assert _PRIVATE_KEY_SECRET in rendered, (
            f"{workflow_name}: mint step {step_id!r} must read the App private key "
            f"from {_PRIVATE_KEY_SECRET}; got with: {inputs!r}"
        )


@pytest.mark.unit
def test_every_minted_token_is_consumed_in_its_own_job() -> None:
    """A mint step with no consumer is a migration that only looks migrated."""
    orphans: list[str] = []
    for path in _workflow_paths():
        for job_id, job in _jobs(_load(path)).items():
            for step in _mint_steps(job):
                step_id = step.get("id")
                if not isinstance(step_id, str) or not step_id:
                    orphans.append(f"{path.name}:{job_id} (mint step has no id)")
                    continue
                consumer = f"steps.{step_id}.outputs.token"
                if not any(consumer in value for value in _strings(job)):
                    orphans.append(f"{path.name}:{job_id} -> {consumer}")

    assert not orphans, (
        "Minted App tokens that no step in the same job consumes — the job still "
        "runs on whatever token it used before, so the OMN-16373 migration is "
        f"cosmetic there: {orphans}"
    )


@pytest.mark.unit
def test_sanctioned_residual_still_degrades_to_github_token() -> None:
    """The one surviving PAT read must fall back to ``github.token``.

    OMN-16373's terminal step is deleting the org-wide secret. That deletion
    must degrade this job, not break it: with the secret gone the expression
    resolves to the empty string and the fallback has to carry the read.
    """
    workflow_name, job_id = next(iter(SANCTIONED_PAT_READS))
    job = _jobs(_load(WORKFLOWS_DIR / workflow_name))[job_id]
    reads = [value for value in _strings(job) if _PAT_REFERENCE in value]

    assert reads, f"{workflow_name}:{job_id} no longer reads {_PAT_REFERENCE}"

    fallbacks = [value for value in reads if "github.token" in value]
    assert fallbacks, (
        f"{workflow_name}:{job_id} reads {_PAT_REFERENCE} with no `|| github.token` "
        "fallback. Deleting the org-wide secret (this ticket's terminal step) would "
        "then hard-fail the job instead of degrading it."
    )
