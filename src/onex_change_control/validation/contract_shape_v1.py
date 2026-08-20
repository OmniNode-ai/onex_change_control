# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Canonical OCC contract shape v1 — the assembled contract-testing gate.

OMN-15669, operator ruling R-0802-9 (2026-08-02).

ONE schema artifact (``schemas/occ_contract_v1.schema.yaml``) is the shape;
this module is its ENGINE, not a second shape. Every rule below is driven by
data declared in the contract instance under that schema — nothing here encodes
a private idea of what a contract looks like.

The six properties, and where each one is enforced
--------------------------------------------------
P1  ONE CANONICAL SHAPE, ZERO HUMAN REVIEW
    :func:`check_schema` validates the instance against the single schema
    artifact. No rule in this file consults a reviewer, an author, an approver,
    or any human judgement: shape validation *replaces* review. The companion
    falsifiability floor (:func:`check_evidence_falsifiability`) carries no
    grandfathering — no exemption by age or author: v1 turns on the OMN-14417
    self-reference kill switch that the legacy corpus gate defers, and
    :func:`v1_vacuity_reason` rejects an ENUMERATED list of always-true check
    forms that survive a verb-level family match. That list is a denylist under
    active attack, not a decision procedure; its open residuals are named in
    that function's docstring and must be quoted with it.

P2  TICKET IDENTITY, fail-closed + identity-blind
    :func:`check_identity` compares three artifacts — the ``## OCC Contract
    (canonical, serialized)`` fenced block in the Linear ticket body, the PR
    trailer ``Contract-Ticket-Hash: <id>=<sha>``, and the landed
    ``contracts/OMN-XXXX.yaml`` — by sha256 over \\n-normalized bytes. Any
    divergence is RED *with a unified diff*, regardless of who authored what;
    the function never receives an actor and cannot branch on one. Linear
    unreachable is RED, not skip. Contracts the PR does not touch are never
    read (:func:`select_scope`) — grandfather by per-touch migration, no
    backfill sweep.

P3  TESTS BEFORE CODE, DECLARED AS BINDING-AGNOSTIC CASES
    The schema makes ``cases`` required with ``minItems: 1`` — a contract
    without cases is MALFORMED, so the declaration cannot follow the code.
    :func:`check_cases` then asserts each declared case exists in the PR tree
    AND is collected by the REAL runner (``pytest --collect-only``), so a case
    that was declared and never written is RED.

P4  ENUMERABLE CASE SPACE
    The schema requires typed ``interface.inputs`` / ``interface.outputs`` and a
    non-empty ``interface.error_taxonomy``. :func:`check_case_space` enumerates
    every error class, every input-constraint boundary and every dependency
    seam into target tokens, then requires each token to be covered by a case
    or listed as a labeled exclusion. An unmapped token is shape-RED. Because
    the space is derived from the shape, a generator can iterate it.

P5  SHAPE-CORRECT INJECTED MOCKS
    The schema requires ``dependencies[].seam_schema`` + ``injectable: true``.
    :func:`check_dependencies` requires the ref to RESOLVE, requires a case
    covering each dependency, and requires that case's test file to cite the
    same ``seam_schema`` string and to execute the shared validator
    (:func:`onex_change_control.testing.seam_binding.assert_seam_shape`), which
    validates mock and real payloads against the SAME schema. Enforcement is
    SEMANTIC, via :func:`parse_source_facts`: the citation must be a real string
    literal in executable code, and the validator call must be reached from the
    case's own test function on an UNCONDITIONAL path whose failure can still
    fail the test. A commented-out call, one quoted in a docstring, one parked
    in ``if False:``, one behind a runtime condition or loop, one after a
    ``return``/``pytest.skip(...)``, and one inside a ``try`` that swallows the
    failure are all NOT executed. What this proves is that the declared seam
    validation RUNS for the case; it does not prove the mock is faithful in any
    respect the seam schema does not constrain, so "the OMN-15598 class is
    unrepresentable" is exactly as strong as the seam schema is.

P6  DUAL-BINDING EXECUTION
    ``bindings`` is per-case in the schema. :func:`check_bindings` reads the
    collected node ids and requires the declared bindings to match the
    parameterized fixture axis actually wired: ``both`` must show BOTH a
    ``[mock]`` and a ``[real]`` collected variant of the same case body. The
    convention is one fixture axis, never duplicated test bodies — see
    ``docs/standards/DUAL_BINDING_CASES.md``. A ``both`` case with only one leg
    wired is RED.

