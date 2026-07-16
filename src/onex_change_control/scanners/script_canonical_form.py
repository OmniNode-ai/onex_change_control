# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Script canonical-form scanner (DEFAULT-DENY new scripts, OMN-14475).

The imperative-contract scanner only walks ``src/``; ``scripts/**`` is a total
blind spot, so logic-bearing scripts accumulate there uncaught. This scanner
governs ``scripts/**`` under a deterministic deny-new inventory policy:

- A script already in the frozen baseline allowlist is pre-existing debt (PASS,
  burn-down only).
- A NEW script (not in the baseline) passes ONLY if it has an entry in the
  CODEOWNERS-approved exceptions registry (``scripts_exceptions.yaml``, resolved
  from onex_change_control@main so a downstream PR cannot self-add an entry — the
  gate is CODEOWNERS review on a separate @main PR; ``approved_by`` is advisory,
  not code-enforced). Otherwise it is BLOCKED (NEW_UNREGISTERED).

The AST scorer never decides pass/fail on its own. It makes exactly one binary
hard check — a registry entry with disposition ``node-backed`` must have a real
dispatch call into the ONEX substrate, else the claim is false and BLOCKED — and
otherwise only surfaces a LOUD advisory when a ``permanent`` entry's logic score
is high, so the CODEOWNERS reviewer, not the score, decides. (Evidence the score
must not decide: ``deploy_source_ref.py`` — heavy git-checkout orchestration —
scores 0 under this heuristic because subprocess orchestration is not a scored
logic form; only the inventory gate reliably catches it.)
"""

from __future__ import annotations

import ast
import re
from typing import TYPE_CHECKING

from onex_change_control.enums.enum_script_canonical_verdict import (
    EnumScriptCanonicalVerdict,
)
from onex_change_control.enums.enum_script_exception_disposition import (
    EnumScriptExceptionDisposition,
)
from onex_change_control.enums.enum_script_file_kind import EnumScriptFileKind
from onex_change_control.models.model_script_canonical_result import (
    ModelScriptCanonicalResult,
)

if TYPE_CHECKING:
    from pathlib import Path

    from onex_change_control.models.model_script_exception import ModelScriptException

# --- Governed file extensions ------------------------------------------------

# v1 governs Python + shell scripts. Data/config files under scripts/ (.yaml,
# .json, .txt, .md, ...) are not scripts and are not governed.
PYTHON_SUFFIXES: frozenset[str] = frozenset({".py"})
SHELL_SUFFIXES: frozenset[str] = frozenset({".sh", ".bash"})
GOVERNED_SUFFIXES: frozenset[str] = PYTHON_SUFFIXES | SHELL_SUFFIXES

# Default logic-score ceiling for the permanent-shim LOUD advisory (not a block).
# Calibrated against the live scripts corpus: thin CI/deploy glue (e.g.
# publish_with_retry.py=14) stays under it; a script re-implementing a
# validator/algorithm (corpus heavy scripts 20-56) trips the advisory.
DEFAULT_SHIM_CEILING = 18

# --- Dispatch detection (Python AST) ----------------------------------------

# Bare-call names that dispatch into the node/handler/runtime substrate.
_DISPATCH_CALL_NAMES: frozenset[str] = frozenset(
    {"run_node", "run_skill", "dispatch_node"}
)
# Attribute-call suffixes that dispatch (e.g. ``runtime.run_node(...)``,
# ``handler.handle(...)``). Deliberately excludes bare ``.run`` (too noisy —
# ``subprocess.run`` is not a dispatch).
_DISPATCH_ATTR_SUFFIXES: tuple[str, ...] = (
    ".run_node",
    ".run_skill",
    ".dispatch_node",
    ".handle",
)
# String tokens that indicate a CLI dispatch (``onex run-node ...`` /
# ``onex skill ...``) invoked via subprocess.
_DISPATCH_STRING_TOKENS: tuple[str, ...] = (
    "run-node",
    "onex skill",
    "onex run node",
)


def _module_path_is_node_import(dotted: str) -> bool:
    """Return True if a dotted import path targets the node/handler substrate."""
    parts = dotted.split(".")
    if "nodes" in parts:
        return True
    return any(part.startswith(("node_", "handler_")) for part in parts)


def _call_dotted_name(node: ast.Call) -> str | None:
    """Return the dotted function name of a Call node, or None."""
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        parts: list[str] = [func.attr]
        current: ast.expr = func.value
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if isinstance(current, ast.Name):
            parts.append(current.id)
        parts.reverse()
        return ".".join(parts)
    return None


def _detect_dispatch(tree: ast.Module) -> bool:
    """Return True if the module dispatches into the node/handler/runtime substrate."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if _module_path_is_node_import(node.module):
                return True
        elif isinstance(node, ast.Import):
            if any(_module_path_is_node_import(alias.name) for alias in node.names):
                return True
        elif isinstance(node, ast.Call):
            dotted = _call_dotted_name(node)
            if dotted is not None and (
                dotted in _DISPATCH_CALL_NAMES
                or dotted.endswith(_DISPATCH_ATTR_SUFFIXES)
            ):
                return True
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            lowered = node.value.lower()
            if any(token in lowered for token in _DISPATCH_STRING_TOKENS):
                return True
    return False


# --- Logic scoring (Python AST, advisory only) -------------------------------

_MAGNITUDE_OPS = (ast.Lt, ast.LtE, ast.Gt, ast.GtE)
_ARITH_OPS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow)
_AGGREGATION_METHODS: frozenset[str] = frozenset(
    {"append", "add", "update", "extend", "insert"}
)
_CLASSIFY_REGEX_FUNCS: frozenset[str] = frozenset(
    {"re.compile", "re.search", "re.match", "re.fullmatch", "re.findall"}
)


def _binop_is_arithmetic(node: ast.BinOp) -> bool:
    """True for numeric arithmetic; False for string concatenation/formatting."""
    if not isinstance(node.op, _ARITH_OPS):
        return False
    for operand in (node.left, node.right):
        if isinstance(operand, ast.Constant) and isinstance(operand.value, str):
            return False
        if isinstance(operand, ast.JoinedStr):  # f-string
            return False
    return True


def _comprehension_transforms(node: ast.expr) -> bool:
    """True if a comprehension's element expression transforms (not passthrough)."""
    element: ast.expr | None = None
    if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp)):
        element = node.elt
    elif isinstance(node, ast.DictComp):
        element = node.value
    if element is None:
        return False
    return isinstance(
        element,
        (ast.Call, ast.BinOp, ast.IfExp, ast.ListComp, ast.SetComp, ast.DictComp),
    )


def _loop_aggregates(node: ast.For | ast.While) -> bool:
    """True if a loop body aggregates/mutates or nests a branch (real work)."""
    for child in ast.walk(node):
        if child is node:
            continue
        if isinstance(child, (ast.AugAssign, ast.If)):
            return True
        if isinstance(child, ast.Call):
            dotted = _call_dotted_name(child)
            if dotted is not None and dotted.split(".")[-1] in _AGGREGATION_METHODS:
                return True
    return False


def _is_elif_chain(node: ast.If) -> bool:
    """True if this If has an elif branch (orelse is a single If)."""
    return len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If)


def _score_logic(tree: ast.Module) -> int:  # noqa: C901, PLR0912  Why: one branch per scored AST node kind
    """Compute the module's advisory logic score.

    Walks the whole tree so logic hidden inside helper defs is still counted.
    Marshalling constructs (argparse, print, Path, json/yaml/tomllib load, Model
    construction, dispatch, simple guards, assignments) contribute 0. This score
    is ADVISORY only — it never decides pass/fail; see the module docstring.
    """
    score = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            if any(isinstance(op, _MAGNITUDE_OPS) for op in node.ops):
                score += 2
        elif isinstance(node, ast.BinOp):
            if _binop_is_arithmetic(node):
                score += 1
        elif isinstance(
            node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
        ):
            if _comprehension_transforms(node):
                score += 2
        elif isinstance(node, (ast.For, ast.While)):
            if _loop_aggregates(node):
                score += 2
        elif isinstance(node, ast.If):
            if _is_elif_chain(node):
                score += 2
        elif isinstance(node, ast.Call):
            dotted = _call_dotted_name(node)
            if dotted in _CLASSIFY_REGEX_FUNCS:
                score += 2
    return score


# --- Shell dispatch (text-based) ---------------------------------------------

# Optional in-file human breadcrumb (never a pass — authority is the registry).
_BREADCRUMB_RE = re.compile(r"#\s*scripts-exception:", re.IGNORECASE)


def _shell_has_dispatch(source: str) -> bool:
    """Text-based dispatch detection for shell scripts (no AST)."""
    lowered = source.lower()
    return any(token in lowered for token in _DISPATCH_STRING_TOKENS)


def _file_kind(path: Path) -> EnumScriptFileKind:
    """Classify a governed script by suffix."""
    if path.suffix in PYTHON_SUFFIXES:
        return EnumScriptFileKind.PYTHON
    return EnumScriptFileKind.SHELL


def is_governed_script(path: Path) -> bool:
    """Return True if a scripts/** file is governed by this scanner.

    Governs .py/.sh/.bash, excluding package markers (``__init__.py``).
    """
    if path.suffix not in GOVERNED_SUFFIXES:
        return False
    return path.name != "__init__.py"


def _analyse_source(kind: EnumScriptFileKind, source: str) -> tuple[int, bool]:
    """Return (logic_score, has_dispatch) for a governed script's source."""
    if kind is EnumScriptFileKind.PYTHON:
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return 0, False
        return _score_logic(tree), _detect_dispatch(tree)
    return 0, _shell_has_dispatch(source)


def classify_script(  # noqa: PLR0913  Why: deny-new classification needs full baseline/registry/ceiling context
    script_path: Path,
    repo: str,
    *,
    in_baseline: bool,
    exception: ModelScriptException | None = None,
    rel_path: str | None = None,
    shim_ceiling: int = DEFAULT_SHIM_CEILING,
) -> ModelScriptCanonicalResult:
    """Classify a single governed ``scripts/**`` file under the deny-new policy.

    Args:
        script_path: Path to the script file.
        repo: Repository name.
        in_baseline: Whether the path is frozen in the baseline allowlist.
        exception: The CODEOWNERS-approved registry entry for this path, if any.
        rel_path: Repo-relative path for reporting (defaults to the file name).
        shim_ceiling: Logic-score threshold above which a ``permanent`` entry
            raises the loud advisory (never a block).

    Returns:
        The per-script canonical-form result.
    """
    display_path = rel_path if rel_path is not None else script_path.name
    kind = _file_kind(script_path)
    logic_score, has_dispatch = _analyse_source(kind, script_path.read_text("utf-8"))

    # Baseline = pre-existing debt: pass regardless of score, keep advisory score.
    if in_baseline:
        return ModelScriptCanonicalResult(
            script_path=display_path,
            repo=repo,
            file_kind=kind,
            verdict=EnumScriptCanonicalVerdict.ALLOWLISTED,
            logic_score=logic_score,
            has_dispatch=has_dispatch,
            is_new=False,
            detail="baselined pre-existing script (burn-down debt)",
        )

    verdict, disposition, advisory, detail = _classify_new_script(
        exception=exception,
        kind=kind,
        has_dispatch=has_dispatch,
        logic_score=logic_score,
        shim_ceiling=shim_ceiling,
    )
    return ModelScriptCanonicalResult(
        script_path=display_path,
        repo=repo,
        file_kind=kind,
        verdict=verdict,
        logic_score=logic_score,
        has_dispatch=has_dispatch,
        is_new=True,
        disposition=disposition,
        logic_advisory=advisory,
        detail=detail,
    )


def _classify_new_script(
    *,
    exception: ModelScriptException | None,
    kind: EnumScriptFileKind,
    has_dispatch: bool,
    logic_score: int,
    shim_ceiling: int,
) -> tuple[
    EnumScriptCanonicalVerdict, EnumScriptExceptionDisposition | None, bool, str
]:
    """Resolve (verdict, disposition, advisory, detail) for a new script."""
    if exception is None:
        return (
            EnumScriptCanonicalVerdict.NEW_UNREGISTERED,
            None,
            False,
            "new script not in the baseline and not in the exceptions registry — "
            "build it as a CONTRACT+NODE+HANDLER, or add a reviewed entry to "
            "onex_change_control/allowlists/scripts_exceptions.yaml citing a ticket.",
        )

    disposition = exception.disposition
    if disposition is EnumScriptExceptionDisposition.NODE_BACKED:
        if has_dispatch:
            return (
                EnumScriptCanonicalVerdict.EXCEPTION_GRANTED,
                disposition,
                False,
                f"node-backed exception ({exception.ticket}) corroborated by a "
                "dispatch call.",
            )
        return (
            EnumScriptCanonicalVerdict.FALSE_NODE_BACKED,
            disposition,
            False,
            f"node-backed exception ({exception.ticket}) but no dispatch into the "
            "node/handler/runtime substrate was found — unsubstantiated claim.",
        )

    if disposition is EnumScriptExceptionDisposition.PERMANENT:
        advisory = kind is EnumScriptFileKind.PYTHON and logic_score >= shim_ceiling
        detail = f"permanent exception ({exception.ticket})."
        if advisory:
            detail += (
                f" LOUD ADVISORY: logic score {logic_score} >= ceiling "
                f"{shim_ceiling} — reviewer should confirm this is glue, not "
                "logic that belongs in a node."
            )
        return (
            EnumScriptCanonicalVerdict.EXCEPTION_GRANTED,
            disposition,
            advisory,
            detail,
        )

    # CONVERT: logic expected; conversion tracked by the ticket, no advisory.
    return (
        EnumScriptCanonicalVerdict.EXCEPTION_GRANTED,
        disposition,
        False,
        f"convert exception ({exception.ticket}) — conversion tracked; "
        f"logic score {logic_score}.",
    )
