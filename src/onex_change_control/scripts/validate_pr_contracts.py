# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""validate_pr_contracts.py — PR-level contract validation gate.

Layer 3: Code-change-to-contract-sync gate.
If a PR modifies handler*.py or handlers/*.py, the sibling contract.yaml
must also appear in the PR diff. Same for skill SKILL.md files.

Usage (CI):
    python -m onex_change_control.scripts.validate_pr_contracts \\
        --diff-files file1.py file2.py \\
        --diff-content <path-to-unified-diff>

Exit codes:
    0  All checks pass
    1  One or more BLOCK-level findings
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import PurePosixPath

_HANDLER_FILE_PATTERN = re.compile(
    r"(?:^|/)handler[^/]*\.py$"
    r"|"
    r"(?:^|/)handlers/[^/]+\.py$",
)

_SKILL_FILE_PATTERN = re.compile(
    r"(?:^|/)skills/[^/]+/SKILL\.md$",
)

_TEST_PATH_PATTERN = re.compile(r"(?:^|/)tests?/")

_DEF_PATTERN = re.compile(r"^\+\s*(?:async\s+)?def\s+\w+", re.MULTILINE)

_SMALL_DIFF_THRESHOLD = 10


@dataclass(frozen=True)
class Finding:
    level: str  # "BLOCK" | "WARN" | "INFO"
    file: str
    message: str


def _is_test_file(path: str) -> bool:
    return bool(_TEST_PATH_PATTERN.search(path))


def _is_handler_file(path: str) -> bool:
    return bool(_HANDLER_FILE_PATTERN.search(path))


def _is_skill_file(path: str) -> bool:
    return bool(_SKILL_FILE_PATTERN.search(path))


def _extract_file_diff(diff_content: str, file_path: str) -> str:
    """Extract the diff hunk for a specific file from unified diff content."""
    marker = f"diff --git a/{file_path}"
    start = diff_content.find(marker)
    if start == -1:
        return ""
    next_diff = diff_content.find("\ndiff --git ", start + 1)
    if next_diff == -1:
        return diff_content[start:]
    return diff_content[start:next_diff]


def _count_added_lines(file_diff: str) -> int:
    """Count lines starting with + (excluding +++ header)."""
    count = 0
    for line in file_diff.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            count += 1
    return count


def _has_new_def(file_diff: str) -> bool:
    """Check if diff adds any def or async def."""
    return bool(_DEF_PATTERN.search(file_diff))


def _derive_contract_paths(file_path: str) -> list[str]:
    """Derive candidate contract.yaml paths from a handler or skill file path."""
    p = PurePosixPath(file_path)
    candidates: list[str] = []

    if _is_skill_file(file_path):
        candidates.append(str(p.parent / "contract.yaml"))
        return candidates

    # handler.py → same dir contract.yaml
    candidates.append(str(p.parent / "contract.yaml"))

    # handlers/foo.py → parent dir contract.yaml
    if p.parent.name == "handlers":
        candidates.append(str(p.parent.parent / "contract.yaml"))

    return candidates


def validate_contract_sync(
    changed_files: list[str],
    diff_content: str,
) -> list[Finding]:
    """Layer 3: check that handler/skill changes co-touch their contract.

    Returns a list of Finding objects. Empty list = all clear.
    """
    findings: list[Finding] = []
    changed_set = set(changed_files)

    for file_path in changed_files:
        if _is_test_file(file_path):
            continue

        is_handler = _is_handler_file(file_path)
        is_skill = _is_skill_file(file_path)

        if not is_handler and not is_skill:
            continue

        contract_candidates = _derive_contract_paths(file_path)
        contract_found = any(c in changed_set for c in contract_candidates)

        if contract_found:
            continue

        if is_handler:
            file_diff = _extract_file_diff(diff_content, file_path)
            added_count = _count_added_lines(file_diff)
            has_def = _has_new_def(file_diff)

            if added_count < _SMALL_DIFF_THRESHOLD and not has_def:
                continue

        findings.append(
            Finding(
                level="BLOCK",
                file=file_path,
                message=(
                    f"Handler/skill file '{file_path}' modified without updating "
                    f"contract. Expected one of: {contract_candidates}"
                ),
            )
        )

    return findings


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Layer 3: code-change-to-contract-sync gate"
    )
    parser.add_argument(
        "--diff-files",
        nargs="+",
        required=True,
        help="List of changed file paths in the PR",
    )
    parser.add_argument(
        "--diff-content",
        type=str,
        default="",
        help="Path to file containing unified diff content",
    )
    args = parser.parse_args()

    diff_content = ""
    if args.diff_content:
        from pathlib import Path

        diff_path = Path(args.diff_content)
        if diff_path.exists():
            diff_content = diff_path.read_text()

    findings = validate_contract_sync(args.diff_files, diff_content)

    if not findings:
        print("[PASS] Layer 3: All handler/skill changes have co-touched contracts.")
        return 0

    print(f"[BLOCK] Layer 3: {len(findings)} finding(s):\n")
    for f in findings:
        print(f"  [{f.level}] {f.file}")
        print(f"    {f.message}\n")

    return 1


if __name__ == "__main__":
    sys.exit(main())