Exit codes: 0 clean, 1 findings, 2 usage error.
"""

from __future__ import annotations

import argparse
import ast
import difflib
import functools
import hashlib
import importlib
import importlib.util
import json
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

import yaml
from jsonschema import Draft202012Validator

__all__ = [
    "CONTRACT_BLOCK_HEADING",
    "SCHEMA_PATH",
    "SEAM_ASSERT_NAME",
    "V1_DIR",
    "V1_MARKER",
    "Finding",
    "IdentityInputs",
    "LinearUnreachableError",
    "PytestCollector",
    "SourceFacts",
    "canonicalize",
    "check_bindings",
    "check_case_space",
    "check_cases",
    "check_dependencies",
    "check_evidence_falsifiability",
    "check_identity",
    "check_schema",
    "evaluate_contract",
    "extract_contract_block",
    "load_schema",
    "load_ticket_bodies",
    "main",
    "make_ticket_body_reader",
    "parse_contract_trailers",
    "parse_source_facts",
    "select_scope",
    "sha256_block",
    "v1_vacuity_reason",
]

# --------------------------------------------------------------------------
# The ONE schema artifact. There is deliberately no second path here.
# --------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMA_PATH = REPO_ROOT / "schemas" / "occ_contract_v1.schema.yaml"

V1_MARKER = "occ-contract/v1"

# Where a v1 contract lives. `contracts/*.yaml` (top level) is owned by
# omnibase_core's ModelTicketContract, which is `extra="forbid"` and validates
# `schema_version` as SemVer -- the v1 blocks are literally unrepresentable
# there today, and the required `Validate Contract YAML (OMN-8808)` job proves
# it on every PR. One shape per path is the point of the one-model-per-shape
# rule, so v1 gets its own directory rather than two models fighting over one
# file. RESIDUAL, named not hidden: forcing migration on every legacy contract
# touch has to wait for ModelTicketContract to absorb v1 (or for RSD to retire
# it per ruling R-0802-8).
V1_DIR = "contracts/v1/"

CONTRACT_BLOCK_HEADING = "## OCC Contract (canonical, serialized)"

_TRAILER_RE = re.compile(
    r"^Contract-Ticket-Hash:\s*(?P<ticket>OMN-[0-9]+)\s*=\s*(?P<sha>[0-9a-f]{64})\s*$",
    re.MULTILINE,
)

# One fenced ```yaml block under the canonical heading. Non-greedy, so a second
# fence later in the body cannot be swallowed into the first.
_BLOCK_RE = re.compile(
    re.escape(CONTRACT_BLOCK_HEADING)
    + r"\s*\n+```(?:yaml|yml)?\s*\n(?P<body>.*?)\n?```",
    re.DOTALL,
)

# The shared seam-validation helper a P5 seam case must execute.
#
# REMEDIATION r1 (2026-08-02): the first build enforced this by the substring
# test ``SEAM_ASSERT_SYMBOL not in source``. An adversarial replay showed a seam
# case whose ONLY ``assert_seam_shape(`` occurrences were a ``#`` comment and an
# ``if False:`` branch passed both this gate and pytest — validation that never
# executes, which is precisely the class the leg exists to forbid. Enforcement
# is now SEMANTIC (:func:`parse_source_facts`): a real, reachable call node
# reached from the case's own test function. ``SEAM_ASSERT_SYMBOL`` survives
# only as the rendered spelling inside finding messages.
SEAM_ASSERT_NAME = "assert_seam_shape"
SEAM_ASSERT_SYMBOL = SEAM_ASSERT_NAME + "("


class LinearUnreachableError(RuntimeError):
    """Raised when the ticket body cannot be read. Always RED, never skip."""


@dataclass(frozen=True)
class Finding:
    """One fail-closed verdict. ``rule`` is the machine-readable reason code."""

    rule: str
    subject: str
    message: str
    diff: str = ""

    def render(self) -> str:
        head = f"  [{self.rule}] {self.subject}: {self.message}"
        if not self.diff:
            return head
        return head + "\n" + "\n".join(f"      {ln}" for ln in self.diff.splitlines())


# --------------------------------------------------------------------------
# Canonicalization — applied identically to all three identity artifacts.
# --------------------------------------------------------------------------
def canonicalize(text: str) -> str:
    r"""Return ``text`` \n-normalized: CRLF/CR -> LF, exactly one trailing LF.

    Both the ticket-body block and the landed file go through this before
    hashing, so a platform line ending or an editor's trailing newline can
    never masquerade as a contract divergence.
    """
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return normalized.rstrip("\n") + "\n"


def sha256_block(text: str) -> str:
    """sha256 of the canonicalized text, hex."""
    return hashlib.sha256(canonicalize(text).encode("utf-8")).hexdigest()


@functools.lru_cache(maxsize=4)
def load_schema(path: Path | None = None) -> dict[str, Any]:
    """Load the ONE schema artifact. A missing schema is a hard error.

    Cached: the artifact is immutable within a run, and caching keeps
    ``evaluate_contract`` from needing a schema parameter at all.
    """
    target = path or SCHEMA_PATH
    if not target.exists():
        msg = f"canonical schema artifact missing: {target}"
        raise FileNotFoundError(msg)
    loaded = yaml.safe_load(target.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        msg = f"canonical schema artifact is not a mapping: {target}"
        raise TypeError(msg)
    return loaded


# --------------------------------------------------------------------------
# P2 — ticket identity (identity-blind by construction: no actor parameter).
# --------------------------------------------------------------------------
def extract_contract_block(ticket_body: str) -> str | None:
    """Return the serialized contract under the canonical heading, or None."""
    match = _BLOCK_RE.search(ticket_body or "")
    if match is None:
        return None
    return match.group("body")


def parse_contract_trailers(pr_body: str) -> dict[str, str]:
    """Return ``{ticket_id: sha}`` for every ``Contract-Ticket-Hash`` trailer."""
    return {
        m.group("ticket"): m.group("sha") for m in _TRAILER_RE.finditer(pr_body or "")
    }


def load_ticket_bodies(path: Path | None) -> dict[str, str]:
    """Load the ``{ticket_id: body}`` map the workflow layer fetched from Linear.

    THE GATE MAKES NO NETWORK CALL. This repo already reads Linear from the
    workflow layer (``stale-todo-gate.yml``, ``todo-audit-on-merge.yml``,
    ``staleness-monitor.yml`` all curl the API with ``LINEAR_API_KEY``), and the
    imperative-contract guard blocks raw HTTP from ``src/`` because it bypasses
    contract transport. So the CI step fetches and writes the map; this function
    only reads it.

    Fail-closed either way: a missing file, unreadable JSON, or an absent ticket
    key all surface as :class:`LinearUnreachableError` -> ``identity_linear_
    unreachable`` RED. There is no offline pass and no cached pass.
    """
    if path is None or not path.exists():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}
    if not isinstance(loaded, dict):
        return {}
    return {str(k): str(v) for k, v in loaded.items() if isinstance(v, str)}


def make_ticket_body_reader(bodies: dict[str, str]) -> Any:
    """Return a reader that raises :class:`LinearUnreachableError` on a miss."""

    def _read(ticket_id: str) -> str:
        body = bodies.get(ticket_id)
        if not body:
            msg = (
                f"no ticket body available for {ticket_id} — Linear was "
                "unreachable or the fetch step produced nothing. Unreachable is "
                "RED (fail-closed), never a skip."
            )
            raise LinearUnreachableError(msg)
        return body

    return _read


def _unified(left: str, right: str, left_name: str, right_name: str) -> str:
    return "".join(
        difflib.unified_diff(
            canonicalize(left).splitlines(keepends=True),
            canonicalize(right).splitlines(keepends=True),
            fromfile=left_name,
            tofile=right_name,
            n=2,
        )
    )


def check_identity(
    ticket_id: str,
    contract_text: str,
    pr_body: str,
    ticket_body: str | None,
) -> list[Finding]:
    """Three-way identity check. Divergence is RED with a diff, always.

    ``ticket_body`` is None ONLY to express *unreachable*, which is RED. No
    parameter of this function identifies an actor, so the verdict cannot vary
    by who authored the ticket, the PR, or the contract.
    """
    findings: list[Finding] = []
    landed_sha = sha256_block(contract_text)

    trailers = parse_contract_trailers(pr_body)
    trailer_sha = trailers.get(ticket_id)
    if trailer_sha is None:
        findings.append(
            Finding(
                rule="identity_trailer_missing",
                subject=ticket_id,
                message=(
                    "PR body carries no `Contract-Ticket-Hash: "
                    f"{ticket_id}=<sha256>` trailer. Expected "
                    f"{ticket_id}={landed_sha}"
                ),
            )
        )
    elif trailer_sha != landed_sha:
        findings.append(
            Finding(
                rule="identity_trailer_divergence",
                subject=ticket_id,
                message=(
                    f"PR trailer sha {trailer_sha} != landed "
                    f"contracts/{ticket_id}.yaml sha {landed_sha}"
                ),
            )
        )

    if ticket_body is None:
        findings.append(
            Finding(
                rule="identity_linear_unreachable",
                subject=ticket_id,
                message=(
                    "ticket body unreachable — identity cannot be proven, so "
                    "this is RED (fail-closed), not a skip"
                ),
            )
        )
        return findings

    block = extract_contract_block(ticket_body)
    if block is None:
        findings.append(
            Finding(
                rule="identity_block_missing",
                subject=ticket_id,
                message=(
                    f"ticket body has no `{CONTRACT_BLOCK_HEADING}` section with "
                    "exactly one fenced yaml block"
                ),
            )
        )
        return findings

    block_sha = sha256_block(block)
    if block_sha != landed_sha:
        findings.append(
            Finding(
                rule="identity_block_divergence",
                subject=ticket_id,
                message=(
                    f"ticket-embedded contract sha {block_sha} != landed "
                    f"contracts/{ticket_id}.yaml sha {landed_sha}"
                ),
                diff=_unified(
                    block,
                    contract_text,
                    f"ticket:{ticket_id}#occ-contract",
                    f"contracts/{ticket_id}.yaml",
                ),
            )
        )
    if trailer_sha is not None and trailer_sha != block_sha:
        findings.append(
            Finding(
                rule="identity_trailer_block_divergence",
                subject=ticket_id,
                message=(
                    f"PR trailer sha {trailer_sha} != ticket-embedded contract "
                    f"sha {block_sha} (the ticket was edited after the PR was "
                    "opened, or the trailer was never refreshed)"
                ),
            )
        )
    return findings


# --------------------------------------------------------------------------
# P1 — schema validation against the ONE artifact.
# --------------------------------------------------------------------------
def check_schema(
    contract: dict[str, Any], subject: str, schema: dict[str, Any] | None = None
) -> list[Finding]:
    """Validate the instance against the single canonical schema artifact."""
    validator = Draft202012Validator(schema or load_schema())
    findings: list[Finding] = []
    for error in sorted(validator.iter_errors(contract), key=lambda e: list(e.path)):
        location = "/".join(str(p) for p in error.path) or "(root)"
        findings.append(
            Finding(
                rule="shape_invalid",
                subject=f"{subject}:{location}",
                message=error.message,
            )
        )
    return findings


# --------------------------------------------------------------------------
# P3/P6 — real collection through the real runner.
# --------------------------------------------------------------------------
class Collector(Protocol):
    """Returns the node ids the real test runner collects for a path."""

    def collect(self, test_path: str) -> list[str]: ...


@dataclass
class PytestCollector:
    """The REAL runner. ``pytest --collect-only`` over the PR tree."""

    root: Path
    timeout: int = 300
    _cache: dict[str, list[str]] = field(default_factory=dict)

    def collect(self, test_path: str) -> list[str]:
        if test_path in self._cache:
            return self._cache[test_path]
        proc = subprocess.run(  # noqa: S603
            [
                sys.executable,
                "-m",
                "pytest",
                "--collect-only",
                "-q",
                "--no-header",
                "-p",
                "no:randomly",
                "-p",
                "no:cacheprovider",
                test_path,
            ],
            cwd=self.root,
            capture_output=True,
            text=True,
            timeout=self.timeout,
            check=False,
        )
        # pytest reports node ids relative to ROOTDIR, which is the nearest
        # ancestor holding a pytest config — not necessarily ``self.root``. The
        # file part is rewritten back to the requested path so a case declared
        # `tests/x.py` matches whether or not the evaluated tree is nested
        # inside another project. Anything that does not resolve to the
        # requested file is dropped rather than guessed at.
        node_ids: list[str] = []
        for raw in proc.stdout.splitlines():
            line = raw.strip()
            if "::" not in line:
                continue
            file_part, _, rest = line.partition("::")
            if file_part == test_path or file_part.endswith("/" + test_path):
                node_ids.append(f"{test_path}::{rest}")
        self._cache[test_path] = node_ids
        return node_ids


def _case_node_ids(node_ids: list[str], case_id: str) -> list[str]:
    """Node ids whose test function name carries this case id."""
    return [n for n in node_ids if case_id in n.rsplit("::", 1)[-1]]


def _binding_of(node_id: str) -> str | None:
    match = re.search(r"\[([^\]]*)\]$", node_id)
    if match is None:
        return None
    parts = match.group(1).split("-")
    for token in parts:
        if token in {"mock", "real"}:
            return token
    return None


def check_cases(
    contract: dict[str, Any], subject: str, root: Path, collector: Collector
) -> list[Finding]:
    """P3. Every declared case exists in the tree and is really collected."""
    findings: list[Finding] = []
    for case in contract.get("cases") or []:
        if not isinstance(case, dict):
            continue
        case_id = str(case.get("id", "<unnamed>"))
        test_path = str(case.get("test_path", ""))
        if not test_path or not (root / test_path).exists():
            findings.append(
                Finding(
                    rule="case_test_absent",
                    subject=f"{subject}:{case_id}",
                    message=(
                        f"declared test_path {test_path!r} does not exist in the "
                        "PR tree — cases are written BEFORE the code, so a case "
                        "with no file is an undelivered declaration"
                    ),
                )
            )
            continue
        node_ids = collector.collect(test_path)
        matched = _case_node_ids(node_ids, case_id)
        if not matched:
            findings.append(
                Finding(
                    rule="case_not_collected",
                    subject=f"{subject}:{case_id}",
                    message=(
                        f"{test_path} exists but the real runner collects no test "
                        f"naming case {case_id!r} (collected: "
                        f"{[n.rsplit('::', 1)[-1] for n in node_ids][:8]})"
                    ),
                )
            )
    return findings


def check_bindings(
    contract: dict[str, Any], subject: str, root: Path, collector: Collector
) -> list[Finding]:
    """P6. Declared bindings must match the parameterized axis actually wired."""
    findings: list[Finding] = []
    for case in contract.get("cases") or []:
        if not isinstance(case, dict):
            continue
        case_id = str(case.get("id", "<unnamed>"))
        test_path = str(case.get("test_path", ""))
        declared = str(case.get("bindings", ""))
        if not test_path or not (root / test_path).exists():
            continue  # already reported by check_cases
        matched = _case_node_ids(collector.collect(test_path), case_id)
        if not matched:
            continue  # already reported by check_cases
        wired = {b for b in (_binding_of(n) for n in matched) if b is not None}
        expected = {"mock", "real"} if declared == "both" else {declared}
        if wired != expected:
            findings.append(
                Finding(
                    rule="binding_axis_mismatch",
                    subject=f"{subject}:{case_id}",
                    message=(
                        f"case declares bindings={declared!r} but the collected "
                        f"parameterized axis wires {sorted(wired) or '[]'}. The "
                        "same case body must run on ONE fixture axis for every "
                        "declared binding — a `both` case wired for one leg is "
                        "an unproven integration claim"
                    ),
                )
            )
    return findings


# --------------------------------------------------------------------------
# P4 — the enumerable case space.
# --------------------------------------------------------------------------
def enumerate_case_space(contract: dict[str, Any]) -> list[str]:
    """Every target token the shape makes enumerable, in stable order."""
    targets: list[str] = []
    interface = contract.get("interface") or {}
    for error in interface.get("error_taxonomy") or []:
        if isinstance(error, dict) and error.get("code"):
            targets.append(f"error:{error['code']}")
    for field_decl in interface.get("inputs") or []:
        if not isinstance(field_decl, dict):
            continue
        name = field_decl.get("name")
        for constraint in field_decl.get("constraints") or {}:
            targets.append(f"input:{name}.{constraint}")
    for dep in contract.get("dependencies") or []:
        if isinstance(dep, dict) and dep.get("name"):
            targets.append(f"dependency:{dep['name']}")
    return targets


def check_case_space(contract: dict[str, Any], subject: str) -> list[Finding]:
    """P4. Every enumerated target maps to a case or a labeled exclusion."""
    findings: list[Finding] = []
    covered: set[str] = set()
    for case in contract.get("cases") or []:
        if isinstance(case, dict):
            covered.update(str(t) for t in (case.get("covers") or []))
    excluded = {
        str(x.get("target"))
        for x in (contract.get("exclusions") or [])
        if isinstance(x, dict)
    }
    space = enumerate_case_space(contract)
    for target in space:
        if target in covered or target in excluded:
            continue
        findings.append(
            Finding(
                rule="case_space_unmapped",
                subject=f"{subject}:{target}",
                message=(
                    f"{target} is declared in the shape but is mapped to no case "
                    "and to no labeled exclusion. Every error class and every "
                    "input-constraint boundary must be walked or explicitly "
                    "excluded with a reason"
                ),
            )
        )
    for target in sorted(excluded - set(space)):
        findings.append(
            Finding(
                rule="exclusion_target_unknown",
                subject=f"{subject}:{target}",
                message=(
                    "exclusion names a target that is not in the enumerated case "
                    "space — a stale exclusion silently shrinks the space"
                ),
            )
        )
    for target in sorted(excluded & covered):
        findings.append(
            Finding(
                rule="exclusion_target_covered",
                subject=f"{subject}:{target}",
                message=(
                    "target is BOTH excluded and covered by a case — delete the "
                    "exclusion; a covered target must not carry an excuse"
                ),
            )
        )
    for target in sorted(covered - set(space)):
        findings.append(
            Finding(
                rule="case_covers_unknown_target",
                subject=f"{subject}:{target}",
                message=(
                    "a case claims to cover a target that the shape does not "
                    "declare — the claim is unfalsifiable"
                ),
            )
        )
    return findings


# --------------------------------------------------------------------------
# Reachable-source facts. Textual containment is NOT enforcement.
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class SourceFacts:
    """What a Python module REALLY does, as opposed to what its bytes contain.

    Built by :func:`parse_source_facts` from the AST with two prunings applied:

    * **comments and docstrings are gone.** A ``# assert_seam_shape(...)`` line
      or a schema ref quoted in a docstring never reaches the AST as executable
      code, so it cannot satisfy a citation or an execution requirement.
    * **compile-time-dead branches are pruned.** ``if False:`` / ``while 0:``
      bodies (and the ``else`` arm of ``if True:``) are not visited, so parking
      a call in an unreachable branch does not count as calling it.

    ``literals`` holds every string constant in reachable, non-docstring code —
    the only form in which a contract's ``seam_schema`` ref can actually be
    handed to the validator. ``calls_by_function`` maps each function name to
    the callee names reachable inside it, which is what lets the gate ask "does
    THIS case's test body reach the seam validator", directly or through a
    module-local helper, rather than "does the file mention it anywhere".

    REMEDIATION r1r (2026-08-02): compile-time pruning alone was not enough.
    An adversarial replay drove five RUNTIME-dead forms past it — a call under
    ``if os.environ.get("NEVER_SET"):``, a call after ``pytest.skip(...)``, a
    call after an early ``return``, and a call inside a ``try`` whose handler
    swallows the exception (``except Exception: pass`` / bare ``except``). Two
    of those are green in pytest as well as in the gate, with the seam
    validation never executing. ``unconditional_calls`` is therefore the graph
    the seam leg reads: it holds only the callees invoked on EVERY entry to a
    function, with the verdict still able to fail the test. ``calls_by_function``
    is retained as the looser "appears in reachable code" view — it is not what
    P5 asserts.
    """

    literals: frozenset[str]
    calls_by_function: dict[str, frozenset[str]]
    module_calls: frozenset[str]
    unconditional_calls: dict[str, frozenset[str]] = field(default_factory=dict)
    unconditional_module_calls: frozenset[str] = frozenset()

    def reaches(
        self, roots: list[str], symbol: str, *, unconditional: bool = True
    ) -> bool:
        """True when ``symbol`` is called from any of ``roots``, transitively.

        The walk follows module-local callees only: a helper defined in the same
        module is followed, an imported name is a leaf. That is enough for the
        shapes this convention produces (a test body that validates inline, or
        one that validates through a module-local binding-resolver helper) and
        it terminates on every input because ``seen`` is monotone.

        ``unconditional`` (the default, and what P5 asserts) walks the
        every-entry graph: each edge means "entering the caller ALWAYS invokes
        the callee, and a failure there still fails the caller". Pass
        ``unconditional=False`` for the looser reachable-code view.
        """
        graph = self.unconditional_calls if unconditional else self.calls_by_function
        seen: set[str] = set()
        stack = list(roots)
        while stack:
            name = stack.pop()
            if name in seen:
                continue
            seen.add(name)
            callees = graph.get(name)
            if callees is None:
                continue
            if symbol in callees:
                return True
            stack.extend(callees)
        return False


def _constant_truth(node: ast.expr) -> bool | None:
    """``True``/``False`` for a compile-time constant test, else ``None``."""
    if isinstance(node, ast.Constant):
        return bool(node.value)
    return None


def _callee_name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _strip_docstring(body: list[ast.stmt]) -> list[ast.stmt]:
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        return body[1:]
    return body


# --------------------------------------------------------------------------
# Unconditional-execution analysis (REMEDIATION r1r).
#
# "Present in reachable code" is weaker than "runs". These helpers answer the
# stronger question the seam leg needs: entering this function, is the callee
# ALWAYS invoked, with a failure there still able to fail the caller?
# --------------------------------------------------------------------------

# Calls that end the current statement sequence: nothing after them executes.
# `_callee_name` returns the attribute, so this covers pytest.skip / pytest.xfail
# / pytest.fail / sys.exit / os._exit written in any import style.
_TERMINATING_CALLS = frozenset({"skip", "xfail", "fail", "exit", "_exit"})

# pytest marks that stop the decorated body from running at all.
_SKIPPING_MARKS = frozenset({"skip", "xfail"})

# Expression forms whose sub-calls are NOT guaranteed to evaluate.
_CONDITIONAL_EXPRS: tuple[type[ast.AST], ...] = (
    ast.IfExp,
    ast.ListComp,
    ast.SetComp,
    ast.DictComp,
    ast.GeneratorExp,
)
_DEFERRED_SCOPES: tuple[type[ast.AST], ...] = (
    ast.Lambda,
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.ClassDef,
)
# ``try``/``try*`` — the handler decides whether a failure inside survives.
_TRY_STMTS: tuple[type[ast.AST], ...] = tuple(
    node for node in (ast.Try, getattr(ast, "TryStar", None)) if node is not None
)
# Statement forms whose bodies run zero-or-more times, never exactly-once.
_CONDITIONAL_STMTS: tuple[type[ast.AST], ...] = tuple(
    node
    for node in (
        ast.If,
        ast.While,
        ast.For,
        ast.AsyncFor,
        getattr(ast, "Match", None),
    )
    if node is not None
)


def _calls_in_expression(node: ast.AST) -> set[str]:
    """Callee names guaranteed to be invoked when ``node`` is evaluated.

    Deliberately conservative: a call parked in a lambda, a nested def, a
    comprehension body, a conditional expression, or the short-circuit arm of a
    ``and``/``or`` is not counted, because none of them is guaranteed to run.
    """
    names: set[str] = set()
    stack: list[ast.AST] = [node]
    while stack:
        current = stack.pop()
        if isinstance(current, ast.Call):
            callee = _callee_name(current.func)
            if callee is not None:
                names.add(callee)
        if isinstance(current, ast.BoolOp):
            # Only the FIRST operand is always evaluated.
            children: list[ast.AST] = [current.values[0]] if current.values else []
        else:
            children = list(ast.iter_child_nodes(current))
        for child in children:
            if isinstance(child, _DEFERRED_SCOPES + _CONDITIONAL_EXPRS):
                continue
            stack.append(child)
    return names


def _terminates(stmt: ast.stmt) -> bool:
    """True when no statement after ``stmt`` can run."""
    if isinstance(stmt, (ast.Return, ast.Raise, ast.Break, ast.Continue)):
        return True
    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
        return _callee_name(stmt.value.func) in _TERMINATING_CALLS
    return False


def _handler_reraises(handler: ast.ExceptHandler) -> bool:
    """True when this handler re-raises rather than swallowing the failure."""
    return any(isinstance(node, ast.Raise) for node in ast.walk(handler))


def _with_calls(stmt: ast.With | ast.AsyncWith) -> tuple[set[str], bool]:
    """A ``with`` body always runs, so its unconditional calls carry through."""
    names: set[str] = set()
    for item in stmt.items:
        names |= _calls_in_expression(item.context_expr)
    inner, stopped = _unconditional_calls(stmt.body)
    return names | inner, stopped


def _try_calls(stmt: ast.stmt) -> tuple[set[str], bool]:
    """``try`` body calls count only when no handler swallows the failure."""
    handlers = list(getattr(stmt, "handlers", []))
    inner, _ = _unconditional_calls(list(getattr(stmt, "body", [])))
    if handlers and not all(_handler_reraises(h) for h in handlers):
        # The call may run, but its VERDICT is discarded — a swallowed
        # assertion cannot fail the test, so it proves nothing.
        inner = set()
    final, stopped = _unconditional_calls(list(getattr(stmt, "finalbody", [])))
    return inner | final, stopped


def _unconditional_calls(body: list[ast.stmt]) -> tuple[set[str], bool]:
    """``(callees always invoked by ``body``, whether ``body`` terminates)``."""
    names: set[str] = set()
    for stmt in _strip_docstring(body):
        if isinstance(stmt, _DEFERRED_SCOPES):
            continue  # defined here, executed only if something calls it
        if isinstance(stmt, (ast.With, ast.AsyncWith)):
            inner, stopped = _with_calls(stmt)
        elif isinstance(stmt, _TRY_STMTS):
            inner, stopped = _try_calls(stmt)
        elif isinstance(stmt, _CONDITIONAL_STMTS):
            continue  # runtime-conditional: not every entry runs it
        else:
            inner, stopped = _calls_in_expression(stmt), _terminates(stmt)
        names |= inner
        if stopped:
            return names, True
    return names, False


def _is_pytest_mark(node: ast.expr, mark: str) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == mark
        and isinstance(node.value, ast.Attribute)
        and node.value.attr == "mark"
    )


def _decorator_skips_body(decorators: list[ast.expr]) -> bool:
    """True when a decorator stops the body from running on every entry."""
    for decorator in decorators:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if any(_is_pytest_mark(target, mark) for mark in _SKIPPING_MARKS):
            return True
        if (
            _is_pytest_mark(target, "skipif")
            and isinstance(decorator, ast.Call)
            and decorator.args
            and _constant_truth(decorator.args[0]) is True
        ):
            return True
    return False


class _ReachableCollector(ast.NodeVisitor):
    """Collects :class:`SourceFacts` over reachable, non-docstring code only."""

    def __init__(self) -> None:
        self.literals: set[str] = set()
        self.calls_by_function: dict[str, set[str]] = {}
        self.module_calls: set[str] = set()
        self.unconditional_calls: dict[str, set[str]] = {}
        self.unconditional_module_calls: set[str] = set()
        self._stack: list[str] = []

    # -- reachability pruning ------------------------------------------------
    def visit_If(self, node: ast.If) -> None:
        truth = _constant_truth(node.test)
        if truth is None:
            self.generic_visit(node)
            return
        for stmt in node.body if truth else node.orelse:
            self.visit(stmt)

    def visit_While(self, node: ast.While) -> None:
        if _constant_truth(node.test) is False:
            for stmt in node.orelse:
                self.visit(stmt)
            return
        self.generic_visit(node)

    # -- docstring stripping + function scoping ------------------------------
    def visit_Module(self, node: ast.Module) -> None:
        unconditional, _ = _unconditional_calls(list(node.body))
        self.unconditional_module_calls |= unconditional
        for stmt in _strip_docstring(list(node.body)):
            self.visit(stmt)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        for stmt in _strip_docstring(list(node.body)):
            self.visit(stmt)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        # Decorators evaluate at DEFINITION time, outside the body's scope.
        for decorator in node.decorator_list:
            self.visit(decorator)
        self.calls_by_function.setdefault(node.name, set())
        entry = self.unconditional_calls.setdefault(node.name, set())
        if not _decorator_skips_body(node.decorator_list):
            unconditional, _ = _unconditional_calls(list(node.body))
            entry |= unconditional
        self._stack.append(node.name)
        for stmt in _strip_docstring(list(node.body)):
            self.visit(stmt)
        self._stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    # -- leaves --------------------------------------------------------------
    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str):
            self.literals.add(node.value)

    def visit_Call(self, node: ast.Call) -> None:
        name = _callee_name(node.func)
        if name is not None:
            if self._stack:
                # Attribute to every enclosing function so a call buried in a
                # nested def still counts for the outer test body.
                for enclosing in self._stack:
                    self.calls_by_function.setdefault(enclosing, set()).add(name)
            else:
                self.module_calls.add(name)
        self.generic_visit(node)


def parse_source_facts(source: str) -> SourceFacts | None:
    """Parse ``source`` into :class:`SourceFacts`; ``None`` when unparseable."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    collector = _ReachableCollector()
    collector.visit(tree)
    return SourceFacts(
        literals=frozenset(collector.literals),
        calls_by_function={
            name: frozenset(callees)
            for name, callees in collector.calls_by_function.items()
        },
        module_calls=frozenset(collector.module_calls),
        unconditional_calls={
            name: frozenset(callees)
            for name, callees in collector.unconditional_calls.items()
        },
        unconditional_module_calls=frozenset(collector.unconditional_module_calls),
    )


