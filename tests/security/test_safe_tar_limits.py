import io
import tarfile
from pathlib import Path

import pytest

from nowcast_service.downloads.safe_tar import TarLimits, UnsafeArchiveError, validate_tar


def make_tar(path: Path, members: list[tuple[str, bytes]]) -> None:
    with tarfile.open(path, "w") as bundle:
        for name, content in members:
            info = tarfile.TarInfo(name)
            info.size = len(content)
            bundle.addfile(info, io.BytesIO(content))


def test_empty_archive_is_rejected(tmp_path: Path) -> None:
    archive = tmp_path / "empty.tar"
    with tarfile.open(archive, "w"):
        pass

    with pytest.raises(UnsafeArchiveError, match="entry count"):
        validate_tar(archive)


def test_entry_count_is_bounded(tmp_path: Path) -> None:
    archive = tmp_path / "many.tar"
    make_tar(archive, [("a-hd5", b"1"), ("b-hd5", b"2")])

    with pytest.raises(UnsafeArchiveError, match="entry count"):
        validate_tar(archive, TarLimits(max_entries=1))


def test_unexpected_member_suffix_is_rejected(tmp_path: Path) -> None:
    archive = tmp_path / "wrong.tar"
    make_tar(archive, [("readme.txt", b"text")])

    with pytest.raises(UnsafeArchiveError, match="file type"):
        validate_tar(archive)


def test_total_expanded_size_is_bounded(tmp_path: Path) -> None:
    archive = tmp_path / "total.tar"
    make_tar(archive, [("a-hd5", b"123"), ("b-hd5", b"456")])

    with pytest.raises(UnsafeArchiveError, match="Expanded archive"):
        validate_tar(archive, TarLimits(max_member_bytes=4, max_total_bytes=5))
