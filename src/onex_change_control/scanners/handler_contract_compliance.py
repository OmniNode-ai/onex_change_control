# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Handler-contract cross-reference scanner.

AST-based analysis of handler Python files and their contract.yaml
declarations to detect contract compliance violations.

Uses ast.walk() to find string literals and import statements,
skipping docstrings and comments to reduce false positives.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

import yaml

from onex_change_control.enums.enum_compliance_verdict import EnumComplianceVerdict
from onex_change_control.enums.enum_compliance_violation import EnumComplianceViolation
from onex_change_control.enums.enum_reachability import EnumReachability
from onex_change_control.models.model_freestanding_imperative_result import (
    ModelFreestandingImperativeFinding,
    ModelFreestandingImperativeResult,
)
from onex_change_control.models.model_handler_compliance_result import (
    ModelHandlerComplianceResult,
)

# --- Topic detection patterns ---

# Known ONEX topic patterns
_ONEX_TOPIC_RE = re.compile(r"onex\.evt\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.v\d+")

# Known bare topic names used in the platform
_BARE_TOPIC_NAMES: frozenset[str] = frozenset(
    {
        "agent-actions",
        "agent-transformation-events",
        "agent.routing.requested.v1",
        "agent.routing.completed.v1",
        "agent.routing.failed.v1",
        "router-performance-metrics",
        "documentation-changed",
    }
)

# --- Transport detection ---

# Call-site transport indicators (precise call-site detection only)
_TRANSPORT_CALLSITE_PATTERNS: dict[str, str] = {
    "session.execute": "DATABASE",
    "session.query": "DATABASE",
    "asyncpg.connect": "DATABASE",
    "asyncpg.create_pool": "DATABASE",
    "psycopg.connect": "DATABASE",
    "create_engine": "DATABASE",
    "create_async_engine": "DATABASE",
    "AsyncClient": "HTTP",
    "httpx.get": "HTTP",
    "httpx.post": "HTTP",
    "requests.get": "HTTP",
    "requests.post": "HTTP",
    "KafkaProducer": "KAFKA",
    "KafkaConsumer": "KAFKA",
    "AIOKafkaProducer": "KAFKA",
    "AIOKafkaConsumer": "KAFKA",
    "QdrantClient": "QDRANT",
}

_IMPERATIVE_VIOLATION_THRESHOLD = 2

# --- Freestanding imperative-IO detection ---

# Inline suppression comment. A finding on a line carrying this comment is
# retained for visibility but does not count as an active violation.
_NO_CONTRACT_CHECK_RE = re.compile(r"#\s*no-contract-check:")

# Dotted-call substrings that constitute raw HTTP inference / transport.
_RAW_HTTP_CALL_PATTERNS: frozenset[str] = frozenset(
    {
        "httpx.get",
        "httpx.post",
        "httpx.put",
        "httpx.patch",
        "httpx.delete",
        "httpx.request",
        "httpx.stream",
        "httpx.Client",
        "httpx.AsyncClient",
        "requests.get",
        "requests.post",
        "requests.put",
        "requests.patch",
        "requests.delete",
        "requests.request",
        "requests.Session",
        "aiohttp.ClientSession",
        "aiohttp.request",
        "urllib.request.urlopen",
        "urlopen",
    }
)

# Dotted-call substrings that construct a raw Kafka client.
_RAW_KAFKA_CALL_PATTERNS: frozenset[str] = frozenset(
    {
        "KafkaProducer",
        "KafkaConsumer",
        "AIOKafkaProducer",
        "AIOKafkaConsumer",
        "confluent_kafka.Producer",
        "confluent_kafka.Consumer",
        "Producer",
        "Consumer",
    }
)

# Dotted-call substrings that open a direct DB / cache connection.
_DIRECT_DB_CALL_PATTERNS: frozenset[str] = frozenset(
    {
        "asyncpg.connect",
        "asyncpg.create_pool",
        "psycopg.connect",
        "psycopg2.connect",
        "sqlite3.connect",
        "redis.Redis",
        "redis.from_url",
        "redis.asyncio.Redis",
        "redis.asyncio.from_url",
        "aioredis.from_url",
    }
)