# --------------------------------------------------------------------------
# P5 — shape-correct injected mocks.
# --------------------------------------------------------------------------
def _seam_schema_resolves(ref: str, root: Path) -> bool:
    if (root / ref).exists():
        return True
    if "." not in ref:
        return False
    module_name, _, attribute = ref.rpartition(".")
    try:
        module = importlib.import_module(module_name)
    except (ImportError, ValueError):
        return False
    return hasattr(module, attribute)


def _check_seam_source(
    case: dict[str, Any], subject: str, root: Path, seam_schema: str
) -> list[Finding]:
    """P5's source half: the seam ref is cited AND the validator really runs.

    Split out of :func:`check_dependencies` so the semantic (AST) enforcement
    reads as one unit. Every rule here is about what the module DOES, never
    about what its bytes contain.
    """
    findings: list[Finding] = []
    case_id = str(case.get("id", "<unnamed>"))
    test_path = str(case.get("test_path", ""))
    test_file = root / test_path
    if not test_file.exists():
        return findings  # reported by check_cases

    facts = parse_source_facts(test_file.read_text(encoding="utf-8"))
    if facts is None:
        return [
            Finding(
                rule="case_source_unparseable",
                subject=f"{subject}:{case_id}",
                message=(
                    f"{test_path} does not parse as Python, so the seam leg "
                    "cannot be proven semantically. A gate that falls back to "
                    "substring matching here is the hole this rule closes"
                ),
            )
        ]

    if seam_schema and seam_schema not in facts.literals:
        findings.append(
            Finding(
                rule="seam_schema_not_cited",
                subject=f"{subject}:{case_id}",
                message=(
                    f"{test_path} does not cite seam_schema {seam_schema!r} as a "
                    "string literal in executable code. The mock fixture must "
                    "validate against the SAME schema ref the real dependency "
                    "does; a ref that appears only in a comment or a docstring "
                    "is never handed to the validator"
                ),
            )
        )

    # SEMANTIC, not textual. The case's own test function(s) must reach the seam
    # validator — directly, or through a module-local helper. When the file
    # declares no function carrying this case id, the missing-case finding is
    # check_cases' to report, so fall back to module scope here rather than
    # double-reporting the same defect.
    entry_points = [fn for fn in facts.calls_by_function if case_id in fn]
    if entry_points:
        executed = facts.reaches(entry_points, SEAM_ASSERT_NAME)
        scope = f"the test function(s) for case {case_id!r}"
    else:
        executed = SEAM_ASSERT_NAME in facts.unconditional_module_calls or any(
            SEAM_ASSERT_NAME in callees
            for callees in facts.unconditional_calls.values()
        )
        scope = "any unconditionally executed code in the file"
    if not executed:
        findings.append(
            Finding(
                rule="seam_validation_not_executed",
                subject=f"{subject}:{case_id}",
                message=(
                    f"{test_path}: {scope} never reaches {SEAM_ASSERT_SYMBOL}...) "
                    "on every entry with a verdict that can still fail the test. "
                    "Enforcement is by AST, not by substring, and the bar is "
                    "UNCONDITIONAL execution: a commented-out call, a call quoted "
                    "in a docstring, a call in an `if False:` branch, a call "
                    "behind ANY runtime condition or loop, a call after a "
                    "`return`/`raise`/`pytest.skip(...)`, a call under a "
                    "skip/xfail mark, and a call inside a `try` whose handler "
                    "swallows the failure all read as NOT executed. Put the "
                    "validator on the test's straight-line path"
                ),
            )
        )
    return findings


