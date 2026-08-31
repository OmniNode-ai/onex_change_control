# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tests for the doc_reference_resolver scanner (OMN-13521).

These tests pin two behaviours that the pre-fix resolver lacked:

1. Class/function references resolve via a single in-memory index per
   repo-root instead of one ``grep -r`` subprocess per reference per root.
   No subprocess is spawned during resolution.
2. A wall-clock time budget bounds the run: once the budget is exhausted the
   resolver stops doing work and returns a partial result with the remaining
   references marked ``exists=None`` (fail-loud, bounded), rather than running
   for an unbounded number of minutes.
"""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

import pytest

from onex_change_control.enums.enum_doc_reference_type import EnumDocReferenceType
from onex_change_control.models.model_doc_reference import ModelDocReference
from onex_change_control.scanners import doc_reference_resolver
from onex_change_control.scanners.doc_reference_resolver import resolve_references

if TYPE_CHECKING:
    from pathlib import Path


def _make_repo(tmp_path: Path, name: str, contents: dict[str, str]) -> Path:
    """Create a fake repo root with a src/ tree from {relpath: text}."""
    root = tmp_path / name
    for rel, text in contents.items():
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8")
    return root


@pytest.mark.unit
class TestResolveClassOrFunctionBatched:
    """Class/function resolution must be subprocess-free and index-backed."""

    def test_class_reference_resolves_from_index(self, tmp_path: Path) -> None:
        root = _make_repo(
            tmp_path,
            "repo_a",
            {"src/pkg/model_thing.py": "class ModelThing:\n    pass\n"},
        )
        ref = ModelDocReference(
            doc_path="doc.md",
            line_number=1,
            reference_type=EnumDocReferenceType.CLASS_NAME,
            raw_text="ModelThing",
        )
        out = resolve_references([ref], [str(root)])
        assert len(out) == 1
        assert out[0].exists is True
        assert out[0].resolved_target is not None
        assert out[0].resolved_target.endswith("model_thing.py")

    def test_function_reference_resolves_from_index(self, tmp_path: Path) -> None:
        root = _make_repo(
            tmp_path,
            "repo_b",
            {"src/pkg/util.py": "def classify_node():\n    return None\n"},
        )
        ref = ModelDocReference(
            doc_path="doc.md",
            line_number=1,
            reference_type=EnumDocReferenceType.FUNCTION_NAME,
            raw_text="classify_node",
        )
        out = resolve_references([ref], [str(root)])
        assert out[0].exists is True
        assert out[0].resolved_target is not None
        assert out[0].resolved_target.endswith("util.py")

    def test_missing_symbol_marked_not_existing(self, tmp_path: Path) -> None:
        root = _make_repo(
            tmp_path,
            "repo_c",
            {"src/pkg/real.py": "class Real:\n    pass\n"},
        )
        ref = ModelDocReference(
            doc_path="doc.md",
            line_number=1,
            reference_type=EnumDocReferenceType.CLASS_NAME,
            raw_text="DoesNotExist",
        )
        out = resolve_references([ref], [str(root)])
        assert out[0].exists is False
        assert out[0].resolved_target is None

    def test_no_subprocess_spawned_during_resolution(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regression guard for OMN-13521: resolution must not shell out.

        The pre-fix resolver ran ``grep -r`` once per ref per repo-root. The
        batched implementation builds an in-memory index and resolves in pure
        Python — so ``subprocess.run`` must never be called.
        """
        root = _make_repo(
            tmp_path,
            "repo_d",
            {"src/pkg/model_thing.py": "class ModelThing:\n    pass\n"},
        )

        msg = "resolver spawned a subprocess (grep) -- not batched"

        def _boom(*_args: object, **_kwargs: object) -> None:
            raise AssertionError(msg)

        # Patch the stdlib entrypoint directly: any code path that shells out
        # (e.g. the old grep-per-ref resolver) would trip this guard.
        monkeypatch.setattr(subprocess, "run", _boom)

        refs = [
            ModelDocReference(
                doc_path="doc.md",
                line_number=i,
                reference_type=EnumDocReferenceType.CLASS_NAME,
                raw_text="ModelThing",
            )
            for i in range(1, 21)
        ]
        out = resolve_references(refs, [str(root)])
        assert all(r.exists is True for r in out)

    def test_index_built_once_per_root_not_per_ref(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With N refs and 1 root, the symbol index is built exactly once."""
        root = _make_repo(
            tmp_path,
            "repo_e",
            {"src/pkg/model_thing.py": "class ModelThing:\n    pass\n"},
        )

        calls: list[str] = []
        original = doc_reference_resolver._build_symbol_index

        def _counting(root_arg: str) -> dict[str, str]:
            calls.append(root_arg)
            return original(root_arg)

        monkeypatch.setattr(doc_reference_resolver, "_build_symbol_index", _counting)

        refs = [
            ModelDocReference(
                doc_path="doc.md",
                line_number=i,
                reference_type=EnumDocReferenceType.CLASS_NAME,
                raw_text="ModelThing",
            )
            for i in range(1, 51)
        ]
        resolve_references(refs, [str(root)])
        assert calls.count(str(root)) == 1


@pytest.mark.unit
class TestNonClassReferenceTypesPreserved:
    """Batching must not regress the other reference-type branches."""

    def test_file_path_still_resolved(self, tmp_path: Path) -> None:
        root = _make_repo(tmp_path, "repo_f", {"src/pkg/file.py": "x = 1\n"})
        ref = ModelDocReference(
            doc_path="doc.md",
            line_number=1,
            reference_type=EnumDocReferenceType.FILE_PATH,
            raw_text="src/pkg/file.py",
        )
        out = resolve_references([ref], [str(root)])
        assert out[0].exists is True

    def test_url_marked_unknown(self, tmp_path: Path) -> None:
        ref = ModelDocReference(
            doc_path="doc.md",
            line_number=1,
            reference_type=EnumDocReferenceType.URL,
            raw_text="http://localhost:8080",
        )
        out = resolve_references([ref], [str(tmp_path)])
        assert out[0].exists is None

    def test_env_var_resolved(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("MY_VAR=value\n", encoding="utf-8")
        ref = ModelDocReference(
            doc_path="doc.md",
            line_number=1,
            reference_type=EnumDocReferenceType.ENV_VAR,
            raw_text="MY_VAR",
        )
        out = resolve_references([ref], [str(tmp_path)], env_file=str(env_file))
        assert out[0].exists is True


@pytest.mark.unit
class TestTimeBudgetGuard:
    """The resolver must be bounded by a wall-clock budget."""

    def test_budget_exceeded_returns_partial_with_unknown(self, tmp_path: Path) -> None:
        """A zero budget short-circuits: every ref comes back exists=None.

        This is the fail-loud bounded behaviour — the resolver returns a
        complete list (same length as input) rather than running unbounded.
        """
        root = _make_repo(
            tmp_path,
            "repo_g",
            {"src/pkg/model_thing.py": "class ModelThing:\n    pass\n"},
        )
        refs = [
            ModelDocReference(
                doc_path="doc.md",
                line_number=i,
                reference_type=EnumDocReferenceType.CLASS_NAME,
                raw_text="ModelThing",
            )
            for i in range(1, 6)
        ]
        out = resolve_references(refs, [str(root)], time_budget_seconds=0.0)
        assert len(out) == len(refs)
        assert all(r.exists is None for r in out)
        assert all(r.resolved_target is None for r in out)

    def test_default_budget_resolves_normally(self, tmp_path: Path) -> None:
        root = _make_repo(
            tmp_path,
            "repo_h",
            {"src/pkg/model_thing.py": "class ModelThing:\n    pass\n"},
        )
        ref = ModelDocReference(
            doc_path="doc.md",
            line_number=1,
            reference_type=EnumDocReferenceType.CLASS_NAME,
            raw_text="ModelThing",
        )
        out = resolve_references([ref], [str(root)])
        assert out[0].exists is True