# subprocess argv[0] tokens that are unconditionally network/remote tools.
# ``git`` is intentionally NOT here: local git plumbing (rev-parse, log,
# status, ...) performs no network IO and is a legitimate freestanding op.
# Network git is handled separately via _GIT_NETWORK_VERBS below.
_SUBPROCESS_NETWORK_TOKENS: frozenset[str] = frozenset(
    {
        "ssh",
        "scp",
        "rsync",
        "curl",
        "wget",
    }
)

# argv[0] value for the git CLI (after stripping any path prefix).
_GIT_ARGV0 = "git"

# git subcommands that actually contact a remote. Only these make a
# ``git ...`` subprocess a network op; local plumbing/porcelain such as
# rev-parse, log, describe, status, branch, show, diff, rev-list, commit,
# add, and tag stay unflagged (git commit-SHA provenance is the canonical
# legitimate local use).
_GIT_NETWORK_VERBS: frozenset[str] = frozenset(
    {
        "fetch",
        "clone",
        "push",
        "pull",
        "ls-remote",
    }
)
_SUBPROCESS_CALL_PATTERNS: frozenset[str] = frozenset(
    {
        "subprocess.run",
        "subprocess.call",
        "subprocess.check_call",
        "subprocess.check_output",
        "subprocess.Popen",
    }
)

# Call-keyword names whose numeric literal values are hardcoded inference config.
# ``timeout`` is intentionally excluded: it is a control-flow safety bound on
# local IO (``subprocess.run``, socket reads, ``asyncio.wait_for``, file-lock
# acquisition), not an LLM sampling/generation knob. Flagging every numeric
# ``timeout=`` over-flagged legitimate local timeouts. The remaining params are
# unambiguously inference-only and stay flagged per contract-config doctrine.
_INFERENCE_PARAM_KWARGS: frozenset[str] = frozenset(
    {
        "max_tokens",
        "max_new_tokens",
        "temperature",
        "top_p",
        "top_k",
        "presence_penalty",
        "frequency_penalty",
    }
)

# LAN / private-range IP literals are hardcoded config.
_LAN_IP_RE = re.compile(
    r"\b(?:192\.168|10\.|172\.(?:1[6-9]|2\d|3[01])|127\.0\.0\.1)"
    r"(?:\.\d{1,3}){1,3}\b"
)


def _normalize_topic_list(raw: list[Any]) -> list[str]:
    """Normalize a topic list that may contain strings or dicts with a 'topic' key."""
    result: list[str] = []
    for item in raw:
        if isinstance(item, str):
            result.append(item)
        elif isinstance(item, dict) and "topic" in item:
            result.append(str(item["topic"]))
    return result


def parse_contract_topics(contract_path: Path) -> tuple[list[str], list[str]]:
    """Extract publish and subscribe topics from contract.yaml.

    Returns:
        Tuple of (publish_topics, subscribe_topics).
    """
    data = _load_yaml(contract_path)
    if data is None:
        return [], []

    event_bus = data.get("event_bus", {}) or {}
    publish = _normalize_topic_list(event_bus.get("publish_topics", []) or [])
    subscribe = _normalize_topic_list(event_bus.get("subscribe_topics", []) or [])
    return publish, subscribe


def parse_contract_transports(contract_path: Path) -> list[str]:
    """Extract declared transport types from contract.yaml.

    Looks at metadata.transport_type and handler_routing.handlers[].handler_type.
    """
    data = _load_yaml(contract_path)
    if data is None:
        return []

    transports: list[str] = []

    # Check metadata.transport_type
    metadata = data.get("metadata", {}) or {}
    if transport := metadata.get("transport_type"):
        transports.append(str(transport).upper())

    # Check handler_routing.handlers[].handler_type
    handler_routing = data.get("handler_routing", {}) or {}
    for handler_entry in handler_routing.get("handlers", []) or []:
        handler_info = handler_entry.get("handler", {}) or {}
        if handler_type := handler_info.get("handler_type"):
            transports.append(str(handler_type).upper())

    # Infer transports from node_type and topics
    _infer_kafka_transport(data, transports)

    return transports


