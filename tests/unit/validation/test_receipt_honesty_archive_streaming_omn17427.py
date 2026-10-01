# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Frozen receipt archives spool to disk without weakening provenance guards."""

from __future__ import annotations

import io
import subprocess
import tarfile
from typing import TYPE_CHECKING, Any, cast

import pytest

from onex_change_control.validation import receipt_honesty_ratchet as ratchet

if TYPE_CHECKING:
    from pathlib import Path


def _archive(
    name: str, payload: bytes = b"receipt\n", kind: bytes = tarfile.REGTYPE
) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        member = tarfile.TarInfo(name)
        member.type = kind
        member.size = len(payload) if kind == tarfile.REGTYPE else 0
        member.linkname = "outside" if kind == tarfile.SYMTYPE else ""
        archive.addfile(member, io.BytesIO(payload) if member.isfile() else None)
    return buffer.getvalue()


def _git_archive(
    monkeypatch: pytest.MonkeyPatch, archive: bytes, returncode: int = 0
) -> None:
    def run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        assert args[1:] == [
            "archive",
            "--format=tar",
            ratchet._ORIGIN_COMMIT,
            "--",
            "drift/dod_receipts",
        ]
        assert not kwargs.get("capture_output"), "archive bytes must not be captured"
        assert kwargs["stdout"].seekable(), "archive output must spool to a file"
        kwargs["stdout"].write(archive)
        kwargs["stderr"].write(b"archive refused" if returncode else b"")
        return subprocess.CompletedProcess(args, returncode)

    monkeypatch.setattr(subprocess, "run", run)


def test_frozen_receipt_archive_uses_disk_stream_and_bounded_payload_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = b"x" * (128 * 1024)
    name = "drift/dod_receipts/OMN-1/dod-1/command.yaml"
    _git_archive(monkeypatch, _archive(name, payload))

    def no_members(_archive: tarfile.TarFile) -> list[tarfile.TarInfo]:
        pytest.fail("archive member table must not be eagerly materialized")

    monkeypatch.setattr(tarfile.TarFile, "getmembers", no_members)
    read = tarfile.ExFileObject.read
    sizes: list[int] = []

    def bounded_read(stream: tarfile.ExFileObject, size: int = -1) -> bytes:
        assert 0 < size <= 64 * 1024, "payload reads must be bounded"
        sizes.append(size)
        return read(stream, size)

    monkeypatch.setattr(tarfile.ExFileObject, "read", bounded_read)
    ratchet._extract_origin_receipts(tmp_path, tmp_path / "output")
    assert (tmp_path / "output" / name).read_bytes() == payload
    assert sizes


@pytest.mark.parametrize("kind", [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE])
def test_streamed_archive_still_rejects_nonregular_members(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: bytes
) -> None:
    _git_archive(monkeypatch, _archive("drift/dod_receipts/bad.yaml", kind=kind))
    with pytest.raises(ratchet.RatchetError, match="unsupported member"):
        ratchet._extract_origin_receipts(tmp_path, tmp_path / "output")


def test_streamed_archive_still_rejects_traversal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _git_archive(monkeypatch, _archive("../escape.yaml"))
    with pytest.raises(ratchet.RatchetError, match="traversal member"):
        ratchet._extract_origin_receipts(tmp_path, tmp_path / "output")
    assert not (tmp_path / "escape.yaml").exists()


def test_failed_git_archive_status_refuses_even_with_valid_tar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _git_archive(monkeypatch, _archive("receipt.yaml"), returncode=1)
    with pytest.raises(ratchet.RatchetError, match=r"archive.*failed.*archive refused"):
        ratchet._extract_origin_receipts(tmp_path, tmp_path / "output")
    assert not (tmp_path / "output").exists()


def test_malformed_streamed_archive_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _git_archive(monkeypatch, b"invalid tar")
    with pytest.raises(ratchet.RatchetError, match="unable to materialize"):
        ratchet._extract_origin_receipts(tmp_path, tmp_path / "output")


def test_streamed_archive_discards_processed_headers_including_pax(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    buffer = io.BytesIO()
    names: list[str] = []
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for index in range(32):
            directory = tarfile.TarInfo(f"drift/dod_receipts/dir-{index}")
            directory.type = tarfile.DIRTYPE
            archive.addfile(directory)
            name = f"drift/dod_receipts/dir-{index}/{'x' * 120}.yaml"
            names.append(name)
            member = tarfile.TarInfo(name)
            member.size = 8
            member.pax_headers = {"comment": "extended header remains supported"}
            archive.addfile(member, io.BytesIO(b"receipt\n"))
    _git_archive(monkeypatch, buffer.getvalue())
    next_member = tarfile.TarFile.next
    observed: list[int] = []

    def bounded_next(archive: tarfile.TarFile) -> tarfile.TarInfo | None:
        result = next_member(archive)
        members = cast("list[tarfile.TarInfo]", vars(archive)["members"])
        observed.append(len(members))
        assert len(members) <= 2, "processed archive headers must be discarded"
        return result

    monkeypatch.setattr(tarfile.TarFile, "next", bounded_next)
    ratchet._extract_origin_receipts(tmp_path, tmp_path / "output")
    assert observed
    for name in names:
        assert (tmp_path / "output" / name).read_bytes() == b"receipt\n"
