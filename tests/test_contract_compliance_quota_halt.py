# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""OMN-20503 -- a spent GitHub App quota halts the ``gh`` checks of one run.

Run 37171017353 (PR #12717) executed 1,753 ``gh api`` observation checks, hit
``API rate limit exceeded for installation ID 148180820`` and then made 952 more
``gh`` calls, every one a 403. Nothing remembered the quota 403, so each later
check called GitHub again. The breaker is per run, trips on the PRIMARY
rate-limit message only, and never skips a check that does not call ``gh``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
import yaml

from onex_change_control.scripts import contract_compliance_check as mod

if TYPE_CHECKING:
    from pathlib import Path

_REPO = "OmniNode-ai/onex_change_control"
_PR = 12717
_TICKET = "OMN-20503"
_SHAS = [f"{n:040x}" for n in range(1, 6)]
_QUOTA_BODY = (
    '{"message": "API rate limit exceeded for installation ID 148180820. '
    "If you reach out to GitHub Support for help, please include the request "
    'ID ABCD:1234 and timestamp 2026-10-04 02:38:50 UTC.", "status": "403"}'
)
_SECONDARY_BODY = (
    '{"message": "You have exceeded a secondary rate limit. Please wait a few '
    'minutes before you try again.", "status": "403"}'
)
_PERMISSION_BODY = (
    '{"message": "Resource not accessible by integration", "status": "403"}'
)


def _write_contract(contracts_dir: Path) -> None:
    items: list[dict[str, Any]] = [
        {
            "id": f"obs-{i}",
            "description": f"observation {i}",
            "source": "generated",
            "checks": [
                {
                    "check_type": "command",
                    "check_value": f"gh api repos/{_REPO}/commits/{sha} --jq .sha",
                }
            ],
        }
        for i, sha in enumerate(_SHAS)
    ]
    items.append(
        {
            "id": "local-grep",
            "description": "a check that never calls gh",
            "source": "generated",
            "checks": [{"check_type": "command", "check_value": "grep -q x f.txt"}],
        }
    )
    contract = {
        "schema_version": "1.0.0",
        "ticket_id": _TICKET,
        "title": "quota halt fixture",
        "summary": "quota halt fixture",
        "is_seam_ticket": False,
        "interface_change": False,
        "interfaces_touched": [],
        "evidence_requirements": [],
        "emergency_bypass": {
            "enabled": False,
            "justification": "",
            "follow_up_ticket_id": "",
        },
        "dod_evidence": items,
    }
    contracts_dir.mkdir(parents=True, exist_ok=True)
    (contracts_dir / f"{_TICKET}.yaml").write_text(yaml.safe_dump(contract))


class _Fake:
    """Stands in for ``_run``; records every command string it is asked to run."""

    def __init__(self, gh_replies: list[tuple[int, str, str]]) -> None:
        self.gh_replies = gh_replies
        self.commands: list[str] = []

    @property
    def gh_calls(self) -> list[str]:
        return [c for c in self.commands if mod._command_binaries(c).count("gh")]

    @property
    def other_calls(self) -> list[str]:
        return [c for c in self.commands if c not in self.gh_calls]

    def __call__(self, cmd: list[str], *_a: Any, **_k: Any) -> tuple[int, str, str]:
        command = cmd[-1]
        self.commands.append(command)
        if mod._command_binaries(command).count("gh"):
            index = len(self.gh_calls) - 1
            return self.gh_replies[min(index, len(self.gh_replies) - 1)]
        return 0, "", ""


def _drive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    gh_replies: list[tuple[int, str, str]],
) -> tuple[int, _Fake, str]:
    contracts_dir = tmp_path / "contracts"
    _write_contract(contracts_dir)
    (tmp_path / "f.txt").write_text("x\n")
    fake = _Fake(gh_replies)
    monkeypatch.setattr(mod, "_run", fake)
    monkeypatch.setattr(mod, "_extract_ticket_id", lambda _pr, _repo: _TICKET)
    monkeypatch.setattr(
        mod, "_pr_changed_paths", lambda _pr, _repo: frozenset({"src/x.py"})
    )
    rc = mod.run_compliance_check(_PR, _REPO, contracts_dir, tmp_path)
    return rc, fake, capsys.readouterr().out


@pytest.mark.unit
def test_primary_quota_403_halts_remaining_gh_checks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc, fake, out = _drive(
        tmp_path, monkeypatch, capsys, [(1, "", f"gh: {_QUOTA_BODY} (HTTP 403)")]
    )

    assert len(fake.gh_calls) == 1, fake.gh_calls
    skipped = [line for line in out.splitlines() if "command: QUOTA_EXHAUSTED" in line]
    assert len(skipped) == len(_SHAS) - 1, out
    assert all("[X]" in line for line in skipped)
    assert "API rate limit exceeded for installation ID 148180820" in skipped[0]
    assert "4 QUOTA_EXHAUSTED" in out
    assert rc == 1


@pytest.mark.unit
def test_non_gh_check_still_runs_after_the_halt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, fake, _ = _drive(tmp_path, monkeypatch, capsys, [(1, _QUOTA_BODY, "")])

    assert len(fake.gh_calls) == 1
    assert len(fake.other_calls) == 1
    assert "grep" in fake.other_calls[0]


@pytest.mark.unit
@pytest.mark.parametrize("body", [_SECONDARY_BODY, _PERMISSION_BODY])
def test_secondary_limit_and_scope_denial_do_not_trip_the_breaker(
    body: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, fake, out = _drive(tmp_path, monkeypatch, capsys, [(1, "", body)])

    assert len(fake.gh_calls) == len(_SHAS)
    assert "QUOTA_EXHAUSTED" not in out
