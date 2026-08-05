"""Safe TAR extraction without ``extractall``."""

from __future__ import annotations

import os
import tarfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


class UnsafeArchiveError(RuntimeError):
    """Raised when an archive violates structural or size constraints."""


@dataclass(frozen=True, slots=True)
class TarLimits:
    max_entries: int = 64
    max_member_bytes: int = 128 * 1024 * 1024
    max_total_bytes: int = 1024 * 1024 * 1024
    allowed_suffixes: tuple[str, ...] = ("-hd5", ".h5", ".hdf5")


def _safe_member_name(name: str) -> str:
    path = PurePosixPath(name.replace("\\", "/"))
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise UnsafeArchiveError(f"Unsafe archive path: {name!r}")
    if len(path.parts) != 1:
        raise UnsafeArchiveError("Nested archive paths are not accepted")
    return path.name


def validate_tar(archive: Path, limits: TarLimits = TarLimits()) -> tuple[tarfile.TarInfo, ...]:
    with tarfile.open(archive, mode="r:*") as bundle:
        members = bundle.getmembers()
        if not members or len(members) > limits.max_entries:
            raise UnsafeArchiveError("Archive entry count is outside the safety limit")

        accepted: list[tarfile.TarInfo] = []
        total = 0
        for member in members:
            name = _safe_member_name(member.name)
            if not member.isfile() or member.issym() or member.islnk() or member.isdev():
                raise UnsafeArchiveError(f"Unsupported archive member type: {member.name}")
            if not any(name.endswith(suffix) for suffix in limits.allowed_suffixes):
                raise UnsafeArchiveError(f"Unexpected archive file type: {member.name}")
            if member.size <= 0 or member.size > limits.max_member_bytes:
                raise UnsafeArchiveError("Archive member size is outside the safety limit")
            total += member.size
            if total > limits.max_total_bytes:
                raise UnsafeArchiveError("Expanded archive size exceeds the safety limit")
            accepted.append(member)
        return tuple(accepted)


def extract_tar_safely(
    archive: Path, destination: Path, limits: TarLimits = TarLimits()
) -> tuple[Path, ...]:
    members = validate_tar(archive, limits)
    destination.mkdir(parents=True, exist_ok=True)
    results: list[Path] = []

    with tarfile.open(archive, mode="r:*") as bundle:
        for member in members:
            name = _safe_member_name(member.name)
            target = destination / name
            temporary = destination / f".{name}.part"
            temporary.unlink(missing_ok=True)
            source = bundle.extractfile(member)
            if source is None:
                raise UnsafeArchiveError(f"Could not read archive member: {name}")
            written = 0
            try:
                with source, temporary.open("xb") as output:
                    while chunk := source.read(64 * 1024):
                        written += len(chunk)
                        if written > member.size or written > limits.max_member_bytes:
                            raise UnsafeArchiveError("Expanded member exceeded declared size")
                        output.write(chunk)
                    output.flush()
                    os.fsync(output.fileno())
                if written != member.size:
                    raise UnsafeArchiveError("Expanded member size differs from TAR metadata")
                os.replace(temporary, target)
                results.append(target)
            except BaseException:
                temporary.unlink(missing_ok=True)
                raise

    return tuple(results)
