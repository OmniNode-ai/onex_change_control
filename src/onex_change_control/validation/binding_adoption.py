# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""OMN-18239 — measuring, and ratcheting, acceptance-criterion binding adoption.

The plan's acceptance criterion for this item is a TREND: contracts declaring an
accepted binding rise week over week from the live baseline. A trend is not a
thing a gate can assert on one run, and a gate that pretended otherwise would be
a worse artifact than none.

What a gate CAN do, and what this one does, is make the trend mechanical in the
only direction available to it: **adoption may not go backwards**. The counts
below are frozen in a baseline file, and a run that measures fewer than the
baseline fails. Raising the baseline is a deliberate edit in the pull request
that raised the count, so the number in the repository is always a measurement
somebody made rather than a number somebody remembered.

**Three counts, because they answer three different questions.**

* ``declaring`` — contracts DECLARING a non-empty ``binds_ac`` on an evidence
  item. This is the OMN-18056 adoption number. Measured, it is 63; a substring
  count over the same corpus returns 67 and the plan quotes 61, because four
  contracts carry the sentence "It declares no binds_ac because ..." in prose.
  That gap is the whole reason this counts by parsing.
* ``with_records`` — contracts carrying an ``ac_bindings`` record, accepted or
  draft. This is OMN-18236 adoption: how many claims are pinned to a criterion
  revision rather than floating.
* ``with_acceptance`` — contracts carrying a record somebody accepted. This is
  the number the plan's criterion is actually about, and it is the only one of
  the three that means a person agreed.

Counting all three separately is what stops the headline number from being
satisfied by the cheap half. A proposer can raise ``with_records`` on its own;
only a reviewer can raise ``with_acceptance``.

**The honest limit, stated rather than implied.** This ratchet prevents a
regression. It cannot cause a rise, it cannot tell a good binding from a bad
one, and a lane that mass-accepted the corpus to move the number would satisfy
it — which is exactly the failure the acceptance requirement exists to make
visible rather than the one it prevents. The count is evidence for a human
reading the trend, not a substitute for reading it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import (
    Path,  # noqa: TC003  Why: called at runtime (glob/read_text), not only annotated
)

import yaml

__all__ = [
    "ModelBindingAdoption",
    "count_binding_adoption",
    "load_baseline",
    "render_regression",
]

_CONTRACT_GLOB = "OMN-*.yaml"


@dataclass(frozen=True)
class ModelBindingAdoption:
    """How far acceptance-criterion binding has spread through the corpus."""

    contracts: int
    declaring: int
    with_records: int
    with_acceptance: int

    def as_baseline(self) -> dict[str, int]:
        """The subset a baseline freezes. ``contracts`` is context, not a floor.

        The corpus total grows every day for reasons that have nothing to do
        with this ticket, so freezing it would make the ratchet fire on every
        unrelated contract somebody adds.
        """
        return {
            "declaring": self.declaring,
            "with_records": self.with_records,
            "with_acceptance": self.with_acceptance,
        }


def _items(document: object) -> list[dict[str, object]]:
    if not isinstance(document, dict):
        return []
    raw = document.get("dod_evidence")
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def _declares(item: dict[str, object]) -> bool:
    raw = item.get("binds_ac")
    return isinstance(raw, (list, tuple)) and bool(raw)


def _records(item: dict[str, object]) -> list[dict[str, object]]:
    raw = item.get("ac_bindings")
    if not isinstance(raw, (list, tuple)):
        return []
    return [entry for entry in raw if isinstance(entry, dict)]


def count_binding_adoption(contracts_dir: Path) -> ModelBindingAdoption:
    """Count adoption across every ticket contract in ``contracts_dir``.

    Parsed, never grepped. A contract whose PROSE mentions ``binds_ac`` — this
    ticket's own contract will — is not a contract that declares one, and a
    substring count would report the documentation as adoption.
    """
    contracts = 0
    declaring = 0
    with_records = 0
    with_acceptance = 0
    for path in sorted(contracts_dir.glob(_CONTRACT_GLOB)):
        contracts += 1
        try:
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            # An unreadable contract is counted as adopting nothing. It is a
            # different gate's finding, and swallowing it here would be the
            # only alternative to this file deciding something it cannot see.
            continue
        items = _items(document)
        if any(_declares(item) for item in items):
            declaring += 1
        records = [record for item in items for record in _records(item)]
        if records:
            with_records += 1
        if any(str(record.get("accepted_by") or "") for record in records):
            with_acceptance += 1
    return ModelBindingAdoption(
        contracts=contracts,
        declaring=declaring,
        with_records=with_records,
        with_acceptance=with_acceptance,
    )


def load_baseline(path: Path) -> dict[str, int]:
    """The frozen floors. A missing or malformed baseline is an ERROR, not zero.

    Defaulting to zero would turn "somebody deleted the baseline" into "every
    count is fine", which is the shape of a gate that reports green because it
    did not run.
    """
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        msg = f"{path} is not a mapping"
        raise TypeError(msg)
    floors: dict[str, int] = {}
    for key in ("declaring", "with_records", "with_acceptance"):
        value = document.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            msg = f"{path} carries no non-negative integer for {key!r}"
            raise ValueError(msg)
        floors[key] = value
    return floors


def render_regression(measured: ModelBindingAdoption, floors: dict[str, int]) -> str:
    """The failure message, or ``""`` when adoption held or grew."""
    current = measured.as_baseline()
    fallen = {
        key: (floors[key], current[key]) for key in floors if current[key] < floors[key]
    }
    if not fallen:
        return ""
    lines = [
        "Acceptance-criterion binding adoption went BACKWARDS. This ratchet is "
        "grow-only: a contract that declared a binding, recorded one, or "
        "carried an acceptance does not stop.",
        "",
    ]
    lines.extend(
        f"  {key}: baseline {floor}, measured {now} ({floor - now} lost)"
        for key, (floor, now) in sorted(fallen.items())
    )
    lines.extend(
        [
            "",
            "If a contract was legitimately deleted, lower the baseline in the "
            "same pull request that deletes it and say why. If a binding was "
            "removed because it was wrong, that is also a deliberate edit, and "
            "the point of this gate is that it is not a silent one.",
        ]
    )
    return "\n".join(lines)
