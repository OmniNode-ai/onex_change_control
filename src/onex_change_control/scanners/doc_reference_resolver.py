# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Reference resolver for documentation references.

Takes extracted references and checks whether their targets still exist
in the filesystem, codebase, or environment configuration.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from onex_change_control.enums.enum_doc_reference_type import EnumDocReferenceType
from onex_change_control.models.model_doc_reference import ModelDocReference


def _resolve_file_path(
    ref: ModelDocReference, repo_roots: list[str]
) -> ModelDocReference:
    """Resolve a file path reference by checking if the file exists."""
    raw = ref.raw_text

    # Try absolute path first
    if Path(raw).is_absolute() and Path(raw).exists():
        return ModelDocReference(
            doc_path=ref.doc_path,
            line_number=ref.line_number,
            reference_type=ref.reference_type,
            raw_text=ref.raw_text,
            resolved_target=raw,
            exists=True,
        )

    # Try relative to each repo root
    for root in repo_roots:
        candidate = Path(root) / raw
        if candidate.exists():
            return ModelDocReference(
                doc_path=ref.doc_path,
                line_number=ref.line_number,
                reference_type=ref.reference_type,
                raw_text=ref.raw_text,
                resolved_target=str(candidate),
                exists=True,
            )

    return ModelDocReference(
        doc_path=ref.doc_path,
        line_number=ref.line_number,
        reference_type=ref.reference_type,
        raw_text=ref.raw_text,
        resolved_target=None,
        exists=False,
    )


def _resolve_class_or_function(
    ref: ModelDocReference, repo_roots: list[str]
) -> ModelDocReference:
    """Resolve a class or function name by grepping the codebase."""
    name = ref.raw_text
    pattern = (
        f"class {name}"
        if ref.reference_type == EnumDocReferenceType.CLASS_NAME
        else f"def {name}"
    )

    for root in repo_roots:
        src_dir = Path(root) / "src"
        if not src_dir.exists():
            continue
        try:
            result = subprocess.run(
                ["grep", "-r", "-l", pattern, str(src_dir)],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if result.returncode == 0 and result.stdout.strip():
                first_file = result.stdout.strip().splitlines()[0]
                return ModelDocReference(
                    doc_path=ref.doc_path,
                    line_number=ref.line_number,
                    reference_type=ref.reference_type,
                    raw_text=ref.raw_text,
                    resolved_target=first_file,
                    exists=True,
                )
        except (subprocess.TimeoutExpired, OSError):
            continue

    return ModelDocReference(
        doc_path=ref.doc_path,
        line_number=ref.line_number,
        reference_type=ref.reference_type,
        raw_text=ref.raw_text,
        resolved_target=None,
        exists=False,
    )


def _resolve_env_var(
    ref: ModelDocReference, env_file: str | None = None
) -> ModelDocReference:
    """Resolve an env var by checking ~/.omnibase/.env."""
    var_name = ref.raw_text
    env_path = Path(env_file) if env_file else Path.home() / ".omnibase" / ".env"

    if env_path.exists():
        content = env_path.read_text(encoding="utf-8")
        for raw_line in content.splitlines():
            stripped_line = raw_line.strip()
            if stripped_line.startswith("#") or "=" not in stripped_line:
                continue
            key = stripped_line.split("=", 1)[0].strip()
            if key == var_name:
                return ModelDocReference(
                    doc_path=ref.doc_path,
                    line_number=ref.line_number,
                    reference_type=ref.reference_type,
                    raw_text=ref.raw_text,
                    resolved_target=str(env_path),
                    exists=True,
                )

    return ModelDocReference(
        doc_path=ref.doc_path,
        line_number=ref.line_number,
        reference_type=ref.reference_type,
        raw_text=ref.raw_text,
        resolved_target=None,
        exists=False,
    )


def _resolve_url(ref: ModelDocReference) -> ModelDocReference:
    """URLs are not checked for liveness -- mark as exists=None."""
    return ModelDocReference(
        doc_path=ref.doc_path,
        line_number=ref.line_number,
        reference_type=ref.reference_type,
        raw_text=ref.raw_text,
        resolved_target=ref.raw_text,
        exists=None,
    )


def _resolve_command(
    ref: ModelDocReference, repo_roots: list[str]
) -> ModelDocReference:
    """Resolve a command reference by checking if referenced paths exist."""
    raw = ref.raw_text
    # Extract file/dir paths from the command
    parts = raw.split()
    found_path = False

    for part in parts:
        if "/" in part and not part.startswith("http"):
            for root in repo_roots:
                candidate = Path(root) / part
                if candidate.exists():
                    found_path = True
                    break
            if found_path:
                break

    # If no paths to check, mark as exists=None (can't verify)
    if not any("/" in p and not p.startswith("http") for p in parts):
        return ModelDocReference(
            doc_path=ref.doc_path,
            line_number=ref.line_number,
            reference_type=ref.reference_type,
            raw_text=ref.raw_text,
            resolved_target=None,
            exists=None,
        )

    return ModelDocReference(
        doc_path=ref.doc_path,
        line_number=ref.line_number,
        reference_type=ref.reference_type,
        raw_text=ref.raw_text,
        resolved_target=None,
        exists=found_path if found_path else False,
    )


def resolve_references(
    references: list[ModelDocReference],
    repo_roots: list[str],
    env_file: str | None = None,
) -> list[ModelDocReference]:
    """Resolve all references, populating exists and resolved_target fields.

    Args:
        references: List of extracted references to resolve.
        repo_roots: List of repository root directories to search.
        env_file: Optional path to env file (defaults to ~/.omnibase/.env).

    Returns:
        List of resolved references with exists field populated.
    """
    resolved: list[ModelDocReference] = []

    for ref in references:
        if ref.reference_type == EnumDocReferenceType.FILE_PATH:
            resolved.append(_resolve_file_path(ref, repo_roots))
        elif ref.reference_type in (
            EnumDocReferenceType.CLASS_NAME,
            EnumDocReferenceType.FUNCTION_NAME,
        ):
            resolved.append(_resolve_class_or_function(ref, repo_roots))
        elif ref.reference_type == EnumDocReferenceType.ENV_VAR:
            resolved.append(_resolve_env_var(ref, env_file))
        elif ref.reference_type == EnumDocReferenceType.URL:
            resolved.append(_resolve_url(ref))
        elif ref.reference_type == EnumDocReferenceType.COMMAND:
            resolved.append(_resolve_command(ref, repo_roots))
        else:
            resolved.append(ref)

    return resolved
