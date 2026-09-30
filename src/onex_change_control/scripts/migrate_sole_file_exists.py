# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Rewrite ``dod_evidence`` items whose only check is ``file_exists`` (OMN-20136).

omnibase_core 0.47.26 (``ModelDodEvidenceItem.reject_sole_file_exists_check``,
``model_dod_evidence_item.py``) refuses a ``dod_evidence`` item whose only
``check_type`` is ``file_exists``. The rule offers no retirement path and this
repo carries no exemption list, so each such item is rewritten in place to a
check that inspects content instead of presence:

* receipt path (``drift/dod_receipts/...``): ``grep`` for ``status: PASS`` in
  that receipt, so the item asserts the recorded verdict, not that a file
  exists. A receipt that is not PASS needs an entry in
  ``PENDING_RECEIPT_COMMANDS`` or the script refuses.
* the contract's own file: ``grep`` for its ``ticket_id`` line.
* a test file: ``test_exists`` on the same path.
* a file that resolves under ``$OMNI_HOME`` today: ``grep`` for the first line
  of the file that is not licence boilerplate.
* a file no longer under ``$OMNI_HOME``: ``command`` reading the file from the
  commit that added it, ``git cat-file -e <sha>:<path>``.

The edit is textual so untouched YAML keeps its formatting. ``--check`` exits 1
if any sole-``file_exists`` item remains.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Any

import yaml

# Files no longer present under OMNI_HOME, mapped to the commit that added them.
MOVED_ARTIFACT_ADD_COMMIT = {
    "docs/governance/2026-04-27-required-gates-rollout.md": (
        "3c90f0df5ef76352a5de9b3e8c045592ea018120"
    ),
    "docs/tracking/2026-04-26-contract-centralization-inventory.md": (
        "26c01156fd9291a079163b6779ed9d1de8708ead"
    ),
}

# Receipts that record PENDING, not PASS: a ``status: PASS`` grep would assert
# what the receipt denies. Each is rewritten to the executable claim its item
# states, keyed by receipt path.
PENDING_RECEIPT_COMMANDS = {
    "drift/dod_receipts/OMN-9601/dod-003/command.yaml": (
        'test "$(gh pr view 901 --repo OmniNode-ai/omnibase_core --json state'
        ' -q .state)"',
        "= MERGED",
    ),
}

_CHECK_RE = re.compile(
    r"^(?P<i>[ ]*)- check_type: (?P<q>[\"']?)file_exists(?P=q)[ ]*\n"
    r"(?P<j>[ ]*)check_value: (?P<v>.+?)[ ]*\n",
    re.MULTILINE,
)


class MigrationError(Exception):
    """A sole-file_exists item that has no faithful rewrite."""


def _sole_items(data: dict[str, Any]) -> list[str]:
    ids = []
    for item in data.get("dod_evidence") or []:
        types = {c.get("check_type") for c in item.get("checks") or []}
        if types and types <= {"file_exists"}:
            ids.append(item["id"])
    return ids


def _unquote(raw: str) -> str:
    value = yaml.safe_load(raw)
    if not isinstance(value, str):
        msg = f"unexpected check_value {raw!r}"
        raise MigrationError(msg)
    return value


def _grep(indent: str, pattern: str, path: str) -> str:
    quoted = "'" + pattern.replace("'", "''") + "'"
    return (
        f"{indent}- check_type: grep\n"
        f"{indent}  check_value:\n"
        f"{indent}    pattern: {quoted}\n"
        f"{indent}    path: {path}\n"
    )


def _plain(indent: str, check_type: str, value: str) -> str:
    return f"{indent}- check_type: {check_type}\n{indent}  check_value: {value}\n"


def _is_test_path(path: str) -> bool:
    name = Path(path).name
    return "/tests/" in f"/{path}" or name.startswith("test_") or ".test." in name


def _anchor(target: Path) -> str:
    for line in target.read_text().splitlines():
        text = line.strip()
        if text and "'" not in text and "SPDX" not in text and text != "---":
            return text
    msg = f"{target}: no usable anchor line"
    raise MigrationError(msg)


def _replacement(ticket: str, path: str, indent: str, omni_home: Path) -> str:
    if path in PENDING_RECEIPT_COMMANDS:
        head, tail = PENDING_RECEIPT_COMMANDS[path]
        folded = f"'{head}\n{indent}    {tail}'"
        return _plain(indent, "command", folded)
    if path.startswith("drift/dod_receipts/"):
        if not re.search(r"^status: PASS$", Path(path).read_text(), re.MULTILINE):
            msg = f"{path}: receipt is not PASS and has no recorded replacement"
            raise MigrationError(msg)
        return _grep(indent, "status: PASS", path)
    if path == f"contracts/{ticket}.yaml":
        return _grep(indent, f"ticket_id: {ticket}", path)
    if _is_test_path(path):
        return _plain(indent, "test_exists", path)
    if path in MOVED_ARTIFACT_ADD_COMMIT:
        sha = MOVED_ARTIFACT_ADD_COMMIT[path]
        cmd = f"git -C ${{OMNI_HOME}} cat-file -e {sha}:{path}"
        return _plain(indent, "command", cmd)
    target = omni_home / path
    if not target.is_file():
        msg = f"{path}: not under OMNI_HOME and no recorded add commit"
        raise MigrationError(msg)
    return _grep(indent, _anchor(target), path)


def migrate(contract: Path, omni_home: Path) -> int:
    text = contract.read_text()
    data = yaml.safe_load(text)
    ticket = data["ticket_id"]
    targets = set(_sole_items(data))
    if not targets:
        return 0
    count = 0

    def sub(match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        path = _unquote(match.group("v"))
        return _replacement(ticket, path, match.group("i"), omni_home)

    out = []
    # Split at each item so only sole-file_exists items are touched.
    for part in re.split(r"(?m)^(?=[ ]*- id: )", text):
        head = re.match(r"[ ]*- id: [\"']?([^\s\"']+)", part)
        out.append(
            _CHECK_RE.sub(sub, part) if head and head.group(1) in targets else part
        )
    contract.write_text("".join(out))
    return count


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--check", action="store_true")
    default = sorted(str(p) for p in Path("contracts").glob("OMN-*.yaml"))
    parser.add_argument("paths", nargs="*", default=default)
    args = parser.parse_args()
    paths = [Path(p) for p in args.paths]
    if args.check:
        remaining = sum(len(_sole_items(yaml.safe_load(p.read_text()))) for p in paths)
        print(f"sole file_exists items remaining: {remaining}")
        return 1 if remaining else 0
    omni_home = Path(os.environ["OMNI_HOME"])
    total = sum(migrate(p, omni_home) for p in paths)
    print(f"rewrote {total} checks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
