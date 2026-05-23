# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Wave-ordered GitHub dev/main cutover orchestration."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from onex_change_control.promotion.manifest import DEFAULT_PROMOTION_REPOS
from onex_change_control.promotion.workflow import PROMOTION_WAVES

SOURCE_BRANCH = "dev"
TARGET_BRANCH = "main"


class EnumCutoverAction(StrEnum):
    """Actions the cutover script plans or executes."""

    CREATE_DEV_BRANCH = "create_dev_branch"
    COPY_BRANCH_PROTECTION_TO_DEV = "copy_branch_protection_to_dev"
    SET_DEFAULT_BRANCH_TO_DEV = "set_default_branch_to_dev"
    RETARGET_PR_TO_DEV = "retarget_pr_to_dev"


class ModelCutoverPullRequest(BaseModel):
    """Open pull request that must move from main to dev."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    number: int = Field(ge=1)
    title: str
    url: str
    head_ref_name: str
    head_ref_oid: str
    base_ref_name_before: str = TARGET_BRANCH
    base_ref_name_after: str = SOURCE_BRANCH
    fresh_checks_required: bool = True
    auto_merge_rearm: str = "after_fresh_checks_pass"


class ModelCutoverActionRecord(BaseModel):
    """One planned or executed cutover mutation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    action: EnumCutoverAction
    status: str
    detail: str


class ModelCutoverRepoResult(BaseModel):
    """Per-repository cutover result in the manifest."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    repo: str
    wave: int = Field(ge=1)
    main_sha_before: str = Field(min_length=7)
    dev_sha_before: str | None = None
    dev_sha_after: str = Field(min_length=7)
    default_branch_before: str
    default_branch_after: str
    protection_rule_hash_before: str
    protection_rule_hash_after: str
    open_prs_retargeted: int = Field(ge=0)
    retargeted_prs: tuple[ModelCutoverPullRequest, ...] = Field(default_factory=tuple)
    actions: tuple[ModelCutoverActionRecord, ...] = Field(default_factory=tuple)


class ModelCutoverManifest(BaseModel):
    """Cutover manifest emitted for durable evidence."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = "1.0.0"
    cutover_id: str = Field(min_length=1)
    generated_at: datetime
    dry_run: bool
    owner: str
    source_branch: str = SOURCE_BRANCH
    target_branch: str = TARGET_BRANCH
    selected_waves: tuple[int, ...]
    repos: tuple[ModelCutoverRepoResult, ...]
    stop_point_required: bool = True

    @model_validator(mode="after")
    def _validate_manifest(self) -> ModelCutoverManifest:
        repo_names = [entry.repo for entry in self.repos]
        if len(repo_names) != len(set(repo_names)):
            msg = "cutover manifest contains duplicate repos"
            raise ValueError(msg)
        if self.source_branch == self.target_branch:
            msg = "cutover source and target branches must differ"
            raise ValueError(msg)
        return self

    @property
    def manifest_sha256(self) -> str:
        """Return canonical SHA-256 digest for this manifest."""
        payload = self.model_dump(mode="json", exclude={"generated_at"})
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return f"sha256:{hashlib.sha256(encoded).hexdigest()}"

    def to_json_bytes(self) -> bytes:
        """Serialize the manifest with its digest."""
        payload = self.model_dump(mode="json")
        payload["manifest_sha256"] = self.manifest_sha256
        return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()


class GitHubClient(Protocol):
    """GitHub operations required by the cutover orchestrator."""

    def repo_default_branch(self, repo: str) -> str: ...

    def branch_sha(self, repo: str, branch: str) -> str | None: ...

    def create_branch(self, repo: str, branch: str, sha: str) -> None: ...

    def get_branch_protection(self, repo: str, branch: str) -> dict[str, Any]: ...

    def put_branch_protection(
        self, repo: str, branch: str, payload: dict[str, Any]
    ) -> None: ...

    def set_default_branch(self, repo: str, branch: str) -> None: ...

    def list_open_prs(self, repo: str, base: str) -> list[dict[str, Any]]: ...

    def retarget_pr(self, repo: str, number: int, base: str) -> None: ...