def check_dependencies(
    contract: dict[str, Any], subject: str, root: Path
) -> list[Finding]:
    """P5. Mocks validate against the SAME seam schema as the real dependency."""
    findings: list[Finding] = []
    cases = [c for c in (contract.get("cases") or []) if isinstance(c, dict)]
    for dep in contract.get("dependencies") or []:
        if not isinstance(dep, dict):
            continue
        name = str(dep.get("name", "<unnamed>"))
        seam_schema = str(dep.get("seam_schema", ""))
        token = f"dependency:{name}"

        if not _seam_schema_resolves(seam_schema, root):
            findings.append(
                Finding(
                    rule="seam_schema_unresolvable",
                    subject=f"{subject}:{name}",
                    message=(
                        f"seam_schema {seam_schema!r} resolves to neither a file "
                        "in the tree nor an importable dotted path — a mock "
                        "cannot be validated against a schema that does not exist"
                    ),
                )
            )

        seam_cases = [c for c in cases if token in (c.get("covers") or [])]
        if not seam_cases:
            findings.append(
                Finding(
                    rule="seam_case_missing",
                    subject=f"{subject}:{name}",
                    message=(
                        f"no case covers {token}. Every injectable dependency "
                        "needs a case that drives its seam"
                    ),
                )
            )
            continue

        for case in seam_cases:
            case_id = str(case.get("id", "<unnamed>"))
            if str(case.get("bindings", "")) != "both":
                findings.append(
                    Finding(
                        rule="seam_case_not_dual_bound",
                        subject=f"{subject}:{case_id}",
                        message=(
                            f"case covers {token} but declares "
                            f"bindings={case.get('bindings')!r}. A case involving "
                            "a dependency defaults to `both` — mock-bound and "
                            "real-bound runs of the same body are what make "
                            "mock/real divergence detectable"
                        ),
                    )
                )
            findings += _check_seam_source(case, subject, root, seam_schema)
    return findings


