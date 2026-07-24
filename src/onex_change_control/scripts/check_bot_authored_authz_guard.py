# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Fail-closed guard: reject BOT-authored PRs touching grants/** or allowlists/**.

OMN-14919.

Why this exists (the only real closure for the canonical OCC producer)
----------------------------------------------------------------------
Once OCC-companion minting is a canonical *machine* producer authenticating with
an App token, that writer can push to **any path in this repository** — App
tokens scope by REPO, not by PATH. Nothing else stops a machine writer from
minting its own authorization:

* OCC has **no required reviews** on ``dev``.
* ``.github/CODEOWNERS`` here is **advisory** — ``required_pull_request_reviews``
  is intentionally OFF on ``dev`` (OMN-14445), so CODEOWNERS does not block a
  merge.
* OMN-14441's ``approved_by != PR-author`` check on the prod-promotion grants
  file is **trivially satisfied by a bot carrying** ``approved_by: jonah`` — the
  bot is the PR author, ``jonah`` is a different login, so the inequality holds.

So a bot could open a PR that edits ``grants/**`` (the prod-promotion trust
anchor) or ``allowlists/**`` (compliance exemptions) and self-authorize. This
guard is the mechanical closure: it **rejects any bot-authored PR that touches**
``grants/**`` **or** ``allowlists/**``.

It deliberately does NOT block bots from minting *evidence* companions — those
touch only ``contracts/**`` and ``drift/**``. The line is exact: a machine may
write evidence; it may never write **authorization**.

Fail-closed
-----------
For a PR that touches a sensitive path, this guard passes ONLY when it can
positively prove the change is human-authored. Any bot signal -> reject (exit 1).
If authorship cannot be resolved at all (git history unreadable) -> INCONCLUSIVE
(exit 2), which the CI Summary treats as a hard failure. An unproven safety
property never passes.

Token posture
-------------
Uses the default ``GITHUB_TOKEN`` (or no token at all). It does **not** use, and
must not be given, the org-wide ``CROSS_REPO_PAT``: everything it needs — the
changed-file set and the commit author/committer identities — comes from the
locally-checked-out git history (``fetch-depth: 0``), and the PR-author facts
come from the ``pull_request`` event context. No org-private read is required.

Usage
-----
    # CI: derive changed files + commit identities from local git.
    uv run check-bot-authored-authz-guard \
        --base-ref "$BASE_REF" \
        --pr-author-login "$PR_AUTHOR_LOGIN" \
        --pr-author-type "$PR_AUTHOR_TYPE"

    # Explicit (tests / seeded proof): feed the exact tuple a bot PR produces.
    uv run check-bot-authored-authz-guard \
        --changed-file grants/prod_promotion_grants.yaml \
        --commit-identity "omnimarket-bot	bot@omninode.ai"

Exit codes
----------
    0: PASS   — no sensitive path touched, OR a sensitive path is touched but the
               change is proven human-authored.
    1: REJECT — a sensitive path is touched by a bot-authored change.
    2: INCONCLUSIVE — a sensitive path is touched but authorship could not be
               resolved. Fail-closed: blocks via CI Summary.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

# ---------------------------------------------------------------------------
# Policy constants — the two authorization surfaces a machine writer must never
# touch, and the machine identities we recognize. Both are intentionally module
# constants (greppable, extensible) rather than buried in logic.
# ---------------------------------------------------------------------------

# A changed file is "sensitive" when it lives under one of these repo-relative
# prefixes. Scoped EXACTLY to the two authorization surfaces named in the closure
# rationale — grants (prod-promotion trust anchor) and allowlists (compliance
# exemptions). Evidence surfaces (contracts/, drift/) are intentionally NOT here:
# machine-minted evidence companions are allowed.
SENSITIVE_PATH_PREFIXES: tuple[str, ...] = ("grants/", "allowlists/")

# Known non-human GitHub login handles (compared case-insensitively). Any login
# ending in "[bot]" is also treated as a bot regardless of this set.
KNOWN_BOT_LOGINS: frozenset[str] = frozenset(
    {
        "omnimarket-bot",
        "onexbot",
        "omninode-bot",
        "node-occ-companion-effect",
        "node-occ-companion-compute",
        "occ-companion-effect",
        "github-actions",
        "dependabot",
    }
)

# Known machine commit author/committer names (case-insensitive). These are the
# identities the OCC producers stamp onto companion commits (see
# occ_git_transport / handler_occ_companion_effect).
KNOWN_BOT_NAMES: frozenset[str] = frozenset(
    {
        "omnimarket-bot",
        "node-occ-companion-effect",
        "node_occ_companion_effect",
        "node-occ-companion-compute",
        "onexbot",
        "github-actions[bot]",
    }
)

# Known machine commit author/committer emails (case-insensitive).
KNOWN_BOT_EMAILS: frozenset[str] = frozenset(
    {
        "bot@omninode.ai",
        "occ-companion-effect@omninode.ai",
    }
)

_EXIT_PASS = 0
_EXIT_REJECT = 1
_EXIT_INCONCLUSIVE = 2

_GIT_TIMEOUT_SECONDS = 30
_MIN_IDENTITY_PARTS = 2


class _GitFactError(RuntimeError):
    """Raised when a required git fact (changed files / identities) is unreadable."""


# ---------------------------------------------------------------------------
# Pure identity predicates — no I/O, directly unit-testable.
# ---------------------------------------------------------------------------


def is_bot_login(login: str | None) -> bool:
    """Return True if ``login`` is a recognized non-human GitHub handle."""
    if not login:
        return False
    norm = login.strip().lower()
    if not norm:
        return False
    return norm.endswith("[bot]") or norm in KNOWN_BOT_LOGINS


def is_bot_author_type(author_type: str | None) -> bool:
    """Return True if the PR author's GitHub ``type`` field is ``Bot``."""
    return author_type is not None and author_type.strip().lower() == "bot"


def is_bot_identity(name: str, email: str) -> bool:
    """Return True if a commit author/committer (name, email) is a machine identity."""
    n = name.strip().lower()
    e = email.strip().lower()
    if n in KNOWN_BOT_NAMES or n.endswith("[bot]"):
        return True
    if e in KNOWN_BOT_EMAILS:
        return True
    # App-bot commits use "<id>+<app>[bot]@users.noreply.github.com".
    return "[bot]@" in e


def is_sensitive_path(path: str) -> bool:
    """Return True if ``path`` touches one of the guarded authorization surfaces."""
    p = path.strip().lstrip("./")
    return any(p.startswith(prefix) for prefix in SENSITIVE_PATH_PREFIXES)


# ---------------------------------------------------------------------------
# Pure decision logic — the load-bearing evaluation, isolated from I/O.
# ---------------------------------------------------------------------------


def _collect_bot_reasons(
    *,
    pr_author_login: str | None,
    pr_author_type: str | None,
    commit_identities: Sequence[tuple[str, str]] | None,
) -> list[str]:
    """Return the de-duplicated bot signals present in the candidate change."""
    reasons: list[str] = []
    if is_bot_author_type(pr_author_type):
        reasons.append(f"PR author GitHub type is {pr_author_type!r} (Bot)")
    if is_bot_login(pr_author_login):
        reasons.append(
            f"PR author login {pr_author_login!r} is a known/[bot] machine identity"
        )
    if commit_identities is not None:
        seen: set[str] = set()
        for name, email in commit_identities:
            if is_bot_identity(name, email):
                reason = f"commit by machine identity {name!r} <{email}>"
                if reason not in seen:
                    seen.add(reason)
                    reasons.append(reason)
    return reasons


def evaluate(
    *,
    changed_files: Sequence[str] | None,
    pr_author_login: str | None,
    pr_author_type: str | None,
    commit_identities: Sequence[tuple[str, str]] | None,
) -> tuple[int, str]:
    """Decide PASS / REJECT / INCONCLUSIVE for a candidate change.

    ``changed_files``     — repo-relative paths in the PR diff, or None if unresolved.
    ``pr_author_login``   — the ``pull_request.user.login`` (best-effort; may be None).
    ``pr_author_type``    — the ``pull_request.user.type`` (best-effort; may be None).
    ``commit_identities`` — (name, email) for every commit author AND committer in
                            the PR, or None if git history was unreadable. This is
                            the load-bearing input: if it is None and no author-side
                            bot signal is present, the result is INCONCLUSIVE.

    Returns ``(exit_code, message)``.
    """
    if changed_files is None:
        return (
            _EXIT_INCONCLUSIVE,
            "INCONCLUSIVE: could not resolve the PR's changed-file set from git; "
            "cannot decide whether a guarded authorization path was touched. "
            "Fail-closed (OMN-14919).",
        )

    sensitive = sorted({f for f in changed_files if is_sensitive_path(f)})
    if not sensitive:
        return (
            _EXIT_PASS,
            "PASS: PR touches no grants/** or allowlists/** path; the "
            "bot-authored-authz guard does not apply (OMN-14919).",
        )

    # A sensitive path is touched — from here we pass ONLY on proven human authorship.
    bot_reasons = _collect_bot_reasons(
        pr_author_login=pr_author_login,
        pr_author_type=pr_author_type,
        commit_identities=commit_identities,
    )
    touched = "\n".join(f"    - {p}" for p in sensitive)

    if bot_reasons:
        joined = "\n".join(f"    - {r}" for r in bot_reasons)
        return (
            _EXIT_REJECT,
            "REJECT: a BOT-authored change touches a guarded authorization surface "
            "(grants/** or allowlists/**). A machine writer must never mint its own "
            "authorization — App tokens scope by repo not path, OCC has no required "
            "reviews, and CODEOWNERS here is advisory (OMN-14919).\n"
            f"  Bot signal(s):\n{joined}\n"
            f"  Guarded path(s) touched:\n{touched}\n"
            "  If this is legitimate, a human must author it (human login + "
            "non-bot commit identities).",
        )

    if commit_identities is None:
        return (
            _EXIT_INCONCLUSIVE,
            "INCONCLUSIVE: the PR touches a guarded authorization surface "
            "(grants/** or allowlists/**) but the commit author/committer "
            "identities could not be read from git, so human authorship cannot be "
            "proven. Fail-closed (OMN-14919).\n"
            f"  Guarded path(s) touched:\n{touched}",
        )

    return (
        _EXIT_PASS,
        "PASS: PR touches a guarded authorization surface (grants/** or "
        "allowlists/**) but authorship is proven human (no bot login/type and no "
        "machine commit identities) (OMN-14919).\n"
        f"  Guarded path(s) touched:\n{touched}",
    )


# ---------------------------------------------------------------------------
# I/O — git derivation of the two facts the decision needs.
# ---------------------------------------------------------------------------


def _run_git(args: Sequence[str], repo_root: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603  Why: fixed argv, no shell.
        ["git", "-C", repo_root, *args],  # noqa: S607  Why: `git` from PATH, repo convention.
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_SECONDS,
        check=False,
    )


def resolve_changed_files(repo_root: str, base_ref: str) -> list[str]:
    """Return repo-relative paths changed on HEAD since its merge-base with base_ref.

    Raises :class:`_GitFactError` if git cannot produce the diff (so the caller
    fails closed rather than treating an unreadable diff as "nothing changed").
    """
    result = _run_git(
        ["diff", "--no-renames", "--name-only", f"origin/{base_ref}...HEAD"],
        repo_root,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or "unknown git error"
        msg = f"could not resolve changed files vs origin/{base_ref}: {detail}"
        raise _GitFactError(msg)
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def resolve_commit_identities(repo_root: str, base_ref: str) -> list[tuple[str, str]]:
    """Return (name, email) for every commit author AND committer since base_ref.

    Raises :class:`_GitFactError` if git cannot produce the log.
    """
    fmt = "%an%x09%ae%x00%cn%x09%ce"
    result = _run_git(
        ["log", f"origin/{base_ref}..HEAD", f"--format={fmt}"],
        repo_root,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or "unknown git error"
        msg = f"could not read commit identities vs origin/{base_ref}: {detail}"
        raise _GitFactError(msg)
    identities: list[tuple[str, str]] = []
    for record in result.stdout.split("\x00"):
        chunk = record.strip()
        if not chunk:
            continue
        parts = chunk.split("\t")
        if len(parts) >= _MIN_IDENTITY_PARTS:
            identities.append((parts[0].strip(), parts[1].strip()))
    return identities


_GIT_IDENT_RE = re.compile(r"^(?P<name>.*?)\s*<(?P<email>[^>]*)>")


def resolve_staged_changed_files(repo_root: str) -> list[str]:
    """Return repo-relative paths currently staged for commit (pre-commit mode).

    Raises :class:`_GitFactError` if git cannot produce the diff, so the
    caller fails closed (OMN-15008 — the pre-commit mirror of
    :func:`resolve_changed_files`, which is scoped to a merged PR's range).
    """
    result = _run_git(["diff", "--cached", "--no-renames", "--name-only"], repo_root)
    if result.returncode != 0:
        detail = result.stderr.strip() or "unknown git error"
        msg = f"could not resolve staged changed files: {detail}"
        raise _GitFactError(msg)
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def resolve_staged_commit_identities(repo_root: str) -> list[tuple[str, str]]:
    """Return the (name, email) pair git would stamp as author AND committer
    on the in-progress commit (pre-commit mode).

    Uses ``git var GIT_AUTHOR_IDENT`` / ``GIT_COMMITTER_IDENT``, which resolve
    the same identity git itself would use for the commit -- respecting
    ``GIT_AUTHOR_NAME``/``GIT_AUTHOR_EMAIL``/``GIT_COMMITTER_NAME``/
    ``GIT_COMMITTER_EMAIL`` env overrides, falling back to ``user.name``/
    ``user.email`` config. Raises :class:`_GitFactError` if either identity is
    unreadable or unparseable, so the caller fails closed.
    """
    identities: list[tuple[str, str]] = []
    for var in ("GIT_AUTHOR_IDENT", "GIT_COMMITTER_IDENT"):
        result = _run_git(["var", var], repo_root)
        if result.returncode != 0:
            detail = result.stderr.strip() or "unknown git error"
            msg = f"could not read {var}: {detail}"
            raise _GitFactError(msg)
        match = _GIT_IDENT_RE.match(result.stdout.strip())
        if match is None:
            msg = f"could not parse {var} output: {result.stdout!r}"
            raise _GitFactError(msg)
        identities.append((match.group("name").strip(), match.group("email").strip()))
    return identities


def _parse_identity_arg(raw: str) -> tuple[str, str]:
    """Parse a ``--commit-identity`` argument of the form ``name<TAB>email``."""
    if "\t" in raw:
        name, email = raw.split("\t", 1)
    elif "|" in raw:
        name, email = raw.split("|", 1)
    else:
        name, email = raw, ""
    return (name.strip(), email.strip())


def _gather_changed_files(args: argparse.Namespace) -> list[str] | None:
    if args.changed_files is not None:
        return list(args.changed_files)
    try:
        if args.staged:
            return resolve_staged_changed_files(args.repo_root)
        return resolve_changed_files(args.repo_root, args.base_ref)
    except (_GitFactError, OSError, subprocess.SubprocessError) as exc:
        print(f"git: {exc}", file=sys.stderr)
        return None


def _gather_commit_identities(args: argparse.Namespace) -> list[tuple[str, str]] | None:
    if args.commit_identities is not None:
        return [_parse_identity_arg(raw) for raw in args.commit_identities]
    try:
        if args.staged:
            return resolve_staged_commit_identities(args.repo_root)
        return resolve_commit_identities(args.repo_root, args.base_ref)
    except (_GitFactError, OSError, subprocess.SubprocessError) as exc:
        print(f"git: {exc}", file=sys.stderr)
        return None


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        default=".",
        help="Repository root to derive git facts from (default: cwd).",
    )
    parser.add_argument(
        "--base-ref",
        default=os.environ.get("BASE_REF", "dev"),
        help="Base branch the PR targets (default: $BASE_REF or 'dev').",
    )
    parser.add_argument(
        "--staged",
        action="store_true",
        default=False,
        help="Pre-commit mode (OMN-15008): derive changed files from "
        "`git diff --cached` and the identity from `git var "
        "GIT_AUTHOR_IDENT`/`GIT_COMMITTER_IDENT` (the in-progress commit's "
        "identity) instead of a merged PR's --base-ref range. Mutually "
        "exclusive in effect with --base-ref; no pr-author-login/type facts "
        "exist locally, so this mode relies solely on the commit-identity "
        "signal (weaker but real defense-in-depth vs the CI gate).",
    )
    parser.add_argument(
        "--changed-file",
        action="append",
        default=None,
        dest="changed_files",
        help="Explicit changed path (repeatable). Bypasses git derivation; for "
        "tests and the seeded-proof invocation.",
    )
    parser.add_argument(
        "--commit-identity",
        action="append",
        default=None,
        dest="commit_identities",
        help="Explicit commit identity 'name<TAB>email' (repeatable). Bypasses git "
        "derivation; for tests and the seeded-proof invocation.",
    )
    parser.add_argument(
        "--pr-author-login",
        default=os.environ.get("PR_AUTHOR_LOGIN") or None,
        help="pull_request.user.login (default: $PR_AUTHOR_LOGIN).",
    )
    parser.add_argument(
        "--pr-author-type",
        default=os.environ.get("PR_AUTHOR_TYPE") or None,
        help="pull_request.user.type, e.g. 'Bot' or 'User' (default: $PR_AUTHOR_TYPE).",
    )
    args = parser.parse_args(argv)

    changed_files = _gather_changed_files(args)
    commit_identities = _gather_commit_identities(args)

    exit_code, message = evaluate(
        changed_files=changed_files,
        pr_author_login=args.pr_author_login,
        pr_author_type=args.pr_author_type,
        commit_identities=commit_identities,
    )
    print(message, file=sys.stdout if exit_code == _EXIT_PASS else sys.stderr)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