class GhCliClient:
    """GitHub client backed by the `gh` CLI."""

    def __init__(self, *, owner: str) -> None:
        self._owner = owner

    def repo_default_branch(self, repo: str) -> str:
        payload = self._required_api_json(f"repos/{self._owner}/{repo}")
        return str(payload["default_branch"])

    def branch_sha(self, repo: str, branch: str) -> str | None:
        payload = self._api_json(
            f"repos/{self._owner}/{repo}/git/ref/heads/{branch}",
            allow_not_found=True,
        )
        if payload is None:
            return None
        obj = payload.get("object", {})
        return str(obj["sha"])

    def create_branch(self, repo: str, branch: str, sha: str) -> None:
        self._api_json(
            f"repos/{self._owner}/{repo}/git/refs",
            method="POST",
            input_payload={"ref": f"refs/heads/{branch}", "sha": sha},
        )

    def get_branch_protection(self, repo: str, branch: str) -> dict[str, Any]:
        return self._required_api_json(
            f"repos/{self._owner}/{repo}/branches/{branch}/protection"
        )

    def put_branch_protection(
        self, repo: str, branch: str, payload: dict[str, Any]
    ) -> None:
        self._api_json(
            f"repos/{self._owner}/{repo}/branches/{branch}/protection",
            method="PUT",
            input_payload=payload,
        )

    def set_default_branch(self, repo: str, branch: str) -> None:
        self._api_json(
            f"repos/{self._owner}/{repo}",
            method="PATCH",
            input_payload={"default_branch": branch},
        )

    def list_open_prs(self, repo: str, base: str) -> list[dict[str, Any]]:
        completed = subprocess.run(  # noqa: S603
            [
                _gh_bin(),
                "pr",
                "list",
                "--repo",
                f"{self._owner}/{repo}",
                "--state",
                "open",
                "--base",
                base,
                "--json",
                "number,title,url,headRefName,headRefOid,baseRefName",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        payload = json.loads(completed.stdout)
        if not isinstance(payload, list):
            msg = f"unexpected gh pr list payload for {repo}: {payload!r}"
            raise TypeError(msg)
        return [dict(item) for item in payload]

    def retarget_pr(self, repo: str, number: int, base: str) -> None:
        subprocess.run(  # noqa: S603
            [
                _gh_bin(),
                "pr",
                "edit",
                str(number),
                "--repo",
                f"{self._owner}/{repo}",
                "--base",
                base,
            ],
            check=True,
            capture_output=True,
            text=True,
        )

    def _api_json(
        self,
        endpoint: str,
        *,
        method: str = "GET",
        input_payload: dict[str, Any] | None = None,
        allow_not_found: bool = False,
    ) -> dict[str, Any] | None:
        command = [_gh_bin(), "api", "--method", method, endpoint]
        input_text: str | None = None
        if input_payload is not None:
            command.extend(["--input", "-"])
            input_text = json.dumps(input_payload)
        completed = subprocess.run(  # noqa: S603
            command,
            check=False,
            capture_output=True,
            input=input_text,
            text=True,
        )
        if completed.returncode != 0:
            not_found = (
                "Not Found" in completed.stderr or "HTTP 404" in completed.stderr
            )
            if allow_not_found and not_found:
                return None
            completed.check_returncode()
        if not completed.stdout.strip():
            return {}
        payload = json.loads(completed.stdout)
        if not isinstance(payload, dict):
            msg = f"unexpected gh api payload for {endpoint}: {payload!r}"
            raise TypeError(msg)
        return payload

    def _required_api_json(
        self,
        endpoint: str,
        *,
        method: str = "GET",
        input_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = self._api_json(
            endpoint,
            method=method,
            input_payload=input_payload,
        )
        if payload is None:
            msg = f"required gh api payload missing for {endpoint}"
            raise RuntimeError(msg)
        return payload


def normalize_branch_protection_for_put(protection: dict[str, Any]) -> dict[str, Any]:
    """Convert a branch protection GET response into a PUT payload."""
    status_checks = protection.get("required_status_checks")
    contexts: set[str] = set()
    strict = True
    if isinstance(status_checks, dict):
        strict = bool(status_checks.get("strict", True))
        raw_contexts = status_checks.get("contexts", [])
        if isinstance(raw_contexts, list):
            contexts.update(str(item) for item in raw_contexts)
        raw_checks = status_checks.get("checks", [])
        if isinstance(raw_checks, list):
            contexts.update(
                str(item["context"])
                for item in raw_checks
                if isinstance(item, dict) and item.get("context")
            )

    return {
        "required_status_checks": {
            "strict": strict,
            "contexts": sorted(contexts),
        },
        "enforce_admins": bool(
            _nested_bool(protection, ("enforce_admins", "enabled"), default=True)
        ),
        "required_pull_request_reviews": _pull_request_review_payload(
            protection.get("required_pull_request_reviews")
        ),
        "restrictions": _restrictions_payload(protection.get("restrictions")),
        "required_linear_history": bool(
            _nested_bool(
                protection,
                ("required_linear_history", "enabled"),
                default=False,
            )
        ),
        "allow_force_pushes": bool(
            _nested_bool(protection, ("allow_force_pushes", "enabled"), default=False)
        ),
        "allow_deletions": bool(
            _nested_bool(protection, ("allow_deletions", "enabled"), default=False)
        ),
        "required_conversation_resolution": bool(
            _nested_bool(
                protection,
                ("required_conversation_resolution", "enabled"),
                default=False,
            )
        ),
    }


def execute_cutover(  # noqa: PLR0913
    *,
    client: GitHubClient,
    owner: str,
    cutover_id: str,
    selected_waves: tuple[int, ...],
    dry_run: bool,
    repos: tuple[str, ...] = DEFAULT_PROMOTION_REPOS,
    generated_at: datetime | None = None,
) -> ModelCutoverManifest:
    """Plan or execute the selected cutover waves."""
    repo_to_wave = _repo_wave_map()
    requested_repos = tuple(
        repo for repo in repos if repo_to_wave[repo] in selected_waves
    )
    results = tuple(
        _cutover_repo(
            client=client,
            repo=repo,
            wave=repo_to_wave[repo],
            dry_run=dry_run,
        )
        for repo in requested_repos
    )
    return ModelCutoverManifest(
        cutover_id=cutover_id,
        generated_at=generated_at or datetime.now(UTC),
        dry_run=dry_run,
        owner=owner,
        selected_waves=selected_waves,
        repos=results,
    )


def _cutover_repo(
    *,
    client: GitHubClient,
    repo: str,
    wave: int,
    dry_run: bool,
) -> ModelCutoverRepoResult:
    actions: list[ModelCutoverActionRecord] = []
    main_sha = _required_branch_sha(client, repo, TARGET_BRANCH)
    dev_sha_before = client.branch_sha(repo, SOURCE_BRANCH)
    if dev_sha_before is None:
        actions.append(
            _action(
                EnumCutoverAction.CREATE_DEV_BRANCH,
                dry_run=dry_run,
                detail=f"create {SOURCE_BRANCH} at {main_sha}",
            )
        )
        if not dry_run:
            client.create_branch(repo, SOURCE_BRANCH, main_sha)
        dev_sha_after = main_sha
    else:
        dev_sha_after = dev_sha_before

    default_before = client.repo_default_branch(repo)
    main_protection = client.get_branch_protection(repo, TARGET_BRANCH)
    protection_hash_before = stable_json_sha256(main_protection)
    protection_payload = normalize_branch_protection_for_put(main_protection)
    actions.append(
        _action(
            EnumCutoverAction.COPY_BRANCH_PROTECTION_TO_DEV,
            dry_run=dry_run,
            detail=f"copy {TARGET_BRANCH} protection to {SOURCE_BRANCH}",
        )
    )
    if not dry_run:
        client.put_branch_protection(repo, SOURCE_BRANCH, protection_payload)

    actions.append(
        _action(
            EnumCutoverAction.SET_DEFAULT_BRANCH_TO_DEV,
            dry_run=dry_run,
            detail=f"set default branch to {SOURCE_BRANCH}",
        )
    )
    if not dry_run and default_before != SOURCE_BRANCH:
        client.set_default_branch(repo, SOURCE_BRANCH)

    prs = tuple(_model_pr(item) for item in client.list_open_prs(repo, TARGET_BRANCH))
    for pr in prs:
        actions.append(
            _action(
                EnumCutoverAction.RETARGET_PR_TO_DEV,
                dry_run=dry_run,
                detail=f"retarget PR #{pr.number} to {SOURCE_BRANCH}",
            )
        )
        if not dry_run:
            client.retarget_pr(repo, pr.number, SOURCE_BRANCH)

    dev_protection = (
        main_protection
        if dry_run
        else client.get_branch_protection(repo, SOURCE_BRANCH)
    )
    return ModelCutoverRepoResult(
        repo=repo,
        wave=wave,
        main_sha_before=main_sha,
        dev_sha_before=dev_sha_before,
        dev_sha_after=dev_sha_after,
        default_branch_before=default_before,
        default_branch_after=SOURCE_BRANCH,
        protection_rule_hash_before=protection_hash_before,
        protection_rule_hash_after=stable_json_sha256(dev_protection),
        open_prs_retargeted=len(prs),
        retargeted_prs=prs,
        actions=tuple(actions),
    )


def stable_json_sha256(payload: dict[str, Any]) -> str:
    """Return stable SHA-256 for a JSON object."""
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def selected_waves_from_args(
    *, wave: int | None, all_waves: bool, execute: bool
) -> tuple[int, ...]:
    """Return selected waves, enforcing stop points for live execution."""
    wave_numbers = tuple(range(1, len(PROMOTION_WAVES) + 1))
    if wave is not None and all_waves:
        msg = "--wave and --all-waves are mutually exclusive"
        raise ValueError(msg)
    if execute and wave is None:
        msg = "--execute requires --wave N so each wave has an explicit stop point"
        raise ValueError(msg)
    if all_waves:
        return wave_numbers
    if wave is not None:
        if wave not in wave_numbers:
            msg = f"wave must be one of {wave_numbers}"
            raise ValueError(msg)
        return (wave,)
    return wave_numbers


def _model_pr(payload: dict[str, Any]) -> ModelCutoverPullRequest:
    return ModelCutoverPullRequest(
        number=int(payload["number"]),
        title=str(payload["title"]),
        url=str(payload["url"]),
        head_ref_name=str(payload["headRefName"]),
        head_ref_oid=str(payload["headRefOid"]),
        base_ref_name_before=str(payload.get("baseRefName") or TARGET_BRANCH),
    )


def _required_branch_sha(client: GitHubClient, repo: str, branch: str) -> str:
    sha = client.branch_sha(repo, branch)
    if sha is None:
        msg = f"{repo} is missing required branch {branch}"
        raise RuntimeError(msg)
    return sha


def _action(
    action: EnumCutoverAction,
    *,
    dry_run: bool,
    detail: str,
) -> ModelCutoverActionRecord:
    return ModelCutoverActionRecord(
        action=action,
        status="planned" if dry_run else "executed",
        detail=detail,
    )


def _repo_wave_map() -> dict[str, int]:
    return {
        repo: index
        for index, wave in enumerate(PROMOTION_WAVES, start=1)
        for repo in wave
    }


def _gh_bin() -> str:
    path = shutil.which("gh")
    if path is None:
        msg = "gh CLI is required for live cutover operations"
        raise RuntimeError(msg)
    return path


def _nested_bool(
    payload: dict[str, Any],
    path: tuple[str, str],
    *,
    default: bool,
) -> bool:
    current: Any = payload
    for key in path:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
    return current if isinstance(current, bool) else default


def _pull_request_review_payload(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return {
        "dismiss_stale_reviews": bool(value.get("dismiss_stale_reviews", False)),
        "require_code_owner_reviews": bool(
            value.get("require_code_owner_reviews", False)
        ),
        "required_approving_review_count": int(
            value.get("required_approving_review_count", 0)
        ),
        "require_last_push_approval": bool(
            value.get("require_last_push_approval", False)
        ),
        "bypass_pull_request_allowances": _review_bypass_payload(
            value.get("bypass_pull_request_allowances")
        ),
        "dismissal_restrictions": _restrictions_payload(
            value.get("dismissal_restrictions")
        ),
    }


def _restrictions_payload(value: object) -> dict[str, list[str]] | None:
    if not isinstance(value, dict):
        return None
    return {
        "users": _restriction_logins(value.get("users")),
        "teams": _restriction_slugs(value.get("teams")),
        "apps": _restriction_slugs(value.get("apps")),
    }


def _review_bypass_payload(value: object) -> dict[str, list[str]]:
    return _restrictions_payload(value) or {"users": [], "teams": [], "apps": []}


def _restriction_logins(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return sorted(
        str(item["login"])
        for item in value
        if isinstance(item, dict) and item.get("login")
    )


def _restriction_slugs(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return sorted(
        str(item["slug"])
        for item in value
        if isinstance(item, dict) and item.get("slug")
    )


def _default_cutover_id(now: datetime | None = None) -> str:
    return f"cutover-{(now or datetime.now(UTC)).date().isoformat()}"


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner", default="OmniNode-ai")
    parser.add_argument("--cutover-id", default=_default_cutover_id())
    parser.add_argument("--output", type=Path, default=Path("cutover_manifest.json"))
    parser.add_argument("--wave", type=int, choices=range(1, len(PROMOTION_WAVES) + 1))
    parser.add_argument(
        "--all-waves",
        action="store_true",
        help="Plan all waves in dry-run mode. Live execution still requires --wave.",
    )
    parser.add_argument("--execute", action="store_true")
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Required with --execute because this mutates GitHub branch settings.",
    )
    parser.add_argument(
        "--repos",
        default=",".join(DEFAULT_PROMOTION_REPOS),
        help="Comma-separated subset, preserving the canonical wave order.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    args = _parse_args(argv or sys.argv[1:])
    if args.execute and not args.yes:
        sys.stderr.write("--execute requires --yes\n")
        return 2
    try:
        selected_waves = selected_waves_from_args(
            wave=args.wave,
            all_waves=bool(args.all_waves),
            execute=bool(args.execute),
        )
    except ValueError as err:
        sys.stderr.write(f"{err}\n")
        return 2

    repos = tuple(item.strip() for item in str(args.repos).split(",") if item.strip())
    unknown = sorted(set(repos) - set(DEFAULT_PROMOTION_REPOS))
    if unknown:
        sys.stderr.write(f"unknown repos: {', '.join(unknown)}\n")
        return 2

    client = GhCliClient(owner=str(args.owner))
    manifest = execute_cutover(
        client=client,
        owner=str(args.owner),
        cutover_id=str(args.cutover_id),
        selected_waves=selected_waves,
        dry_run=not bool(args.execute),
        repos=repos,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(manifest.to_json_bytes())
    mode = "dry-run" if manifest.dry_run else "executed"
    sys.stdout.write(
        f"{mode} cutover manifest written to {output} ({manifest.manifest_sha256})\n"
    )
    if args.execute and len(selected_waves) == 1:
        sys.stdout.write(
            f"Stop point reached after wave {selected_waves[0]}; "
            "verify before continuing.\n"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
