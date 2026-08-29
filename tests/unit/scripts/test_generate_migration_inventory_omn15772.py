# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""OMN-15772 structural fix, option (a): DERIVE the migration inventory.

Fail-closed cloning (onex_change_control#6635) made the ``migration-inventory``
gate *honest* -- it can no longer under-report because a peer clone silently
half-landed. It did not stop the drift, because the inventory itself was still
a hand-maintained dual write: every migration added to a peer repo had to be
transcribed into ``migration_inventory.yaml`` by a human who remembered to. A
fail-closed gate over a manual dual write does not prevent divergence, it only
complains sooner -- and it converts continuous peer drift into a continuous
stream of blocked PRs in an unrelated repo.

Option (a), recorded on the ticket as the chosen direction: the migration file
lists stop being authored data and become a BUILD PRODUCT derived from each
peer's declared branch via ``git ls-tree``. The gate's only question becomes
"does the committed artifact equal what regeneration produces?", which is
answerable mechanically and is blind to provenance -- so it also catches drift
from force-landed commits, direct-to-branch pushes, and bot-authored changes
that never ran the peer repo's own CI, which writer-side enforcement (option
(b)) cannot see by construction.

Scope split proven below:

* ``migration_inventory_sources.yaml`` -- HAND-MAINTAINED declared config
  (which repo, which branch, which directory, runner/tracking metadata). Not
  derivable from a tree.
* ``migration_inventory.yaml`` -- GENERATED. File lists derived from the tree.
* ``migration_table_annotations.yaml`` -- OPTIONAL hand annotations for
  ``tables:``, which are NOT reliably derivable from SQL text (measured: a
  comment-stripped CREATE/ALTER/INSERT/UPDATE regex disagreed with 30 of the
  hand-authored entries, losing the target of index-only migrations and adding
  migration-bookkeeping tables). A missing annotation is not an error.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from onex_change_control.scripts import generate_migration_inventory as gen

_REPO_ROOT = Path(__file__).resolve().parents[3]
_BOUNDARIES = _REPO_ROOT / "src" / "onex_change_control" / "boundaries"
# A path that deliberately does not exist: "no annotation layer".
_EMPTY_ANNOTATIONS = Path(__file__).with_name("__no_such_annotations__.yaml")


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def _make_peer(root: Path, name: str, files: dict[str, str], branch: str) -> Path:
    """Create a peer repo with ``files`` committed on ``branch``."""
    repo = root / name
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", branch)
    _git(repo, "config", "user.email", "ci@onex.test")
    _git(repo, "config", "user.name", "onex ci")
    for rel, body in files.items():
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "seed")
    return repo


def _run(args: list[str], annotations: Path | None = None) -> int:
    """Invoke the generator with an explicit annotations file.

    Fixture-driven tests must never fall through to the repo's real
    annotation layer -- its keys target the real peer sets, which a synthetic
    repos-root does not contain, so every one of them would surface as a stale
    annotation and mask what the test is actually asserting.
    """
    if annotations is None:
        annotations = _EMPTY_ANNOTATIONS
    return gen.main([*args, "--annotations", str(annotations)])


@pytest.fixture
def fixture_root(tmp_path: Path) -> Path:
    """A repos-root with one peer carrying two .sql files and one non-.sql."""
    root = tmp_path / "repos"
    root.mkdir()
    _make_peer(
        root,
        "peer_one",
        {
            "db/migrations/001_a.sql": "CREATE TABLE a();",
            "db/migrations/002_b.sql": "CREATE TABLE b();",
            "db/migrations/000_bootstrap.sh": "#!/bin/sh\n",
            "db/migrations/README.md": "notes\n",
        },
        "dev",
    )
    return root


@pytest.fixture
def fixture_sources(tmp_path: Path) -> Path:
    path = tmp_path / "sources.yaml"
    path.write_text(
        "---\n"
        'version: "1"\n'
        "databases:\n"
        "  demo:\n"
        "    connection_env: DEMO_DB_URL\n"
        "    migration_sets:\n"
        "      - source_repo: peer_one\n"
        "        branch: dev\n"
        "        directory: db/migrations\n"
        "        runner: k8s migration job\n",
        encoding="utf-8",
    )
    return path


# ---------------------------------------------------------------------------
# Derivation
# ---------------------------------------------------------------------------


def test_derives_only_sql_files_sorted(
    tmp_path: Path, fixture_root: Path, fixture_sources: Path
) -> None:
    """The derived list is exactly the ``*.sql`` blobs in the tree, sorted.

    ``.sh`` bootstrap scripts and READMEs living in the same directory are not
    migrations and must not enter the artifact -- this matches the disk-scan
    semantics the validator has always enforced (``glob("*.sql")``).
    """
    out = tmp_path / "inventory.yaml"
    rc = _run(
        [
            "--repos-root",
            str(fixture_root),
            "--sources",
            str(fixture_sources),
            "--inventory",
            str(out),
            "--write",
        ]
    )
    assert rc == 0
    data = yaml.safe_load(out.read_text(encoding="utf-8"))
    mset = data["databases"]["demo"]["migration_sets"][0]
    assert [e["file"] for e in mset["migrations"]] == ["001_a.sql", "002_b.sql"]


