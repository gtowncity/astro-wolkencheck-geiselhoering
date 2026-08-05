import io
import tarfile
from pathlib import Path

import pytest

from nowcast_service.downloads.safe_tar import (
    TarLimits,
    UnsafeArchiveError,
    extract_tar_safely,
    validate_tar,
)


def make_tar(path: Path, members: list[tuple[str, bytes, str]]) -> None:
    with tarfile.open(path, "w") as bundle:
        for name, content, kind in members:
            info = tarfile.TarInfo(name)
            if kind == "file":
                info.size = len(content)
                bundle.addfile(info, io.BytesIO(content))
            elif kind == "symlink":
                info.type = tarfile.SYMTYPE
                info.linkname = "target"
                bundle.addfile(info)
            else:
                raise AssertionError(kind)


def test_extracts_only_valid_flat_hdf5_members(tmp_path: Path) -> None:
    archive = tmp_path / "valid.tar"
    make_tar(archive, [("composite_rv_20260804_2320_000-hd5", b"1234", "file")])

    files = extract_tar_safely(archive, tmp_path / "out")

    assert len(files) == 1
    assert files[0].read_bytes() == b"1234"


@pytest.mark.parametrize(
    "name",
    ["../escape-hd5", "/absolute-hd5", "folder/nested-hd5", "C:\\evil-hd5"],
)
def test_rejects_unsafe_paths(tmp_path: Path, name: str) -> None:
    archive = tmp_path / "bad.tar"
    make_tar(archive, [(name, b"1234", "file")])

    with pytest.raises(UnsafeArchiveError):
        validate_tar(archive)


def test_rejects_symlinks(tmp_path: Path) -> None:
    archive = tmp_path / "link.tar"
    make_tar(archive, [("link-hd5", b"", "symlink")])

    with pytest.raises(UnsafeArchiveError, match="member type"):
        validate_tar(archive)


def test_rejects_expanded_size_over_limit(tmp_path: Path) -> None:
    archive = tmp_path / "large.tar"
    make_tar(archive, [("large-hd5", b"12345", "file")])

    with pytest.raises(UnsafeArchiveError, match="member size"):
        validate_tar(archive, TarLimits(max_member_bytes=4))