# --------------------------------------------------------------------------
# Falsifiability floor — reuses the OMN-14409 deriver, no second taxonomy.
# --------------------------------------------------------------------------
def _load_proof_tier_deriver() -> Any:
    # Always resolved from THIS repo, never from the evaluated tree: the deriver
    # is the gate's own dependency, not the contract author's.
    module_path = (
        REPO_ROOT / "scripts" / "validation" / "check_contract_substance_floor.py"
    )
    if not module_path.exists():
        msg = f"substance-floor deriver missing: {module_path}"
        raise FileNotFoundError(msg)
    spec = importlib.util.spec_from_file_location(
        "_occ_substance_floor_for_shape_v1", module_path
    )
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        msg = f"cannot load substance-floor deriver from {module_path}"
        raise ImportError(msg)
    module = importlib.util.module_from_spec(spec)
    # Register BEFORE exec: @dataclass resolves its own module out of
    # sys.modules, and an unregistered module makes that lookup return None.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# Search verbs whose invocation the v1 vacuity rules inspect. `grep`/`rg` are
# in the substance floor's SUBSTANTIVE family (a static assertion over source is
# genuinely falsifiable) — which is exactly why their ARGUMENTS have to be read.
_SEARCH_VERBS = frozenset({"grep", "egrep", "fgrep", "rg", "ag", "ack", "ast-grep"})

# Wrappers that delegate to the real command; the verb sits after them.
_RUNNER_PREFIXES = frozenset({"uv", "poetry", "sudo", "env", "time", "xargs", "exec"})
_RUNNER_SUBCOMMANDS = frozenset({"run", "--"})

# Verbs that feed a file's CONTENT into the next pipeline stage. `cat x | grep`
# searches x; the search segment itself carries no path, so a rule that reads
# only the search segment's own argv sees no target at all.
_FILE_SOURCE_VERBS = frozenset({"cat", "bat", "zcat", "head", "tail", "strings"})

# Task runners with a real dry-run mode. `-n` is listed only for THESE verbs:
# on grep/rg it means "print line numbers", which is not a dry run.
_DRY_RUN_VERBS = frozenset({"make", "just", "task", "rsync", "ansible-playbook"})
_DRY_RUN_FLAGS = frozenset(
    {"--dry-run", "--dryrun", "--just-print", "--recon", "--no-act", "-n"}
)

# Comparison verbs. `diff a a` is a tautology, not a comparison.
_COMPARISON_VERBS = frozenset({"diff", "cmp"})

# Interpreters that take an inline program via `-c`.
_INLINE_PROGRAM_VERBS = frozenset({"python", "python3", "bash", "sh", "zsh"})

# Flags that consume the NEXT token, so it is neither the pattern nor a path.
_VALUE_TAKING_FLAGS = frozenset(
    {
        "-m",
        "--max-count",
        "-A",
        "--after-context",
        "-B",
        "--before-context",
        "-C",
        "--context",
        "-f",
        "--file",
        "--include",
        "--exclude",
        "--exclude-dir",
        "-d",
        "--devices",
        "-t",
        "--type",
        "-g",
        "--glob",
    }
)
_PATTERN_FLAGS = frozenset({"-e", "--regexp", "--pattern"})

# Literal patterns that match ANY non-empty content. Kept as a fast path and as
# documentation; the LOAD-BEARING rule is :func:`_pattern_is_vacuous`, which
# executes the pattern rather than looking it up. REMEDIATION r1r: as a
# standalone denylist this was defeated by `x*` — `.` with a different spelling.
_VACUOUS_SEARCH_PATTERNS = frozenset(
    {"", ".", ".*", ".+", "^", "$", "^.*$", "^$", "^.*", ".*$", "[\\s\\S]*", "(?s).*"}
)