def _infer_kafka_transport(data: dict[str, Any], transports: list[str]) -> None:
    """Add KAFKA transport if EFFECT node has declared topics."""
    node_type = data.get("node_type", "")
    if "EFFECT" not in str(node_type):
        return
    event_bus = data.get("event_bus", {}) or {}
    has_topics = event_bus.get("publish_topics") or event_bus.get("subscribe_topics")
    if has_topics and "KAFKA" not in transports:
        transports.append("KAFKA")


def parse_contract_handler_routing(
    contract_path: Path,
) -> list[dict[str, Any]]:
    """Extract handler routing entries from contract.yaml.

    Returns list of handler entries with name, module, operation.
    """
    data = _load_yaml(contract_path)
    if data is None:
        return []

    handler_routing = data.get("handler_routing", {}) or {}
    entries: list[dict[str, Any]] = []
    for handler_entry in handler_routing.get("handlers", []) or []:
        handler_info = handler_entry.get("handler", {}) or {}
        entries.append(
            {
                "name": handler_info.get("name", ""),
                "module": handler_info.get("module", ""),
                "operation": handler_entry.get("operation", ""),
            }
        )
    return entries


def scan_handler_topics(handler_path: Path) -> list[str]:
    """Find topic string literals in handler code using AST.

    Only flags string literals inside function bodies,
    skipping docstrings and comments.
    """
    source = handler_path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    topics: list[str] = []
    docstring_nodes = _get_docstring_nodes(tree)

    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        if id(node) in docstring_nodes:
            continue
        value = node.value
        if _ONEX_TOPIC_RE.search(value) or value in _BARE_TOPIC_NAMES:
            topics.append(value)

    return sorted(set(topics))


def scan_handler_transports(handler_path: Path) -> list[str]:
    """Detect transport usage in handler via AST.

    Only returns transports confirmed by call-site usage, not bare imports.
    Import-only references (including TYPE_CHECKING guards) are excluded
    to avoid false positives.

    Returns deduplicated list of transport types found at call sites.
    """
    source = handler_path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    callsite_transports: set[str] = set()

    for node in ast.walk(tree):
        _check_callsite_transports(node, callsite_transports)

    return sorted(callsite_transports)


def _check_callsite_transports(node: ast.AST, transports: set[str]) -> None:
    """Check call-site and attribute nodes for transport usage."""
    call_str: str | None = None
    if isinstance(node, ast.Call):
        call_str = _get_call_string(node)
    elif isinstance(node, ast.Attribute):
        call_str = _get_attribute_string(node)

    if call_str:
        for pattern, transport in _TRANSPORT_CALLSITE_PATTERNS.items():
            if pattern in call_str:
                transports.add(transport)


def scan_node_py_logic(node_py_path: Path) -> list[str]:
    """Return custom method names in node.py beyond __init__.

    A clean declarative node should only have __init__ calling super().__init__.
    Any other methods indicate business logic in the wrong layer.
    """
    if not node_py_path.exists():
        return []

    source = node_py_path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    custom_methods: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for item in node.body:
                if (
                    isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and item.name != "__init__"
                ):
                    custom_methods.append(item.name)

    return custom_methods


def cross_reference(
    node_dir: Path,
    repo: str,
    allowlisted_paths: frozenset[str] | None = None,
) -> list[ModelHandlerComplianceResult]:
    """Run all checks for all handlers in a node directory.

    Args:
        node_dir: Path to node directory containing contract.yaml and handlers/.
        repo: Repository name.
        allowlisted_paths: Set of handler paths that are allowlisted.

    Returns:
        List of compliance results, one per handler file.
    """
    if allowlisted_paths is None:
        allowlisted_paths = frozenset()

    contract_ctx = _build_contract_context(node_dir)
    node_logic = scan_node_py_logic(node_dir / "node.py")

    handlers_dir = node_dir / "handlers"
    if not handlers_dir.exists():
        return []

    handler_files = sorted(
        f
        for f in handlers_dir.rglob("*.py")
        if f.name != "__init__.py" and not f.name.startswith("_")
    )

    return [
        _audit_handler(
            handler_file=hf,
            node_dir=node_dir,
            repo=repo,
            contract_ctx=contract_ctx,
            node_logic=node_logic,
            allowlisted_paths=allowlisted_paths,
        )
        for hf in handler_files
    ]


