# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""The frozen legacy sole-``file_exists`` baseline behind ``validate-yaml``.

omnibase_core 0.47.26 refuses a ``dod_evidence`` item whose only check type is
``file_exists``. The historical contracts that declare one are append-only, so
``validate-yaml`` withholds those items' ``checks`` from core, and only those.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from onex_change_control.scripts.validate_yaml import (
    validate_file,
    withhold_legacy_sole_file_exists,
)
from onex_change_control.validation.legacy_sole_file_exists import (
    LEGACY_SOLE_FILE_EXISTS_CEILING,
    LEGACY_SOLE_FILE_EXISTS_ITEMS,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
CONTRACTS = REPO_ROOT / "contracts"


def _sole_file_exists_items_in_corpus() -> set[str]:
    found: set[str] = set()
    for path in sorted(CONTRACTS.glob("OMN-*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for item in data.get("dod_evidence") or []:
            checks = item.get("checks") or []
            if checks and {c.get("check_type") for c in checks} == {"file_exists"}:
                found.add(f"{path.stem}:{item.get('id')}")
    return found


@pytest.mark.unit
def test_baseline_names_exactly_the_sole_file_exists_items_in_the_corpus() -> None:
    """No phantom entry survives a migration and no unlisted item slips in."""
    assert _sole_file_exists_items_in_corpus() == set(LEGACY_SOLE_FILE_EXISTS_ITEMS)


@pytest.mark.unit
def test_baseline_can_only_shrink() -> None:
    assert len(LEGACY_SOLE_FILE_EXISTS_ITEMS) <= LEGACY_SOLE_FILE_EXISTS_CEILING


def _item(item_id: str, check_types: list[str]) -> dict[str, object]:
    return {
        "id": item_id,
        "description": "d",
        "checks": [{"check_type": t, "check_value": "v"} for t in check_types],
    }


@pytest.mark.unit
def test_shim_withholds_checks_only_for_a_baseline_item() -> None:
    baseline_key = sorted(LEGACY_SOLE_FILE_EXISTS_ITEMS)[0]
    ticket, item_id = baseline_key.split(":")
    data: dict[str, object] = {
        "dod_evidence": [
            _item(item_id, ["file_exists"]),
            _item("dod-new", ["file_exists"]),
        ]
    }
    out = withhold_legacy_sole_file_exists(data, ticket)
    listed = out["dod_evidence"]
    assert isinstance(listed, list)
    assert "checks" not in listed[0]
    assert listed[0]["id"] == item_id
    assert listed[1] == data["dod_evidence"][1]  # type: ignore[index]


@pytest.mark.unit
def test_shim_leaves_a_baseline_item_alone_once_it_has_a_stronger_check() -> None:
    ticket, item_id = sorted(LEGACY_SOLE_FILE_EXISTS_ITEMS)[0].split(":")
    data: dict[str, object] = {
        "dod_evidence": [_item(item_id, ["file_exists", "command"])]
    }
    assert withhold_legacy_sole_file_exists(data, ticket) is data


@pytest.mark.unit
def test_shim_does_not_apply_to_another_contract_with_the_same_item_id() -> None:
    _, item_id = sorted(LEGACY_SOLE_FILE_EXISTS_ITEMS)[0].split(":")
    data: dict[str, object] = {"dod_evidence": [_item(item_id, ["file_exists"])]}
    assert withhold_legacy_sole_file_exists(data, "OMN-99999999") is data


@pytest.mark.unit
def test_a_new_sole_file_exists_contract_still_fails_validate_yaml(
    tmp_path: Path,
) -> None:
    """RED direction: a contract outside the baseline is refused by core."""
    legacy = next(iter(sorted(CONTRACTS.glob("OMN-*.yaml"))))
    template = yaml.safe_load(legacy.read_text(encoding="utf-8"))
    template["ticket_id"] = "OMN-99999999"
    template["dod_evidence"] = [
        {
            "id": "dod-001",
            "description": "presence only",
            "checks": [{"check_type": "file_exists", "check_value": "README.md"}],
        }
    ]
    target = tmp_path / "contracts" / "OMN-99999999.yaml"
    target.parent.mkdir()
    target.write_text(yaml.safe_dump(template), encoding="utf-8")
    assert validate_file(target) is False


@pytest.mark.unit
def test_every_legacy_contract_validates() -> None:
    """GREEN direction: every baseline contract passes under installed core."""
    tickets = sorted({key.split(":")[0] for key in LEGACY_SOLE_FILE_EXISTS_ITEMS})
    failed = [t for t in tickets if not validate_file(CONTRACTS / f"{t}.yaml")]
    assert failed == []
