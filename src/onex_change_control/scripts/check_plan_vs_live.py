# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Strict v1 plan-vs-live checker for planning documents.

OMN-12694 v1 verifies three mechanical claim classes in changed
``docs/plans/**`` files:

* GitHub PR citations are resolvable and not closed-unmerged.
* File path citations exist on the target branch or workspace checkout.
* Explicit Linear ticket-state claims match live state when a verifier is
  configured. Without a Linear verifier, ticket claims are reported as skipped
  unless ``--require-linear`` is set.

Live-path authority classification is intentionally deferred to v2.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from onex_change_control.enums.enum_doc_reference_type import EnumDocReferenceType
from onex_change_control.integrations import contract_descriptor
from onex_change_control.scanners.doc_reference_extractor import extract_all_references

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from onex_change_control.models.model_doc_reference import ModelDocReference

_KNOWN_REPOS = {
    "occ": "onex_change_control",
    "onex_change_control": "onex_change_control",
    "omnimarket": "omnimarket",
    "omniclaude": "omniclaude",
    "omnibase_core": "omnibase_core",
    "omnibase-core": "omnibase_core",
    "omnidash": "omnidash",
    "sea": "onex-self-extending-agent",
    "onex-self-extending-agent": "onex-self-extending-agent",
}

_PATH_TOKEN_STRIP = "`\"'.,);:]}"  # noqa: S105  Why: punctuation trim set.
# Linear GraphQL endpoint resolves from the integration contract + overlay
# (OMN-13563) — never a hardcoded URL literal.


@dataclass(frozen=True)
class Finding:
    """A plan-vs-live verification finding."""

    path: str
    line: int
    reference_type: str
    raw_text: str
    status: str
    message: str
    resolved_target: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Serialize for the JSON report."""
        return {
            "path": self.path,
            "line": self.line,
            "reference_type": self.reference_type,
            "raw_text": self.raw_text,
            "status": self.status,
            "message": self.message,
            "resolved_target": self.resolved_target,
        }


def _run(
    args: Sequence[str], cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603  Why: command args are fixed by verifier.
        list(args),
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )


def _normalize_repo(raw_repo: str | None, default_repo: str | None) -> str | None:
    if raw_repo:
        repo = raw_repo.strip().strip("`")
        if "/" in repo:
            return repo
        return f"OmniNode-ai/{_KNOWN_REPOS.get(repo.lower(), repo)}"
    if default_repo:
        return default_repo if "/" in default_repo else f"OmniNode-ai/{default_repo}"
    return None


def _split_pr_reference(raw: str) -> tuple[str | None, int]:
    raw_repo, number = raw.rsplit("#", 1)
    repo = raw_repo.strip() or None
    return repo, int(number)


def verify_pr_reference(ref: ModelDocReference, default_repo: str | None) -> Finding:
    """Verify a PR citation via ``gh pr view``."""
    raw_repo, number = _split_pr_reference(ref.raw_text)
    repo = _normalize_repo(raw_repo, default_repo)
    if repo is None:
        return Finding(
            path=ref.doc_path,
            line=ref.line_number,
            reference_type=ref.reference_type.value,
            raw_text=ref.raw_text,
            status="fail",
            message="bare PR citation has no repo qualifier and no --default-pr-repo",
        )

    result = _run(
        [
            "gh",
            "pr",
            "view",
            str(number),
            "--repo",
            repo,
            "--json",
            "state,mergedAt,url,headRefOid",
        ]
    )
    if result.returncode != 0:
        return Finding(
            path=ref.doc_path,
            line=ref.line_number,
            reference_type=ref.reference_type.value,
            raw_text=ref.raw_text,
            status="fail",
            message=result.stderr.strip() or f"could not resolve {repo}#{number}",
            resolved_target=f"{repo}#{number}",
        )

    data = json.loads(result.stdout)
    state = str(data.get("state") or "")
    merged_at = data.get("mergedAt")
    if state == "CLOSED" and not merged_at:
        return Finding(
            path=ref.doc_path,
            line=ref.line_number,
            reference_type=ref.reference_type.value,
            raw_text=ref.raw_text,
            status="fail",
            message=f"{repo}#{number} is closed unmerged",
            resolved_target=data.get("url") or f"{repo}#{number}",
        )

    return Finding(
        path=ref.doc_path,
        line=ref.line_number,
        reference_type=ref.reference_type.value,
        raw_text=ref.raw_text,
        status="pass",
        message=f"{repo}#{number} is {state.lower() or 'resolved'}",
        resolved_target=data.get("url") or f"{repo}#{number}",
    )


def _git_path_exists(repo_root: Path, base_ref: str, path: str) -> bool:
    result = _run(["git", "cat-file", "-e", f"{base_ref}:{path}"], cwd=repo_root)
    return result.returncode == 0


def _candidate_repo_and_path(
    raw: str, workspace_root: Path, current_repo_root: Path
) -> tuple[Path, str] | None:
    cleaned = raw.strip(_PATH_TOKEN_STRIP)
    first, _, rest = cleaned.partition("/")
    if rest:
        repo_name = _KNOWN_REPOS.get(first.lower(), first)
        candidate_repo = workspace_root / repo_name
        if (candidate_repo / ".git").exists() or (candidate_repo / ".git").is_file():
            return candidate_repo, rest
    return current_repo_root, cleaned


def verify_file_reference(
    ref: ModelDocReference,
    *,
    workspace_root: Path,
    current_repo_root: Path,
    base_ref: str | None,
) -> Finding:
    """Verify a file path against a target git ref or checked-out workspace."""
    candidate = _candidate_repo_and_path(
        ref.raw_text, workspace_root, current_repo_root
    )
    if candidate is None:
        exists = False
        target = None
    else:
        repo_root, rel_path = candidate
        target = str(repo_root / rel_path)
        if base_ref:
            exists = _git_path_exists(repo_root, base_ref, rel_path)
        else:
            exists = (repo_root / rel_path).exists()

    return Finding(
        path=ref.doc_path,
        line=ref.line_number,
        reference_type=ref.reference_type.value,
        raw_text=ref.raw_text,
        status="pass" if exists else "fail",
        message="path exists" if exists else "path missing on target branch",
        resolved_target=target,
    )


def _load_ticket_states(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        msg = "ticket state file must be a JSON object"
        raise TypeError(msg)
    return {str(k).upper(): str(v) for k, v in data.items()}


def _fetch_linear_state(ticket_id: str, token: str) -> str | None:
    query = {
        "query": ("query($id:String!){issue(id:$id){state{name}}}"),
        "variables": {"id": ticket_id},
    }
    request = urllib.request.Request(  # noqa: S310  Why: URL resolves from the contract.
        contract_descriptor.linear_graphql_url(),
        data=json.dumps(query).encode("utf-8"),
        headers={
            "Authorization": token,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310
            body = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return None
    return (((body.get("data") or {}).get("issue") or {}).get("state") or {}).get(
        "name"
    )


def _parse_ticket_claim(raw: str) -> tuple[str, str]:
    ticket, expected = raw.split(":", 1)
    return ticket.upper(), expected


def verify_ticket_state_reference(
    ref: ModelDocReference,
    *,
    ticket_states: Mapping[str, str],
    require_linear: bool,
) -> Finding:
    """Verify an explicit ticket-state claim."""
    ticket_id, expected = _parse_ticket_claim(ref.raw_text)
    actual = ticket_states.get(ticket_id)
    # Linear API key resolves from the contract-declared secret ref (OMN-13563);
    # optional here (live lookup is best-effort), so required=False.
    linear_token = contract_descriptor.linear_api_key(required=False)
    if actual is None and linear_token:
        actual = _fetch_linear_state(ticket_id, linear_token)

    if actual is None:
        return Finding(
            path=ref.doc_path,
            line=ref.line_number,
            reference_type=ref.reference_type.value,
            raw_text=ref.raw_text,
            status="fail" if require_linear else "skip",
            message=(
                "Linear state unavailable"
                if require_linear
                else "Linear state unavailable; claim skipped"
            ),
            resolved_target=ticket_id,
        )

    ok = actual.lower() == expected.lower()
    return Finding(
        path=ref.doc_path,
        line=ref.line_number,
        reference_type=ref.reference_type.value,
        raw_text=ref.raw_text,
        status="pass" if ok else "fail",
        message=f"expected {expected}, live state is {actual}",
        resolved_target=ticket_id,
    )


def evaluate_plan_vs_live(  # noqa: PLR0913  Why: CLI options map directly to checks.
    *,
    plan_paths: Sequence[Path],
    workspace_root: Path,
    current_repo_root: Path,
    base_ref: str | None,
    default_pr_repo: str | None,
    ticket_states: Mapping[str, str],
    require_linear: bool,
) -> dict[str, Any]:
    """Evaluate plan files and return a JSON-serializable report."""
    findings: list[Finding] = []
    for plan_path in plan_paths:
        for ref in extract_all_references(plan_path):
            if ref.reference_type == EnumDocReferenceType.PR_NUMBER:
                findings.append(verify_pr_reference(ref, default_pr_repo))
            elif ref.reference_type == EnumDocReferenceType.FILE_PATH:
                findings.append(
                    verify_file_reference(
                        ref,
                        workspace_root=workspace_root,
                        current_repo_root=current_repo_root,
                        base_ref=base_ref,
                    )
                )
            elif ref.reference_type == EnumDocReferenceType.TICKET_STATE:
                findings.append(
                    verify_ticket_state_reference(
                        ref,
                        ticket_states=ticket_states,
                        require_linear=require_linear,
                    )
                )

    failures = [finding for finding in findings if finding.status == "fail"]
    skipped = [finding for finding in findings if finding.status == "skip"]
    return {
        "status": "fail" if failures else "pass",
        "total_references": len(findings),
        "failed_count": len(failures),
        "skipped_count": len(skipped),
        "failures": [finding.as_dict() for finding in failures],
        "skipped": [finding.as_dict() for finding in skipped],
        "findings": [finding.as_dict() for finding in findings],
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate plan references against live PR/path/ticket state.",
    )
    parser.add_argument("plan_files", nargs="+", help="Plan markdown files to check.")
    parser.add_argument(
        "--workspace-root",
        default=".",
        help=(
            "Workspace root for repo-qualified paths, e.g. /Users/jonah/Code/omni_home."
        ),
    )
    parser.add_argument(
        "--current-repo-root",
        default=".",
        help="Repo root used for unqualified file paths.",
    )
    parser.add_argument(
        "--base-ref",
        default="origin/dev",
        help=(
            "Git ref used for target-branch file checks. "
            "Use empty string for local filesystem checks."
        ),
    )
    parser.add_argument(
        "--default-pr-repo",
        default=None,
        help="Repo for bare #123 PR references, e.g. OmniNode-ai/omnimarket.",
    )
    parser.add_argument(
        "--ticket-state-file",
        type=Path,
        default=None,
        help=(
            "Optional JSON object mapping OMN ticket ids to expected live states "
            "for tests/offline runs."
        ),
    )
    parser.add_argument(
        "--require-linear",
        action="store_true",
        help="Fail explicit ticket-state claims when Linear state cannot be verified.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point."""
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    ticket_states = _load_ticket_states(args.ticket_state_file)
    base_ref = args.base_ref.strip() or None
    report = evaluate_plan_vs_live(
        plan_paths=[Path(path) for path in args.plan_files],
        workspace_root=Path(args.workspace_root),
        current_repo_root=Path(args.current_repo_root),
        base_ref=base_ref,
        default_pr_repo=args.default_pr_repo,
        ticket_states=ticket_states,
        require_linear=args.require_linear,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 1 if report["status"] == "fail" else 0


if __name__ == "__main__":
    raise SystemExit(main())