def _build_contract_context(
    node_dir: Path,
) -> dict[str, Any]:
    """Extract all contract info needed for handler auditing."""
    contract_path = node_dir / "contract.yaml"
    has_contract = contract_path.exists()
    if not has_contract:
        return {
            "has_contract": False,
            "declared_topics": [],
            "declared_transports": [],
            "routed_modules": set(),
            "contract_path": None,
        }

    publish_topics, subscribe_topics = parse_contract_topics(contract_path)
    routing_entries = parse_contract_handler_routing(contract_path)
    return {
        "has_contract": True,
        "declared_topics": sorted(set(publish_topics + subscribe_topics)),
        "declared_transports": parse_contract_transports(contract_path),
        "routed_modules": {e["module"] for e in routing_entries},
        "contract_path": contract_path,
    }


def _audit_handler(  # noqa: PLR0913  Why: audit requires full cross-referencing context
    handler_file: Path,
    node_dir: Path,
    repo: str,
    contract_ctx: dict[str, Any],
    node_logic: list[str],
    allowlisted_paths: frozenset[str],
) -> ModelHandlerComplianceResult:
    """Audit a single handler file against its contract context."""
    base_dir = node_dir.parent.parent.parent
    rel_path = str(handler_file.relative_to(base_dir))
    is_allowlisted = rel_path in allowlisted_paths

    if not contract_ctx["has_contract"]:
        return ModelHandlerComplianceResult(
            handler_path=rel_path,
            node_dir=str(node_dir.relative_to(base_dir)),
            repo=repo,
            contract_path=None,
            verdict=EnumComplianceVerdict.MISSING_CONTRACT,
            allowlisted=is_allowlisted,
            reachability=_handler_reachability(rel_path, is_routed=True),
        )

    violations, violation_details = _collect_violations(
        handler_file, node_dir, contract_ctx, node_logic
    )
    used_topics = scan_handler_topics(handler_file)
    used_transports = scan_handler_transports(handler_file)
    undeclared = [t for t in used_topics if t not in contract_ctx["declared_topics"]]
    undeclared_transports = [
        t for t in used_transports if t not in contract_ctx["declared_transports"]
    ]
    handler_module = _infer_module_path(handler_file, node_dir)
    in_routing = handler_module in contract_ctx["routed_modules"]

    verdict = _determine_verdict(violations, is_allowlisted=is_allowlisted)
    contract_rel = str(contract_ctx["contract_path"].relative_to(base_dir))

    return ModelHandlerComplianceResult(
        handler_path=rel_path,
        node_dir=str(node_dir.relative_to(base_dir)),
        repo=repo,
        contract_path=contract_rel,
        violations=violations,
        violation_details=violation_details,
        declared_topics=contract_ctx["declared_topics"],
        used_topics=used_topics,
        undeclared_topics=undeclared,
        declared_transports=contract_ctx["declared_transports"],
        used_transports=used_transports,
        undeclared_transports=undeclared_transports,
        handler_in_routing=in_routing,
        verdict=verdict,
        allowlisted=is_allowlisted,
        reachability=_handler_reachability(rel_path, is_routed=in_routing),
    )


def _handler_reachability(rel_path: str, *, is_routed: bool) -> EnumReachability:
    """Classify handler reachability for static guard blocking."""
    parts = set(Path(rel_path).parts)
    if "tests" in parts or Path(rel_path).name.startswith("test_"):
        return EnumReachability.TEST_HARNESS
    return EnumReachability.LIVE if is_routed else EnumReachability.DEAD


