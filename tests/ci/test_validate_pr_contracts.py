# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Tests for Layer 3 — code-change-to-contract-sync gate.

Covers:
  - handler changed + no contract → FAIL (dod-001)
  - handler changed + contract changed → PASS
  - test-only change → SKIP (dod-002)
  - skill file changed + no contract → FAIL
  - non-handler diff → SKIP
  - handler diff < 10 lines with no new def → SKIP (rename heuristic)
  - handler diff ≥ 10 lines without contract → FAIL
"""

from __future__ import annotations

from onex_change_control.scripts.validate_pr_contracts import (
    validate_contract_sync,
)

# ---------------------------------------------------------------------------
# Fixtures — fake diff content
# ---------------------------------------------------------------------------


def _make_diff(
    path: str,
    added_lines: list[str] | None = None,
    removed_lines: list[str] | None = None,
) -> str:
    """Build a minimal unified diff for a single file."""
    added = added_lines or ["+ pass"]
    removed = removed_lines or []
    hunk_lines = "\n".join(removed + added)
    return (
        f"diff --git a/{path} b/{path}\n"
        f"--- a/{path}\n"
        f"+++ b/{path}\n"
        f"@@ -1,5 +1,5 @@\n"
        f"{hunk_lines}\n"
    )


# ---------------------------------------------------------------------------
# dod-001: handler changed + no contract → FAIL
# ---------------------------------------------------------------------------


class TestHandlerNoContractFails:
    """Handler modified without co-touching contract.yaml must fail."""

    def test_handler_py_no_contract(self) -> None:
        changed_files = [
            "src/omnimarket/nodes/node_foo/handler.py",
        ]
        diff_content = _make_diff(
            "src/omnimarket/nodes/node_foo/handler.py",
            added_lines=[
                "+def new_method(self):",
                "+    return 42",
                "+",
                "+def another_method(self):",
                "+    pass",
                "+",
                "+# some comment",
                "+x = 1",
                "+y = 2",
                "+z = 3",
            ],
        )
        findings = validate_contract_sync(changed_files, diff_content)
        assert len(findings) == 1
        assert findings[0].level == "BLOCK"
        assert "contract" in findings[0].message.lower()

    def test_handlers_dir_no_contract(self) -> None:
        changed_files = [
            "src/omnimarket/nodes/node_bar/handlers/process.py",
        ]
        diff_content = _make_diff(
            "src/omnimarket/nodes/node_bar/handlers/process.py",
            added_lines=[
                "+async def handle_event(self, event):",
                "+    await self.process(event)",
                "+",
                "+async def validate(self, data):",
                "+    return True",
                "+",
                "+# placeholder",
                "+a = 1",
                "+b = 2",
                "+c = 3",
            ],
        )
        findings = validate_contract_sync(changed_files, diff_content)
        assert len(findings) == 1
        assert findings[0].level == "BLOCK"


# ---------------------------------------------------------------------------
# dod-002: test-only change → SKIP
# ---------------------------------------------------------------------------


class TestTestOnlySkips:
    """Changes only under tests/ should not trigger the gate."""

    def test_test_handler_file_skips(self) -> None:
        changed_files = [
            "tests/nodes/node_foo/test_handler.py",
        ]
        diff_content = _make_diff(
            "tests/nodes/node_foo/test_handler.py",
            added_lines=["+def test_something(): pass"] * 15,
        )
        findings = validate_contract_sync(changed_files, diff_content)
        assert len(findings) == 0

    def test_test_dir_handler_file_skips(self) -> None:
        changed_files = [
            "tests/handlers/test_process.py",
        ]
        diff_content = _make_diff(
            "tests/handlers/test_process.py",
            added_lines=["+def test_something(): pass"] * 15,
        )
        findings = validate_contract_sync(changed_files, diff_content)
        assert len(findings) == 0


# ---------------------------------------------------------------------------
# handler changed + contract changed → PASS
# ---------------------------------------------------------------------------


class TestHandlerWithContractPasses:
    """Handler + sibling contract both in diff → no findings."""

    def test_handler_and_contract_cotouched(self) -> None:
        changed_files = [
            "src/omnimarket/nodes/node_foo/handler.py",
            "src/omnimarket/nodes/node_foo/contract.yaml",
        ]
        diff_content = _make_diff(
            "src/omnimarket/nodes/node_foo/handler.py",
            added_lines=["+def new_method(): pass"] * 12,
        ) + _make_diff(
            "src/omnimarket/nodes/node_foo/contract.yaml", added_lines=["+ version: 2"]
        )
        findings = validate_contract_sync(changed_files, diff_content)
        assert len(findings) == 0

    def test_handler_in_subdir_and_parent_contract(self) -> None:
        changed_files = [
            "src/omnimarket/nodes/node_foo/handlers/process.py",
            "src/omnimarket/nodes/node_foo/contract.yaml",
        ]
        diff_content = _make_diff(
            "src/omnimarket/nodes/node_foo/handlers/process.py",
            added_lines=["+async def handle(): pass"] * 12,
        ) + _make_diff(
            "src/omnimarket/nodes/node_foo/contract.yaml", added_lines=["+ version: 2"]
        )
        findings = validate_contract_sync(changed_files, diff_content)
        assert len(findings) == 0


# ---------------------------------------------------------------------------
# non-handler diff → SKIP
# ---------------------------------------------------------------------------


class TestNonHandlerSkips:
    """Files that aren't handlers or skills should not trigger the gate."""

    def test_utility_module_skips(self) -> None:
        changed_files = [
            "src/omnimarket/utils/helpers.py",
        ]
        diff_content = _make_diff(
            "src/omnimarket/utils/helpers.py",
            added_lines=["+def helper(): pass"] * 15,
        )
        findings = validate_contract_sync(changed_files, diff_content)
        assert len(findings) == 0

    def test_readme_skips(self) -> None:
        changed_files = ["README.md"]
        diff_content = _make_diff("README.md", added_lines=["+ new docs"])
        findings = validate_contract_sync(changed_files, diff_content)
        assert len(findings) == 0