# Content-free strings a pattern is executed against. A pattern that matches
# EVERY one of these matches arbitrary bytes, so finding it proves nothing about
# the change. They are deliberately unrelated to any real source line.
_VACUITY_PROBES = ("a", "0", " ", "zq7f3k9v", "—§—", "x")

# Prose targets. Asserting a sentence exists in documentation cannot fail when
# the CODE is wrong — only when someone deletes the sentence.
_PROSE_SUFFIXES = (
    ".md",
    ".markdown",
    ".mdx",
    ".rst",
    ".txt",
    ".text",
    ".adoc",
    ".org",
)
# Extensionless prose files, and directories whose whole purpose is prose.
_PROSE_BASENAMES = frozenset(
    {
        "README",
        "CHANGELOG",
        "CHANGES",
        "HISTORY",
        "NEWS",
        "NOTES",
        "LICENSE",
        "LICENCE",
        "COPYING",
        "NOTICE",
        "AUTHORS",
        "CONTRIBUTORS",
        "CONTRIBUTING",
    }
)
_PROSE_DIRS = frozenset({"docs", "doc", "documentation"})

_ENV_ASSIGNMENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")


def _strip_runner_prefix(tokens: list[str]) -> list[str]:
    """Drop leading wrappers and env assignments so the real verb is at [0]."""
    index = 0
    while index < len(tokens) and (
        tokens[index] in _RUNNER_PREFIXES
        or tokens[index] in _RUNNER_SUBCOMMANDS
        or _ENV_ASSIGNMENT_RE.match(tokens[index])
    ):
        index += 1
    return tokens[index:]


_PIPE_SEPARATOR = "|"
_PIPELINE_BREAKS = frozenset({"||", "&&", ";", "&", "\n"})


def _tokenize(command: str) -> list[str]:
    """Tokenize like a shell: quotes protect their contents, operators split.

    A regex-level split cannot do this. ``grep -qE '(a|)' src/foo.py`` contains a
    ``|`` INSIDE the quoted pattern, and splitting the raw string on ``|`` tore
    the pattern in half — which is precisely how the r1 build let ``(a|)``
    through while catching ``x*``. An untokenizable command (unbalanced quotes)
    yields no tokens, so no argument rule fires: a NAMED residual.
    """
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        return list(lexer)
    except ValueError:
        return []


def _shell_pipelines(command: str) -> list[list[list[str]]]:
    """Split a command into pipelines, each an ORDERED list of argv segments.

    Pipeline structure is retained (unlike the flat split the first build used)
    because `cat README.md | grep -q x` puts the search TARGET in a different
    segment from the search itself.
    """
    pipelines: list[list[list[str]]] = []
    segments: list[list[str]] = []
    current: list[str] = []

    def flush_segment() -> None:
        if current:
            segments.append(list(current))
            current.clear()

    def flush_pipeline() -> None:
        flush_segment()
        if segments:
            pipelines.append(list(segments))
            segments.clear()

    for token in _tokenize(command):
        if token in _PIPELINE_BREAKS:
            flush_pipeline()
        elif token == _PIPE_SEPARATOR:
            flush_segment()
        else:
            current.append(token)
    flush_pipeline()
    return pipelines


def _shell_segments(command: str) -> list[list[str]]:
    """Every argv segment in ``command``, pipeline structure flattened away."""
    return [segment for pipeline in _shell_pipelines(command) for segment in pipeline]


def _parse_search(tokens: list[str]) -> tuple[str | None, list[str]]:
    """Return ``(pattern, paths)`` for a search invocation's argv."""
    pattern: str | None = None
    paths: list[str] = []
    index = 1
    while index < len(tokens):
        arg = tokens[index]
        if arg == "--":
            index += 1
            continue
        if arg.startswith("-") and arg != "-":
            if arg in _PATTERN_FLAGS and index + 1 < len(tokens):
                pattern = tokens[index + 1] if pattern is None else pattern
                index += 2
                continue
            if "=" in arg:
                head, _, tail = arg.partition("=")
                if head in _PATTERN_FLAGS and pattern is None:
                    pattern = tail
                index += 1
                continue
            if arg in _VALUE_TAKING_FLAGS and index + 1 < len(tokens):
                index += 2
                continue
            index += 1
            continue
        if pattern is None:
            pattern = arg
        else:
            paths.append(arg)
        index += 1
    return pattern, paths


def _is_prose_path(candidate: str) -> bool:
    """True for documentation targets, by suffix, by basename, or by directory.

    All three forms are needed: ``docs/notes.mdx`` (unlisted suffix),
    ``CHANGELOG`` (no suffix at all) and ``docs/whatever`` are prose exactly as
    much as ``README.md`` is, and each defeated a suffix-only rule.
    """
    path = PurePosixPath(candidate.strip().lstrip("./"))
    if candidate.lower().endswith(_PROSE_SUFFIXES):
        return True
    if path.name.upper() in _PROSE_BASENAMES:
        return True
    return any(part.lower() in _PROSE_DIRS for part in path.parts)


def _same_path(left: str, right: str) -> bool:
    normalized = [
        PurePosixPath(value.strip().lstrip("./")).as_posix() for value in (left, right)
    ]
    return normalized[0] == normalized[1]


def _pattern_is_vacuous(pattern: str) -> bool:
    """True when ``pattern`` matches arbitrary content.

    EXECUTED, not looked up: the pattern is compiled and run against strings
    that have nothing to do with any source file. `x*`, `(a|)`, `\\s*` and `.`
    all match every one of them, so a search for any of them succeeds against a
    correct tree and a catastrophically broken one alike.

    A pattern Python cannot compile falls back to the literal denylist. That is
    a NAMED residual, not a closed class — a POSIX BRE that Python rejects and
    that is also vacuous would slip through.
    """
    if pattern.strip() in _VACUOUS_SEARCH_PATTERNS:
        return True
    try:
        compiled = re.compile(pattern)
    except re.error:
        return False
    return all(compiled.search(probe) is not None for probe in _VACUITY_PROBES)