def _collect_violations(
    handler_file: Path,
    node_dir: Path,
    contract_ctx: dict[str, Any],
    node_logic: list[str],
) -> tuple[list[EnumComplianceViolation], list[str]]:
    """Collect all violations for a handler file."""
    violations: list[EnumComplianceViolation] = []
    details: list[str] = []

    # Check 1: Topic compliance — any hardcoded topic literal is a violation,
    # even if declared in the contract. Declaration and hardcoding are orthogonal:
    # handlers should use contract-driven dispatch, not string literals.
    used_topics = scan_handler_topics(handler_file)
    for topic in used_topics:
        violations.append(EnumComplianceViolation.HARDCODED_TOPIC)
        if topic in contract_ctx["declared_topics"]:
            details.append(
                f"hardcoded topic '{topic}' (declared but should use contract dispatch)"
            )
        else:
            details.append(f"hardcoded topic '{topic}' not in contract")

    # Check 2: Transport compliance
    used_transports = scan_handler_transports(handler_file)
    for transport in used_transports:
        if transport not in contract_ctx["declared_transports"]:
            violations.append(EnumComplianceViolation.UNDECLARED_TRANSPORT)
            details.append(f"undeclared transport {transport} used in handler")

    # Check 3: Handler routing registration
    handler_module = _infer_module_path(handler_file, node_dir)
    if handler_module not in contract_ctx["routed_modules"]:
        violations.append(EnumComplianceViolation.MISSING_HANDLER_ROUTING)
        details.append("handler not registered in contract handler_routing")

    # Check 4: Logic in node.py
    if node_logic:
        violations.append(EnumComplianceViolation.LOGIC_IN_NODE)
        details.append(f"node.py has custom methods: {', '.join(node_logic)}")

    return violations, details


def _determine_verdict(
    violations: list[EnumComplianceViolation],
    *,
    is_allowlisted: bool,
) -> EnumComplianceVerdict:
    """Determine the compliance verdict from violations."""
    if is_allowlisted:
        return EnumComplianceVerdict.ALLOWLISTED
    if not violations:
        return EnumComplianceVerdict.COMPLIANT
    if len(violations) >= _IMPERATIVE_VIOLATION_THRESHOLD:
        return EnumComplianceVerdict.IMPERATIVE
    return EnumComplianceVerdict.HYBRID


def scan_freestanding_imperative_io(
    python_file: Path,
    repo: str,
    *,
    allowlisted: bool = False,
    reachability: EnumReachability = EnumReachability.LIVE,
) -> ModelFreestandingImperativeResult:
    """Detect freestanding imperative IO in a non-handler source module.

    AST-based detection of contract-bypassing imperative IO that the node /
    handler scanner cannot see because the module is not under a
    ``node_*/handlers/`` path. Detects:

    - raw HTTP (``httpx`` / ``requests`` / ``aiohttp`` / ``urllib``)
    - raw Kafka producer/consumer construction
    - direct DB / cache connections (``asyncpg`` / ``psycopg`` / ``sqlite3``)
    - hardcoded inference params (numeric ``max_tokens`` / ``temperature`` / ...)
    - hardcoded ONEX topic string literals
    - hardcoded LAN / private-range IP literals
    - ``subprocess`` invocations of network / git operations

    A finding on a line carrying an inline ``# no-contract-check: <reason>``
    comment is retained but marked ``suppressed`` and excluded from the verdict.

    Args:
        python_file: Path to the freestanding module to scan.
        repo: Repository name.
        allowlisted: Whether the module path is baselined in the allowlist.

    Returns:
        A per-module result carrying all findings and an overall verdict.
    """
    source = python_file.read_text(encoding="utf-8")
    rel_path = python_file.name
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return ModelFreestandingImperativeResult(
            module_path=rel_path,
            repo=repo,
            findings=[],
            verdict=EnumComplianceVerdict.COMPLIANT,
            allowlisted=allowlisted,
            reachability=reachability,
        )

    suppressed_lines = _suppressed_lines(source)
    docstring_nodes = _get_docstring_nodes(tree)
    raw_findings: list[tuple[int, EnumComplianceViolation, str]] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            _scan_call_node(node, raw_findings)
        elif isinstance(node, ast.Dict):
            _scan_dict_literal(node, raw_findings)
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstring_nodes
        ):
            _scan_string_constant(node.lineno, node.value, raw_findings)

    findings = [
        ModelFreestandingImperativeFinding(
            violation=violation,
            line=line,
            detail=detail,
            suppressed=line in suppressed_lines,
            reachability=reachability,
        )
        for line, violation, detail in sorted(raw_findings, key=lambda item: item[0])
    ]
    active = [f.violation for f in findings if not f.suppressed]
    verdict = _determine_verdict(active, is_allowlisted=allowlisted)

    return ModelFreestandingImperativeResult(
        module_path=rel_path,
        repo=repo,
        findings=findings,
        verdict=verdict,
        allowlisted=allowlisted,
        reachability=reachability,
    )


