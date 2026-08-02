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
    or any human judgement: shape validation *replaces* review.

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
    validates mock and real payloads against the SAME schema. Mock-shape
    divergence (the OMN-15598 class) is thereby unrepresentable rather than
    merely discouraged.

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
import difflib
import functools
import hashlib
import importlib
import importlib.util
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import yaml
from jsonschema import Draft202012Validator

__all__ = [
    "CONTRACT_BLOCK_HEADING",
    "SCHEMA_PATH",
    "Finding",
    "LinearUnreachableError",
    "PytestCollector",
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
    "select_scope",
    "sha256_block",
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
SEAM_ASSERT_SYMBOL = "assert_seam_shape("


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
            test_file = root / str(case.get("test_path", ""))
            if not test_file.exists():
                continue  # reported by check_cases
            source = test_file.read_text(encoding="utf-8")
            if seam_schema and seam_schema not in source:
                findings.append(
                    Finding(
                        rule="seam_schema_not_cited",
                        subject=f"{subject}:{case_id}",
                        message=(
                            f"{case.get('test_path')} does not cite seam_schema "
                            f"{seam_schema!r}. The mock fixture must validate "
                            "against the SAME schema ref the real dependency "
                            "does, or the two can drift silently"
                        ),
                    )
                )
            if SEAM_ASSERT_SYMBOL not in source:
                findings.append(
                    Finding(
                        rule="seam_validation_not_executed",
                        subject=f"{subject}:{case_id}",
                        message=(
                            f"{case.get('test_path')} never calls "
                            f"{SEAM_ASSERT_SYMBOL[:-1]}(...). Citing a seam schema "
                            "in a comment is not validation — the check must "
                            "EXECUTE against the schema"
                        ),
                    )
                )
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


def check_evidence_falsifiability(
    contract: dict[str, Any], subject: str
) -> list[Finding]:
    """A v1 contract needs at least one check that fails when the work is wrong.

    Reuses ``derive_proof_tier`` from the OMN-14409 substance floor rather than
    introducing a second proof vocabulary. Unlike that gate, v1 has NO legacy
    allowlist: an open-state self-bind probe plus a claim of proof is an
    unfalsifiability overclaim and is shape-RED.
    """
    module = _load_proof_tier_deriver()
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
            value = str(check.get("check_value") or "")
            tier = derive(str(check.get("check_type") or ""), value)
            probes.append(f"[{tier.value}] {value[:90]}")
            if bool(tier.satisfies(floor)):
                substantive += 1
    if substantive == 0:
        return [
            Finding(
                rule="evidence_unfalsifiable_overclaim",
                subject=subject,
                message=(
                    "every dod_evidence check is an existence/self-bind probe "
                    "(tier L0) while the contract declares cases claiming proof. "
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
def evaluate_contract(
    path: str,
    root: Path,
    collector: Collector,
    pr_body: str | None = None,
    ticket_body_reader: Any = None,
) -> list[Finding]:
    """Run every leg over one contract. Returns all findings, never raises."""
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

    if pr_body is not None:
        ticket_id = str(contract.get("ticket_id") or Path(path).stem)
        # No reader at all is the same fact as an unreachable Linear: the ticket
        # body could not be obtained, so identity cannot be proven. RED.
        reader = ticket_body_reader or make_ticket_body_reader({})
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
        help="File holding the PR body. Omit to skip the P2 identity leg "
        "(the leg is meaningless without a PR).",
    )
    args = parser.parse_args(argv)

    root: Path = args.root.resolve()
    scope = select_scope(
        list(args.changed_files), root, GitBaseReader(root, args.base_ref)
    )
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
            path, root, collector, pr_body=pr_body, ticket_body_reader=reader
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