def _python_snippet_is_noop(code: str) -> bool:
    """True when an inline `python -c` program cannot fail on wrong code."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return False
    body = _strip_docstring(list(tree.body))
    if not body:
        return True
    for stmt in body:
        if isinstance(stmt, ast.Pass):
            continue
        if isinstance(stmt, ast.Expr):
            value = stmt.value
            if isinstance(value, ast.Constant):
                continue
            if isinstance(value, ast.Call) and _callee_name(value.func) == "print":
                continue
        return False
    return True


# The no-op family, restated locally so an inline `sh -c 'true'` is read the
# same way the substance floor reads a bare `true`.
_NO_OP_INNER_RE = re.compile(r"^(true|:|exit\s+0|echo(\s|$).*|printf(\s|$).*)$")


def _inline_program_reason(verb: str, tokens: list[str], derive: Any) -> str | None:
    """Why an inline `-c` program cannot carry a proof claim, or None."""
    payload: str | None = None
    for index, argument in enumerate(tokens):
        if argument == "-c" and index + 1 < len(tokens):
            payload = tokens[index + 1]
            break
    if payload is None:
        return None
    if verb.startswith("python"):
        if _python_snippet_is_noop(payload):
            return (
                "no_op_program: the inline python program "
                f"{payload!r} has no assertion, no import and no failing exit "
                "path — it succeeds against any tree"
            )
        return None
    if derive is not None and _NO_OP_INNER_RE.match(payload.strip()):
        return (
            f"wrapped_no_op_command: {verb} -c {payload!r} runs a command that "
            "passes unconditionally; wrapping it in a shell does not make it "
            "falsifiable"
        )
    return None


_MIN_COMPARISON_OPERANDS = 2


def _search_vacuity_reason(
    tokens: list[str], upstream_paths: list[str], contract_path: str | None
) -> str | None:
    """Why this search invocation asserts nothing about the change, or None."""
    pattern, paths = _parse_search(tokens)
    targets = paths or upstream_paths
    if pattern is not None and _pattern_is_vacuous(pattern):
        return (
            f"vacuous_search_pattern: {pattern!r} matches arbitrary content, so "
            "the probe passes whether the work is right or wrong"
        )
    if contract_path is not None and any(
        _same_path(target, contract_path) for target in targets
    ):
        return (
            f"self_target_search: the search target is {contract_path} — the "
            "contract declaring this very check. It confirms the author wrote "
            "what the author wrote"
        )
    prose = [target for target in targets if _is_prose_path(target)]
    if prose:
        return (
            f"prose_satisfiable_search_target: a match in {', '.join(prose)} "
            "alone satisfies this search, and a sentence in documentation "
            "cannot fail when the code is wrong"
        )
    return None


def _pipeline_vacuity_reason(
    pipeline: list[list[str]], derive: Any, contract_path: str | None
) -> str | None:
    """Walk one pipeline left to right, carrying `cat`-supplied targets along."""
    upstream_paths: list[str] = []
    for raw_tokens in pipeline:
        tokens = _strip_runner_prefix(raw_tokens)
        if not tokens:
            continue
        verb = PurePosixPath(tokens[0]).name
        operands = [t for t in tokens[1:] if not t.startswith("-")]

        if verb in _FILE_SOURCE_VERBS:
            upstream_paths += operands
            continue
        if verb in _SEARCH_VERBS:
            reason = _search_vacuity_reason(tokens, upstream_paths, contract_path)
        else:
            reason = _non_search_vacuity_reason(verb, tokens, operands, derive)
        if reason is not None:
            return reason
    return None


def _non_search_vacuity_reason(
    verb: str, tokens: list[str], operands: list[str], derive: Any
) -> str | None:
    """The always-true forms that are not searches: tautological comparison,
    dry run, and an inline program that cannot fail."""
    if verb in _COMPARISON_VERBS:
        if len(operands) >= _MIN_COMPARISON_OPERANDS and len(set(operands)) == 1:
            return (
                f"self_comparison: {verb} compares {operands[0]} with itself, "
                "which cannot differ"
            )
        return None
    if verb in _DRY_RUN_VERBS and any(arg in _DRY_RUN_FLAGS for arg in tokens[1:]):
        return (
            f"dry_run_invocation: {verb} is invoked in dry-run mode, so the "
            "recipe is printed and never executed"
        )
    if verb in _INLINE_PROGRAM_VERBS:
        return _inline_program_reason(verb, tokens, derive)
    return None


def _runner_mask_reason(command: str, derive: Any, floor: Any) -> str | None:
    """True when a wrapper is the only thing lifting the command off the floor."""
    if derive is None or floor is None or not derive("", command).satisfies(floor):
        return None
    for raw_tokens in _shell_segments(command):
        stripped = _strip_runner_prefix(raw_tokens)
        if not stripped or stripped == raw_tokens:
            continue
        inner = shlex.join(stripped)
        inner_tier = derive("", inner)
        if not inner_tier.satisfies(floor):
            return (
                f"runner_prefix_masks_no_op: {inner!r} derives {inner_tier.value} "
                "on its own; the runner prefix is what moved it into a "
                "substantive family"
            )
    return None


def v1_vacuity_reason(
    check_type: str,
    check_value: str,
    derive: Any = None,
    floor: Any = None,
    contract_path: str | None = None,
) -> str | None:
    """Why this check cannot count toward the v1 falsifiability floor, or None.

    The OMN-14409 substance floor is deliberately GENEROUS at the family level
    ("when in doubt, ACCEPT") because it governs a 6,900-contract legacy corpus
    where a false reject teaches authors that honest evidence does not pay. v1
    has no legacy corpus, so these rules are ADDITIVE to the tier derivation —
    never a second tier vocabulary — and they read each check's ARGUMENTS
    instead of its verb.

    SCOPE — read this before quoting the gate. This is an ENUMERATED denylist of
    always-true forms, not a decision procedure for "can this command fail?".
    That question is undecidable in general, and the r1 round shipped prose
    claiming class closure that an adversarial replay falsified in eleven ways.
    What is closed is exactly the list below; what remains open is named at the
    end. Adding a form here is cheap — the list is meant to grow under attack.

    Named forms, each with the replay that motivated it:

    ``vacuous_search_pattern``
        the pattern matches arbitrary content. EXECUTED against unrelated probe
        strings (:func:`_pattern_is_vacuous`), so ``x*``, ``(a|)``, ``\\s*`` and
        ``.`` are all caught by behaviour — the r1 literal denylist caught only
        the spellings it listed.
    ``prose_satisfiable_search_target``
        a search that ANY prose target can satisfy. ``grep`` over several files
        succeeds when one of them matches, so ``grep -q x README.md src/a.py``
        is satisfied by the README alone; r1's ``all()`` let that through.
        Prose is recognised by suffix, by extensionless basename
        (``CHANGELOG``) and by directory (``docs/``).
    ``self_target_search``
        the search target IS the contract declaring the check. It reports that
        the author wrote what the author wrote.
    ``dry_run_invocation``
        ``make -n`` / ``--dry-run``: the recipe is printed, never executed.
    ``self_comparison``
        ``diff -u X X``: comparing a file with itself cannot differ.
    ``no_op_program`` / ``wrapped_no_op_command``
        ``python -c 'pass'`` and ``sh -c 'true'``: an inline program with no
        assertion, no import and no failing exit path.
    ``runner_prefix_masks_no_op``
        ``uv run true``. The substance floor already derives ``true`` to L0; a
        runner prefix moved it out of command position and it derived L1.
    ``check_type_label_only``
        ``derive_proof_tier`` short-circuits to L1 on ``check_type ==
        "test_passes"`` WITHOUT reading ``check_value``, so a prose value under
        that label is a self-declared tier.

    KNOWN RESIDUALS (open, by name — do not describe this function as closing
    the class): a hand-written script that always exits 0 (``./scripts/ok.sh``);
    ``python -c`` bodies that do real work but cannot fail; a POSIX BRE that
    Python's ``re`` refuses to compile; a search pattern that is specific yet
    matches something unrelated to the change; an extensionless prose file whose
    basename is not in ``_PROSE_BASENAMES``; a command with unbalanced quotes,
    which does not tokenize and so is not analysed at all; and any bespoke verb
    the floor's generous ``_EXECUTABLE_RE`` family accepts. These are gaps in an
    enumerated denylist, which is what this is.
    """
    command = (check_value or "").strip()
    if not command:
        return None  # already L0 by derivation; nothing to add

    for pipeline in _shell_pipelines(command):
        reason = _pipeline_vacuity_reason(pipeline, derive, contract_path)
        if reason is not None:
            return reason

    masked = _runner_mask_reason(command, derive, floor)
    if masked is not None:
        return masked

    if derive is not None and check_type:
        labelled = derive(check_type, command)
        unlabelled = derive("", command)
        if labelled != unlabelled:
            return (
                f"check_type_label_only: the tier comes from check_type "
                f"{check_type!r}, not from check_value — the value alone derives "
                f"{unlabelled.value}. A label is a claim; v1 requires the check "
                "itself to be a runnable, falsifiable command"
            )
    return None


def check_evidence_falsifiability(
    contract: dict[str, Any], subject: str
) -> list[Finding]:
    """A v1 contract needs at least one check that fails when the work is wrong.

    Reuses ``derive_proof_tier`` from the OMN-14409 substance floor rather than
    introducing a second proof vocabulary. Unlike that gate, v1 carries no
    legacy allowlist: nothing here is exempted by age or by author.

    The honest statement of strength, after two adversarial rounds: this leg
    rejects an ENUMERATED set of always-true forms (r1 closed five, r1r closed
    eleven more found by extending the same attacks), and it does NOT decide the
    general question "can this command fail when the work is wrong". Do not read
    "no grandfathering" as "no evasion" — the residuals are named in
    :func:`v1_vacuity_reason`. Two mechanisms carry what IS enforced, both
    scoped to v1 only:

    1. The OMN-14417 kill switch is turned **ON for v1**. ``GATE_SELF_REFERENTIAL``
       ships ``False`` in the substance floor for a measured reason: flipping it
       corpus-wide rejects 98.4% of new legacy contract traffic, because reading
       one's own receipt is the current house style there. That measurement is
       about the LEGACY corpus. v1 has no legacy corpus, so the concession does
       not transfer, and a circular ``grep -q '^status: PASS' drift/dod_receipts/…``
       is L0 here. The flip is applied to this gate's own privately-loaded module
       instance (:func:`_load_proof_tier_deriver` re-execs the file under a
       private name on every call), so the legacy gate's verdicts are untouched.
    2. :func:`v1_vacuity_reason` reads each check's ARGUMENTS rather than its
       verb, rejecting the named always-true forms listed in its docstring
       (vacuous pattern, prose-satisfiable target, self-targeting search,
       dry-run, self-comparison, no-op inline program, runner-masked no-op,
       label-only tier). Its residual list is part of the shipped claim.
    """
    module = _load_proof_tier_deriver()
    # v1 enables the kill switch the legacy gate defers. See (1) above; the
    # module object is private to this call, so nothing else observes the flip.
    module.GATE_SELF_REFERENTIAL = True
    self_referential = getattr(module, "_SELF_REFERENTIAL_RE", None)
    derive = module.derive_proof_tier
    floor = module.SUBSTANCE_FLOOR
    probes: list[str] = []
    substantive = 0
    for item in contract.get("dod_evidence") or []:
        if not isinstance(item, dict):
            continue
        for check in item.get("checks") or []:
            if not isinstance(check, dict):
                continue
            check_type = str(check.get("check_type") or "")
            value = str(check.get("check_value") or "")
            tier = derive(check_type, value)
            reason = v1_vacuity_reason(
                check_type,
                value,
                derive=derive,
                floor=floor,
                contract_path=subject,
            )
            if (
                reason is None
                and self_referential is not None
                and self_referential.search(value)
            ):
                reason = (
                    "self_referential: the probe reads the receipt corpus it is "
                    "itself part of (OMN-14417 circular class). v1 does not carry "
                    "the legacy grandfathering that defers this in the corpus gate"
                )
            counts = bool(tier.satisfies(floor)) and reason is None
            probes.append(
                f"[{tier.value}] {value[:90]}"
                + (f"\n    -> {reason}" if reason else "")
            )
            if counts:
                substantive += 1
    if substantive == 0:
        return [
            Finding(
                rule="evidence_unfalsifiable_overclaim",
                subject=subject,
                message=(
                    "no dod_evidence check can fail when the work is wrong. "
                    "Every check is either an existence/self-bind probe (tier "
                    "L0) or one of the always-true forms v1 rejects outright "
                    "(vacuous search pattern, prose-only search target, "
                    "check_type label without a runnable value, or a probe that "
                    "reads its own receipt). The per-check verdicts are below. "
                    "A probe that passes identically whether the code is correct "
                    "or broken cannot carry a proof claim"
                ),
                diff="\n".join(probes),
            )
        ]
    return []


# --------------------------------------------------------------------------
# Scope selection — per-touch migration, grandfather, NO backfill sweep.
# --------------------------------------------------------------------------
class BaseReader(Protocol):
    """Reads a path as it exists on the PR's base ref. None = added by the PR."""

    def read(self, path: str) -> str | None: ...


