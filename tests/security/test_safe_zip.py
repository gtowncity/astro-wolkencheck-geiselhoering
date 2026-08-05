import stat
import zipfile
from pathlib import Path

import pytest

from nowcast_service.downloads.safe_zip import (
    UnsafeZipError,
    ZipLimits,
    extract_zip_safely,
    validate_zip,
)


def make_zip(path: Path, members: list[tuple[str, bytes]]) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for name, content in members:
            bundle.writestr(name, content)


def test_valid_xml_files_extract_with_nested_safe_path(tmp_path: Path) -> None:
    archive = tmp_path / "cap.zip"
    make_zip(archive, [("alerts/one.xml", b"<alert/>")])

    files = extract_zip_safely(archive, tmp_path / "out")

    assert len(files) == 1
    assert files[0].relative_to(tmp_path / "out").as_posix() == "alerts/one.xml"
    assert files[0].read_bytes() == b"<alert/>"


@pytest.mark.parametrize(
    "name",
    ["../escape.xml", "/absolute.xml", "C:\\drive.xml", "a/b/c/d/e.xml"],
)
def test_unsafe_paths_are_rejected(tmp_path: Path, name: str) -> None:
    archive = tmp_path / "bad.zip"
    make_zip(archive, [(name, b"<alert/>")])

    with pytest.raises(UnsafeZipError):
        validate_zip(archive)


def test_non_xml_duplicate_and_empty_archives_are_rejected(tmp_path: Path) -> None:
    wrong = tmp_path / "wrong.zip"
    make_zip(wrong, [("readme.txt", b"text")])
    with pytest.raises(UnsafeZipError, match="member type"):
        validate_zip(wrong)

    duplicate = tmp_path / "duplicate.zip"
    with zipfile.ZipFile(duplicate, "w") as bundle:
        bundle.writestr("one.xml", b"1")
        bundle.writestr("one.xml", b"2")
    with pytest.raises(UnsafeZipError, match="duplicate"):
        validate_zip(duplicate)

    empty = tmp_path / "empty.zip"
    with zipfile.ZipFile(empty, "w"):
        pass
    with pytest.raises(UnsafeZipError, match="entry count"):
        validate_zip(empty)


def test_symlink_and_corrupt_zip_are_rejected(tmp_path: Path) -> None:
    link = tmp_path / "link.zip"
    info = zipfile.ZipInfo("link.xml")
    info.create_system = 3
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(link, "w") as bundle:
        bundle.writestr(info, "target")
    with pytest.raises(UnsafeZipError, match="symlink"):
        validate_zip(link)

    corrupt = tmp_path / "corrupt.zip"
    corrupt.write_bytes(b"not a zip")
    with pytest.raises(UnsafeZipError, match="corrupt"):
        validate_zip(corrupt)


def test_entry_and_total_size_limits_are_enforced(tmp_path: Path) -> None:
    archive = tmp_path / "limits.zip"
    make_zip(archive, [("one.xml", b"1234"), ("two.xml", b"5678")])

    with pytest.raises(UnsafeZipError, match="entry count"):
        validate_zip(archive, ZipLimits(max_entries=1))
    with pytest.raises(UnsafeZipError, match="member size"):
        validate_zip(archive, ZipLimits(max_member_bytes=3))
    with pytest.raises(UnsafeZipError, match="Expanded ZIP"):
        validate_zip(archive, ZipLimits(max_total_bytes=7))