def test_derivation_reads_the_git_tree_not_the_working_tree(
    tmp_path: Path, fixture_root: Path, fixture_sources: Path
) -> None:
    """An uncommitted file on disk must not reach the artifact.

    The OMN-15771 reconciliation produced a false MISSING_FILE purely because a
    canonical clone was 3 commits behind ``origin/dev``. Ground truth is the
    declared branch's tree, so a working-tree read is not acceptable.
    """
    (
        fixture_root / "peer_one" / "db" / "migrations" / "999_uncommitted.sql"
    ).write_text("CREATE TABLE zzz();", encoding="utf-8")
    out = tmp_path / "inventory.yaml"
    assert (
        _run(
            [
                "--repos-root",
                str(fixture_root),
                "--sources",
                str(fixture_sources),
                "--inventory",
                str(out),
                "--write",
            ]
        )
        == 0
    )
    body = out.read_text(encoding="utf-8")
    assert "999_uncommitted.sql" not in body


def test_generation_is_deterministic(
    tmp_path: Path, fixture_root: Path, fixture_sources: Path
) -> None:
    """Two runs over an unchanged tree must produce byte-identical output.

    A whole-file gate is only meaningful if the generator is a function of the
    tree alone -- no timestamps, no SHAs, no set iteration order.
    """
    first = tmp_path / "one.yaml"
    second = tmp_path / "two.yaml"
    for out in (first, second):
        assert (
            _run(
                [
                    "--repos-root",
                    str(fixture_root),
                    "--sources",
                    str(fixture_sources),
                    "--inventory",
                    str(out),
                    "--write",
                ]
            )
            == 0
        )
    assert first.read_bytes() == second.read_bytes()


def test_resolved_sha_is_not_embedded_in_the_artifact(
    tmp_path: Path, fixture_root: Path, fixture_sources: Path
) -> None:
    """The peer's resolved SHA is LOGGED, never written into the artifact.

    Embedding it would redden the gate on every unrelated peer commit, which is
    exactly the "continuous stream of blocked PRs in an unrelated repo" failure
    the derive direction exists to avoid.
    """
    out = tmp_path / "inventory.yaml"
    _run(
        [
            "--repos-root",
            str(fixture_root),
            "--sources",
            str(fixture_sources),
            "--inventory",
            str(out),
            "--write",
        ]
    )
    sha = _git(fixture_root / "peer_one", "rev-parse", "refs/heads/dev").strip()
    assert sha not in out.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Fail-closed preconditions (AC: peers present, git, counted)
# ---------------------------------------------------------------------------