def _scan_call_node(
    node: ast.Call,
    findings: list[tuple[int, EnumComplianceViolation, str]],
) -> None:
    """Inspect a Call node for transport, subprocess, and inference-param debt."""
    call_str = _get_call_string(node)
    if call_str is not None:
        _classify_call(node, call_str, findings)

    for keyword in node.keywords:
        if (
            keyword.arg in _INFERENCE_PARAM_KWARGS
            and isinstance(keyword.value, ast.Constant)
            and isinstance(keyword.value.value, (int, float))
            and not isinstance(keyword.value.value, bool)
        ):
            findings.append(
                (
                    node.lineno,
                    EnumComplianceViolation.HARDCODED_CONFIG,
                    f"hardcoded inference param '{keyword.arg}="
                    f"{keyword.value.value}' passed as call kwarg",
                )
            )


def _scan_dict_literal(
    node: ast.Dict,
    findings: list[tuple[int, EnumComplianceViolation, str]],
) -> None:
    """Flag inference params hardcoded as dict keys (e.g. JSON request bodies)."""
    for key, value in zip(node.keys, node.values, strict=True):
        if (
            isinstance(key, ast.Constant)
            and isinstance(key.value, str)
            and key.value in _INFERENCE_PARAM_KWARGS
            and isinstance(value, ast.Constant)
            and isinstance(value.value, (int, float))
            and not isinstance(value.value, bool)
        ):
            param_name: str = key.value
            param_value: int | float = value.value
            findings.append(
                (
                    value.lineno,
                    EnumComplianceViolation.HARDCODED_CONFIG,
                    f"hardcoded inference param '{param_name}={param_value}' "
                    "in dict literal",
                )
            )


def _classify_call(
    node: ast.Call,
    call_str: str,
    findings: list[tuple[int, EnumComplianceViolation, str]],
) -> None:
    """Map a dotted call string to its imperative-IO violation, if any."""
    if any(pattern in call_str for pattern in _RAW_HTTP_CALL_PATTERNS):
        findings.append(
            (
                node.lineno,
                EnumComplianceViolation.RAW_HTTP_INFERENCE,
                f"raw HTTP call '{call_str}' bypasses contract transport",
            )
        )
        return
    if any(pattern in call_str for pattern in _DIRECT_DB_CALL_PATTERNS):
        findings.append(
            (
                node.lineno,
                EnumComplianceViolation.DIRECT_DB,
                f"direct DB/cache connection '{call_str}' bypasses injected services",
            )
        )
        return
    if call_str in _RAW_KAFKA_CALL_PATTERNS:
        findings.append(
            (
                node.lineno,
                EnumComplianceViolation.RAW_KAFKA,
                f"raw Kafka client '{call_str}' bypasses injected event bus",
            )
        )
        return
    if call_str in _SUBPROCESS_CALL_PATTERNS and _subprocess_is_network(node):
        findings.append(
            (
                node.lineno,
                EnumComplianceViolation.SUBPROCESS_NETWORK,
                f"subprocess network/git op via '{call_str}'",
            )
        )


