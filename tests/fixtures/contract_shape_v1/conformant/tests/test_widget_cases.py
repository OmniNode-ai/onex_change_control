# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Conformant GREEN fixture — the reference shape of a v1 case module.

This file is DATA for the OMN-15669 gate tests, not a test of this repo. It is
excluded from the outer suite by ``collect_ignore_glob`` in tests/conftest.py;
the gate collects it deliberately, in-process, through the real runner.

It demonstrates the whole convention in miniature:
  * one parameterized fixture axis (``binding``) — never two test bodies;
  * the mock and the real dependency validated against the SAME seam schema
    (``schemas/widget_seam.schema.yaml``) via ``assert_seam_shape``;
  * a case per declared error class and per input-constraint boundary.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from onex_change_control.testing.seam_binding import assert_seam_shape, binding_params

SEAM_SCHEMA = "schemas/widget_seam.schema.yaml"
FIXTURE_ROOT = Path(__file__).resolve().parents[1]


class MockWidgetStore:
    """Mock binding. Its payload is validated against the real seam schema."""

    def fetch(self, widget_id: str) -> dict[str, object]:
        return {"widget_id": widget_id, "weight_g": 42}


class RealWidgetStore:
    """Real binding. Same seam, same schema, different implementation."""

    def fetch(self, widget_id: str) -> dict[str, object]:
        return {"widget_id": widget_id, "weight_g": len(widget_id) * 7}


def _store(binding: str) -> MockWidgetStore | RealWidgetStore:
    return MockWidgetStore() if binding == "mock" else RealWidgetStore()


@pytest.mark.parametrize("binding", binding_params("both"))
def test_widget_store_seam(binding: str) -> None:
    """Case ``widget_store_seam`` — identical body, both bindings."""
    payload = _store(binding).fetch("w-1")
    assert_seam_shape(payload, SEAM_SCHEMA, binding=binding, root=FIXTURE_ROOT)
    assert payload["widget_id"] == "w-1"


@pytest.mark.parametrize("binding", binding_params("mock"))
def test_widget_id_empty(binding: str) -> None:
    """Case ``widget_id_empty`` — the WIDGET_ID_EMPTY error class."""
    assert binding == "mock"
    with pytest.raises(Exception):  # noqa: B017, PT011 - fixture data, not a real unit
        assert_seam_shape(
            {"widget_id": "", "weight_g": 1},
            SEAM_SCHEMA,
            binding=binding,
            root=FIXTURE_ROOT,
        )


@pytest.mark.parametrize("binding", binding_params("mock"))
def test_weight_negative(binding: str) -> None:
    """Case ``weight_negative`` — the WEIGHT_NEGATIVE error class."""
    assert binding == "mock"
    with pytest.raises(Exception):  # noqa: B017, PT011 - fixture data
        assert_seam_shape(
            {"widget_id": "w", "weight_g": -1},
            SEAM_SCHEMA,
            binding=binding,
            root=FIXTURE_ROOT,
        )