def test_missing_peer_exits_nonzero(
    tmp_path: Path,
    fixture_root: Path,
    fixture_sources: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A repos-root missing one expected peer must exit non-zero.

    This is the regression test named directly in the ticket's acceptance
    criteria: an absent peer previously produced a WARNING-severity
    MISSING_REPO and zero findings, so a failed clone and a clean repo were
    indistinguishable downstream.
    """
    import shutil

    shutil.rmtree(fixture_root / "peer_one")
    rc = _run(
        [
            "--repos-root",
            str(fixture_root),
            "--sources",
            str(fixture_sources),
            "--inventory",
            str(tmp_path / "out.yaml"),
            "--write",
        ]
    )
    assert rc != 0
    assert "peer_one" in capsys.readouterr().err


def test_peer_present_but_not_a_git_repo_exits_nonzero(
    tmp_path: Path,
    fixture_root: Path,
    fixture_sources: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A peer directory that exists but is not a git repo fails closed.

    A half-landed clone can leave a populated-but-not-git directory; treating
    that as a valid input is the same empty-result-is-not-absence class.
    """
    import shutil

    shutil.rmtree(fixture_root / "peer_one" / ".git")
    rc = _run(
        [
            "--repos-root",
            str(fixture_root),
            "--sources",
            str(fixture_sources),
            "--inventory",
            str(tmp_path / "out.yaml"),
            "--write",
        ]
    )
    assert rc != 0
    assert "peer_one" in capsys.readouterr().err


def test_unresolvable_branch_exits_nonzero(
    tmp_path: Path, fixture_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A declared branch that does not exist in the peer clone fails closed."""
    sources = tmp_path / "sources.yaml"
    sources.write_text(
        "---\n"
        'version: "1"\n'
        "databases:\n"
        "  demo:\n"
        "    connection_env: DEMO_DB_URL\n"
        "    migration_sets:\n"
        "      - source_repo: peer_one\n"
        "        branch: no_such_branch\n"
        "        directory: db/migrations\n",
        encoding="utf-8",
    )
    rc = _run(
        [
            "--repos-root",
            str(fixture_root),
            "--sources",
            str(sources),
            "--inventory",
            str(tmp_path / "out.yaml"),
            "--write",
        ]
    )
    assert rc != 0
    assert "no_such_branch" in capsys.readouterr().err


def test_logs_peer_branch_and_resolved_sha(
    tmp_path: Path,
    fixture_root: Path,
    fixture_sources: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Every peer's name, branch, and resolved SHA is logged.

    AC: a future count discrepancy must be diagnosable from the job log alone,
    rather than by re-deriving ground truth by hand as OMN-15771 had to.
    """
    _run(
        [
            "--repos-root",
            str(fixture_root),
            "--sources",
            str(fixture_sources),
            "--inventory",
            str(tmp_path / "out.yaml"),
            "--write",
        ]
    )
    sha = _git(fixture_root / "peer_one", "rev-parse", "refs/heads/dev").strip()
    out = capsys.readouterr().out
    assert "peer_one" in out
    assert "dev" in out
    assert sha in out


def test_resolved_peer_count_is_asserted(
    tmp_path: Path,
    fixture_root: Path,
    fixture_sources: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The number of peers actually resolved is reported against the expected
    count, so a partial peer set cannot pass as a full validation."""
    _run(
        [
            "--repos-root",
            str(fixture_root),
            "--sources",
            str(fixture_sources),
            "--inventory",
            str(tmp_path / "out.yaml"),
            "--write",
        ]
    )
    assert "1/1" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# --check mode (the gate)
# ---------------------------------------------------------------------------


def test_check_passes_when_committed_artifact_matches(
    tmp_path: Path, fixture_root: Path, fixture_sources: Path
) -> None:
    out = tmp_path / "inventory.yaml"
    args = [
        "--repos-root",
        str(fixture_root),
        "--sources",
        str(fixture_sources),
        "--inventory",
        str(out),
    ]
    assert _run([*args, "--write"]) == 0
    assert _run([*args, "--check"]) == 0


def test_check_fails_on_whole_file_difference_and_names_regen_command(
    tmp_path: Path,
    fixture_root: Path,
    fixture_sources: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Any whole-file difference fails, and the message names the exact
    regeneration command -- the gate's output must be actionable rather than a
    list of filenames to transcribe by hand."""
    out = tmp_path / "inventory.yaml"
    args = [
        "--repos-root",
        str(fixture_root),
        "--sources",
        str(fixture_sources),
        "--inventory",
        str(out),
    ]
    assert _run([*args, "--write"]) == 0
    out.write_text(out.read_text(encoding="utf-8") + "# hand edit\n", encoding="utf-8")
    assert _run([*args, "--check"]) != 0
    captured = capsys.readouterr()
    assert "generate-migration-inventory" in captured.out + captured.err


def test_check_fails_when_a_peer_migration_is_added(
    tmp_path: Path, fixture_root: Path, fixture_sources: Path
) -> None:
    """The defect this whole ticket exists for: a migration landing in a peer
    repo must redden the gate, deterministically, without anyone remembering to
    transcribe it."""
    out = tmp_path / "inventory.yaml"
    args = [
        "--repos-root",
        str(fixture_root),
        "--sources",
        str(fixture_sources),
        "--inventory",
        str(out),
    ]
    assert _run([*args, "--write"]) == 0
    peer = fixture_root / "peer_one"
    (peer / "db" / "migrations" / "003_c.sql").write_text(
        "CREATE TABLE c();", encoding="utf-8"
    )
    _git(peer, "add", "-A")
    _git(peer, "commit", "-q", "-m", "new migration")
    assert _run([*args, "--check"]) != 0


def test_check_is_the_default_mode(
    tmp_path: Path, fixture_root: Path, fixture_sources: Path
) -> None:
    """Running with neither flag must CHECK, never silently rewrite the
    artifact: the CI invocation must not be one typo away from self-healing."""
    out = tmp_path / "inventory.yaml"
    args = [
        "--repos-root",
        str(fixture_root),
        "--sources",
        str(fixture_sources),
        "--inventory",
        str(out),
    ]
    assert _run([*args, "--write"]) == 0
    before = out.read_bytes()
    peer = fixture_root / "peer_one"
    (peer / "db" / "migrations" / "004_d.sql").write_text(
        "CREATE TABLE d();", encoding="utf-8"
    )
    _git(peer, "add", "-A")
    _git(peer, "commit", "-q", "-m", "new migration")
    assert _run(args) != 0
    assert out.read_bytes() == before


# ---------------------------------------------------------------------------
# Annotation layer
# ---------------------------------------------------------------------------


def test_annotations_are_applied_when_present(
    tmp_path: Path, fixture_root: Path, fixture_sources: Path
) -> None:
    annotations = tmp_path / "annotations.yaml"
    annotations.write_text(
        "---\n"
        'version: "1"\n'
        "annotations:\n"
        '  "peer_one/db/migrations/001_a.sql":\n'
        "    - alpha\n"
        "    - beta\n",
        encoding="utf-8",
    )
    out = tmp_path / "inventory.yaml"
    assert (
        _run(
            [
                "--repos-root",
                str(fixture_root),
                "--sources",
                str(fixture_sources),
                "--inventory",
                str(out),
                "--write",
            ],
            annotations=annotations,
        )
        == 0
    )
    entries = yaml.safe_load(out.read_text(encoding="utf-8"))["databases"]["demo"][
        "migration_sets"
    ][0]["migrations"]
    by_file = {e["file"]: e for e in entries}
    assert by_file["001_a.sql"]["tables"] == ["alpha", "beta"]
    assert "tables" not in by_file["002_b.sql"]


def test_orphan_annotation_exits_nonzero(
    tmp_path: Path,
    fixture_root: Path,
    fixture_sources: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An annotation pointing at a file no longer in any peer tree is stale
    data and must be surfaced, not silently ignored -- silently-ignored stale
    listings are precisely what OMN-15771 had to reconcile by hand."""
    annotations = tmp_path / "annotations.yaml"
    annotations.write_text(
        "---\n"
        'version: "1"\n'
        "annotations:\n"
        '  "peer_one/db/migrations/900_deleted.sql":\n'
        "    - gone\n",
        encoding="utf-8",
    )
    rc = _run(
        [
            "--repos-root",
            str(fixture_root),
            "--sources",
            str(fixture_sources),
            "--inventory",
            str(tmp_path / "out.yaml"),
            "--write",
        ],
        annotations=annotations,
    )
    assert rc != 0
    assert "900_deleted.sql" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Committed artifact wiring
# ---------------------------------------------------------------------------


def test_committed_inventory_declares_itself_generated() -> None:
    """The committed artifact must carry a do-not-edit banner naming the
    regeneration command, so the next hand-editor is told before they start."""
    body = (_BOUNDARIES / "migration_inventory.yaml").read_text(encoding="utf-8")
    assert "GENERATED" in body
    assert "generate-migration-inventory" in body


def test_sources_declares_every_peer_with_an_explicit_branch() -> None:
    """Every declared set pins its branch explicitly.

    Inferring the peer's default branch from ``origin/HEAD`` is not acceptable:
    a peer changing its default branch would silently change what this gate
    validates against. It is also observably unreliable -- a canonical clone
    read ``origin/HEAD`` as ``main`` for a peer whose real default branch on
    GitHub is ``dev``.
    """
    sources = yaml.safe_load(
        (_BOUNDARIES / "migration_inventory_sources.yaml").read_text(encoding="utf-8")
    )
    sets = [
        mset for cfg in sources["databases"].values() for mset in cfg["migration_sets"]
    ]
    assert sets
    for mset in sets:
        assert mset.get("source_repo")
        assert mset.get("branch")
        assert mset.get("directory")
        assert "migrations" not in mset, (
            "the sources config must carry no file lists; those are derived"
        )


def test_sources_and_committed_inventory_describe_the_same_sets() -> None:
    """Structural pin: the generated artifact's set list is the sources' set
    list, in the same order."""
    sources = yaml.safe_load(
        (_BOUNDARIES / "migration_inventory_sources.yaml").read_text(encoding="utf-8")
    )
    inventory = yaml.safe_load(
        (_BOUNDARIES / "migration_inventory.yaml").read_text(encoding="utf-8")
    )

    def _keys(doc: dict[str, Any]) -> list[tuple[str, str, str]]:
        return [
            (str(db), str(m["source_repo"]), str(m["directory"]))
            for db, cfg in doc["databases"].items()
            for m in cfg["migration_sets"]
        ]

    assert _keys(sources) == _keys(inventory)


def test_every_annotation_key_targets_a_declared_set() -> None:
    """The committed annotation layer carries no keys outside the declared
    sets -- an annotation nobody can reach is dead data."""
    sources = yaml.safe_load(
        (_BOUNDARIES / "migration_inventory_sources.yaml").read_text(encoding="utf-8")
    )
    prefixes = tuple(
        f"{m['source_repo']}/{m['directory']}/"
        for cfg in sources["databases"].values()
        for m in cfg["migration_sets"]
    )
    annotations = yaml.safe_load(
        (_BOUNDARIES / "migration_table_annotations.yaml").read_text(encoding="utf-8")
    )
    for key in annotations["annotations"]:
        assert key.startswith(prefixes), key