@dataclass
class GitBaseReader:
    """Reads base-ref blobs out of git."""

    root: Path
    base_ref: str

    def read(self, path: str) -> str | None:
        proc = subprocess.run(  # noqa: S603
            ["git", "show", f"{self.base_ref}:{path}"],  # noqa: S607
            cwd=self.root,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            return None
        return proc.stdout


def select_scope(
    changed_files: list[str], root: Path, base_reader: BaseReader
) -> list[str]:
    """Return the contracts this gate reads. Everything else is never opened.

    In scope:
      * every changed contract under ``contracts/v1/`` -- added or edited, no
        exemption and no allowlist;
      * any changed legacy contract that already carries the v1 marker on its
        base version or in the PR, so migration is ONE-WAY: once a contract is
        v1 it can never fall back out of the gate.

    Never read:
      * every contract the PR does not touch. This function does not enumerate
        the corpus -- there is no backfill sweep, by construction;
      * legacy ``contracts/*.yaml`` without the marker (see :data:`V1_DIR` for
        why that path cannot carry v1 blocks today).
    """
    scope: list[str] = []
    for path in changed_files:
        if not path.startswith("contracts/") or not path.endswith(".yaml"):
            continue
        if not (root / path).exists():
            continue  # deleted by the PR
        if path.startswith(V1_DIR):
            scope.append(path)
            continue
        head_text = (root / path).read_text(encoding="utf-8")
        base_text = base_reader.read(path) or ""
        if V1_MARKER in head_text or V1_MARKER in base_text:
            scope.append(path)
    return scope


# --------------------------------------------------------------------------
# Whole-contract evaluation.
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class IdentityInputs:
    """The P2 leg's inputs, as ONE object so the exclusion cannot be implicit.

    ``waived`` is a caller DECLARATION, never a default. REMEDIATION r1r:
    omitting the PR body used to silently drop P2, which made the contract's own
    ``gate-self-applies`` DoD check WEAKER than the CI gate it certifies — that
    check could be receipted PASS while the required gate was RED on the same
    contract for an identity finding. Absent evidence is now RED
    (``identity_not_evaluated``); a caller with no PR to read (the pre-commit
    hook, a push-event CI run) must say so, and the exclusion is then visible in
    the invocation itself.
    """

    pr_body: str | None = None
    ticket_body_reader: Any = None
    waived: bool = False


def evaluate_contract(
    path: str,
    root: Path,
    collector: Collector,
    identity: IdentityInputs | None = None,
) -> list[Finding]:
    """Run every leg over one contract. Returns all findings, never raises."""
    identity = identity or IdentityInputs()
    pr_body = identity.pr_body
    text = (root / path).read_text(encoding="utf-8")
    try:
        contract = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        return [Finding(rule="contract_unparseable", subject=path, message=str(exc))]
    if not isinstance(contract, dict):
        return [
            Finding(
                rule="contract_unparseable",
                subject=path,
                message="contract is not a mapping",
            )
        ]

    findings = check_schema(contract, path)
    findings += check_case_space(contract, path)
    findings += check_cases(contract, path, root, collector)
    findings += check_bindings(contract, path, root, collector)
    findings += check_dependencies(contract, path, root)
    findings += check_evidence_falsifiability(contract, path)

    if pr_body is None and not identity.waived:
        findings.append(
            Finding(
                rule="identity_not_evaluated",
                subject=path,
                message=(
                    "the P2 ticket-identity leg did not run: no PR body was "
                    "supplied and no caller declared the exclusion. Pass "
                    "--pr-body-file to prove three-way identity, or "
                    "--skip-identity to state on the record that this "
                    "invocation is a strict subset of the CI gate. A silent "
                    "subset is how a DoD check ends up weaker than the gate it "
                    "certifies"
                ),
            )
        )
    if pr_body is not None:
        ticket_id = str(contract.get("ticket_id") or Path(path).stem)
        # No reader at all is the same fact as an unreachable Linear: the ticket
        # body could not be obtained, so identity cannot be proven. RED.
        reader = identity.ticket_body_reader or make_ticket_body_reader({})
        ticket_body: str | None
        try:
            ticket_body = str(reader(ticket_id))
        except LinearUnreachableError:
            ticket_body = None
        findings += check_identity(ticket_id, text, pr_body, ticket_body)
    return findings


def main(argv: list[str] | None = None) -> int:
    """CLI. ``--changed-files`` scopes the run; nothing else is ever read."""
    parser = argparse.ArgumentParser(
        prog="check-contract-shape-v1",
        description="Canonical OCC contract shape v1 gate (OMN-15669, R-0802-9).",
    )
    parser.add_argument("--changed-files", nargs="*", default=[])
    # pre-commit passes the staged files POSITIONALLY (pass_filenames: true).
    # Accepting them here is what lets the identical entrypoint be both the CI
    # gate and the local hook — CLAUDE.md rule 5: a detection surface that is
    # not also a local gate is advisory, and two spellings of the same check
    # drift. The union is taken so `--changed-files` keeps working unchanged.
    parser.add_argument("files", nargs="*", default=[])
    parser.add_argument("--base-ref", default="origin/dev")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--ticket-bodies",
        type=Path,
        default=None,
        help="JSON map {ticket_id: body} fetched from Linear by the workflow "
        "layer. Absent or missing a key => identity_linear_unreachable RED.",
    )
    parser.add_argument(
        "--pr-body-file",
        type=Path,
        default=None,
        help="File holding the PR body. Required for the P2 identity leg; "
        "omitting it without --skip-identity is RED.",
    )
    parser.add_argument(
        "--skip-identity",
        action="store_true",
        help="Declare on the record that this invocation excludes the P2 "
        "identity leg (no PR exists to read: the pre-commit hook, a push-event "
        "run). Without it, a missing PR body is identity_not_evaluated RED.",
    )
    args = parser.parse_args(argv)

    root: Path = args.root.resolve()
    requested = list(dict.fromkeys([*args.changed_files, *args.files]))
    scope = select_scope(requested, root, GitBaseReader(root, args.base_ref))
    if not scope:
        print(
            "contract shape v1: no in-scope contract in this PR "
            "(untouched and append-only-legacy contracts are never read)"
        )
        return 0

    pr_body: str | None = None
    if args.pr_body_file is not None:
        if not args.pr_body_file.exists():
            print(
                f"[ERROR] PR body file not found: {args.pr_body_file}", file=sys.stderr
            )
            return 2
        pr_body = args.pr_body_file.read_text(encoding="utf-8")

    collector = PytestCollector(root=root)
    reader = make_ticket_body_reader(load_ticket_bodies(args.ticket_bodies))
    all_findings: list[Finding] = []
    for path in scope:
        all_findings += evaluate_contract(
            path,
            root,
            collector,
            IdentityInputs(
                pr_body=pr_body,
                ticket_body_reader=reader,
                waived=bool(args.skip_identity),
            ),
        )

    if all_findings:
        print(
            f"\ncontract shape v1: {len(all_findings)} finding(s) across "
            f"{len(scope)} in-scope contract(s):\n"
        )
        for finding in all_findings:
            print(finding.render())
        print(
            "\nThis gate has no reviewer and no allowlist. Fix the shape "
            "(schemas/occ_contract_v1.schema.yaml is the only authority).\n"
        )
        return 1

    print(f"contract shape v1 OK: {len(scope)} in-scope contract(s) conform")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    sys.exit(main())