# ---------------------------------------------------------------------------
# Skill file changed + no contract → FAIL
# ---------------------------------------------------------------------------


class TestSkillNoContractFails:
    """Skill SKILL.md changed without co-touching contract.yaml must fail."""

    def test_skill_md_no_contract(self) -> None:
        changed_files = [
            "plugins/onex/skills/my_skill/SKILL.md",
        ]
        diff_content = _make_diff(
            "plugins/onex/skills/my_skill/SKILL.md",
            added_lines=["+ new trigger pattern"] * 12,
        )
        findings = validate_contract_sync(changed_files, diff_content)
        assert len(findings) == 1
        assert findings[0].level == "BLOCK"

    def test_skill_md_with_contract_passes(self) -> None:
        changed_files = [
            "plugins/onex/skills/my_skill/SKILL.md",
            "plugins/onex/skills/my_skill/contract.yaml",
        ]
        diff_content = _make_diff(
            "plugins/onex/skills/my_skill/SKILL.md", added_lines=["+ updated"] * 12
        ) + _make_diff(
            "plugins/onex/skills/my_skill/contract.yaml", added_lines=["+ version: 2"]
        )
        findings = validate_contract_sync(changed_files, diff_content)
        assert len(findings) == 0


# ---------------------------------------------------------------------------
# Small handler diff heuristic — rename/type fix → SKIP
# ---------------------------------------------------------------------------


class TestSmallDiffHeuristic:
    """Handler diff < 10 lines with no new def/async def → skip."""

    def test_small_rename_diff_skips(self) -> None:
        changed_files = [
            "src/omnimarket/nodes/node_foo/handler.py",
        ]
        diff_content = _make_diff(
            "src/omnimarket/nodes/node_foo/handler.py",
            added_lines=[
                "+# renamed variable",
                "+x: int = 1",
            ],
        )
        findings = validate_contract_sync(changed_files, diff_content)
        assert len(findings) == 0

    def test_small_diff_with_new_def_fails(self) -> None:
        changed_files = [
            "src/omnimarket/nodes/node_foo/handler.py",
        ]
        diff_content = _make_diff(
            "src/omnimarket/nodes/node_foo/handler.py",
            added_lines=[
                "+def new_public_method():",
                "+    pass",
            ],
        )
        findings = validate_contract_sync(changed_files, diff_content)
        assert len(findings) == 1
        assert findings[0].level == "BLOCK"

    def test_large_diff_without_def_fails(self) -> None:
        changed_files = [
            "src/omnimarket/nodes/node_foo/handler.py",
        ]
        diff_content = _make_diff(
            "src/omnimarket/nodes/node_foo/handler.py",
            added_lines=[f"+line_{i} = {i}" for i in range(15)],
        )
        findings = validate_contract_sync(changed_files, diff_content)
        assert len(findings) == 1
        assert findings[0].level == "BLOCK"
