# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Seam schemas for the contract-shape-v1 injectable dependencies (OMN-15669).

These are the ``seam_schema`` refs the canonical contract declares for its two
injectable dependencies. The SAME model validates the mock fixture's payload
and the real dependency's payload — that symmetry is what makes mock-shape
divergence (the OMN-15598 class) unrepresentable rather than merely discouraged.

Pure models: no env reads, no filesystem, no network, no clock (D-008).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["ModelBaseBlobSeam", "ModelCollectorSeam"]


class ModelCollectorSeam(BaseModel):
    """What a test collector returns for one test path.

    Real binding: ``PytestCollector`` shelling out to ``pytest --collect-only``.
    Mock binding: a canned mapping. Both must produce THIS shape.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    test_path: str = Field(description="Repo-relative path the collector was asked for")
    node_ids: list[str] = Field(
        default_factory=list,
        description="Fully qualified pytest node ids, e.g. tests/x.py::test_y[mock]",
    )


class ModelBaseBlobSeam(BaseModel):
    """What a base-ref reader returns for one path on the PR's base branch.

    Real binding: ``GitBaseReader`` running ``git show <base>:<path>``.
    Mock binding: a dict-backed reader. ``text`` is None exactly when the path
    does not exist on the base ref — i.e. the PR ADDS it.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(description="Repo-relative path read from the base ref")
    exists_on_base: bool = Field(description="False when the PR adds this path")
    text: str | None = Field(
        default=None, description="Base-ref file contents, None when absent"
    )
