# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""No contract may carry a sole-``file_exists`` dod_evidence item (OMN-20136).

omnibase_core 0.47.26 rejects the shape at parse time. This corpus check holds
the line on the release this repo still locks, so the lock bump cannot be the
first place a regression is seen.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

CONTRACTS = sorted((Path(__file__).parents[3] / "contracts").glob("OMN-*.yaml"))


@pytest.mark.unit
def test_corpus_is_non_empty() -> None:
    assert len(CONTRACTS) > 1000


@pytest.mark.unit
def test_no_sole_file_exists_dod_evidence_item() -> None:
    offenders = []
    for path in CONTRACTS:
        data = yaml.safe_load(path.read_text())
        for item in (data or {}).get("dod_evidence") or []:
            types = {c.get("check_type") for c in item.get("checks") or []}
            if types and types <= {"file_exists"}:
                offenders.append(f"{path.stem}:{item['id']}")
    assert offenders == []