def _subprocess_is_network(node: ast.Call) -> bool:
    """Return True if a subprocess call performs a network/remote operation.

    A plain remote tool (``ssh``/``scp``/``rsync``/``curl``/``wget``) always
    counts. ``git`` only counts when its subcommand is a network verb
    (``fetch``/``clone``/``push``/``pull``/``ls-remote``); local git plumbing
    such as ``git rev-parse HEAD`` performs no network IO and is not flagged.
    """
    if not node.args:
        return False
    first = node.args[0]
    tokens: list[str] = []
    if isinstance(first, ast.List):
        tokens = [
            elt.value
            for elt in first.elts
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
        ]
    elif isinstance(first, ast.Constant) and isinstance(first.value, str):
        tokens = first.value.split()
    if not tokens:
        return False
    head = tokens[0].rsplit("/", 1)[-1]
    if head in _SUBPROCESS_NETWORK_TOKENS:
        return True
    if head == _GIT_ARGV0:
        return _git_argv_is_network(tokens[1:])
    return False


def _git_argv_is_network(argv: list[str]) -> bool:
    """Return True if a git argv (excluding the ``git`` arg) hits a remote.

    Scans for the first non-option arg (the subcommand), skipping global
    flags like ``-C <dir>`` / ``--no-pager``, and flags it only when that
    subcommand is in _GIT_NETWORK_VERBS. Local plumbing stays unflagged.
    """
    skip_next = False
    for arg in argv:
        if skip_next:
            skip_next = False
            continue
        if arg == "-C":
            # ``git -C <dir>`` — the dir is the next arg; skip both.
            skip_next = True
            continue
        if arg.startswith("-"):
            continue
        return arg in _GIT_NETWORK_VERBS
    return False


def _scan_string_constant(
    lineno: int,
    value: str,
    findings: list[tuple[int, EnumComplianceViolation, str]],
) -> None:
    """Inspect a non-docstring string literal for topics and LAN IPs."""
    if _ONEX_TOPIC_RE.search(value) or value in _BARE_TOPIC_NAMES:
        findings.append(
            (
                lineno,
                EnumComplianceViolation.HARDCODED_TOPIC,
                f"hardcoded topic literal '{value}'",
            )
        )
    if _LAN_IP_RE.search(value):
        findings.append(
            (
                lineno,
                EnumComplianceViolation.HARDCODED_CONFIG,
                f"hardcoded LAN/private IP literal '{value}'",
            )
        )


def _suppressed_lines(source: str) -> set[int]:
    """Return 1-based line numbers carrying a '# no-contract-check:' comment."""
    return {
        index
        for index, line in enumerate(source.splitlines(), start=1)
        if _NO_CONTRACT_CHECK_RE.search(line)
    }


# --- Internal helpers ---


def _load_yaml(path: Path) -> dict[str, Any] | None:
    """Load and return a YAML file as a dict."""
    if not path.exists():
        return None
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        return None
    return data


def _get_docstring_nodes(tree: ast.Module) -> set[int]:
    """Collect ids of AST nodes that are docstrings."""
    docstring_ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)
        ) and (
            node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)
        ):
            docstring_ids.add(id(node.body[0].value))
    return docstring_ids


def _get_call_string(node: ast.Call) -> str | None:
    """Extract a dotted string representation of a Call node's function."""
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return _get_attribute_string(node.func)
    return None


def _get_attribute_string(node: ast.Attribute) -> str | None:
    """Extract dotted string from an Attribute node."""
    parts: list[str] = [node.attr]
    current: ast.expr = node.value
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    parts.reverse()
    return ".".join(parts)


def _infer_module_path(handler_file: Path, node_dir: Path) -> str:
    """Infer the Python module path for a handler file.

    E.g., for handler at:
      .../src/omnibase_infra/nodes/node_foo/handlers/handler_bar.py
    Returns:
      omnibase_infra.nodes.node_foo.handlers.handler_bar
    """
    # Walk up to find 'src/' directory
    parts = handler_file.parts
    src_idx = None
    for i, part in enumerate(parts):
        if part == "src":
            src_idx = i
            break

    if src_idx is not None:
        module_parts = parts[src_idx + 1 :]
    else:
        # Fallback: use relative to node_dir parent
        try:
            rel = handler_file.relative_to(node_dir.parent.parent)
            module_parts = rel.parts
        except ValueError:
            return ""

    # Strip .py extension from last part
    last = module_parts[-1]
    if last.endswith(".py"):
        module_parts = (*module_parts[:-1], last[:-3])

    return ".".join(module_parts)
