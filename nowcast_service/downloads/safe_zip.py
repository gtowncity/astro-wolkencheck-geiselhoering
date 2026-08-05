"""Safe validation and extraction for DWD CAP ZIP archives."""

from __future__ import annotations

import os
import stat
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


class UnsafeZipError(RuntimeError):
    """Raised when a ZIP archive violates structural or size limits."""


@dataclass(frozen=True, slots=True)
class ZipLimits:
    max_entries: int = 20_000
    max_member_bytes: int = 2 * 1024 * 1024
    max_total_bytes: int = 128 * 1024 * 1024
    max_depth: int = 4
    max_compression_ratio: float = 250.0
    allowed_suffixes: tuple[str, ...] = (".xml",)


DEFAULT_ZIP_LIMITS = ZipLimits()


def _safe_path(name: str, limits: ZipLimits) -> PurePosixPath:
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise UnsafeZipError(f"Unsafe ZIP path: {name!r}")
    if len(path.parts) > limits.max_depth:
        raise UnsafeZipError("ZIP member nesting exceeds the safety limit")
    if any(":" in part for part in path.parts):
        raise UnsafeZipError("ZIP member contains a drive-like path component")
    return path


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    unix_mode = info.external_attr >> 16
    return stat.S_ISLNK(unix_mode)


def validate_zip(
    archive: Path, limits: ZipLimits = DEFAULT_ZIP_LIMITS
) -> tuple[zipfile.ZipInfo, ...]:
    try:
        with zipfile.ZipFile(archive, "r") as bundle:
            infos = bundle.infolist()
            if not infos or len(infos) > limits.max_entries:
                raise UnsafeZipError("ZIP entry count is outside the safety limit")
            accepted: list[zipfile.ZipInfo] = []
            seen: set[str] = set()
            total = 0
            for info in infos:
                if info.is_dir():
                    continue
                path = _safe_path(info.filename, limits)
                normalized = path.as_posix()
                if normalized in seen:
                    raise UnsafeZipError("ZIP contains duplicate member paths")
                seen.add(normalized)
                if info.flag_bits & 0x1:
                    raise UnsafeZipError("Encrypted ZIP members are not supported")
                if _is_symlink(info):
                    raise UnsafeZipError("ZIP symlinks are not supported")
                allowed_type = any(
                    normalized.casefold().endswith(suffix) for suffix in limits.allowed_suffixes
                )
                if not allowed_type:
                    raise UnsafeZipError(f"Unexpected ZIP member type: {info.filename}")
                if info.file_size <= 0 or info.file_size > limits.max_member_bytes:
                    raise UnsafeZipError("ZIP member size is outside the safety limit")
                total += info.file_size
                if total > limits.max_total_bytes:
                    raise UnsafeZipError("Expanded ZIP size exceeds the safety limit")
                denominator = max(1, info.compress_size)
                if info.file_size / denominator > limits.max_compression_ratio:
                    raise UnsafeZipError("ZIP member compression ratio is implausible")
                accepted.append(info)
            if not accepted:
                raise UnsafeZipError("ZIP contains no usable files")
            return tuple(accepted)
    except zipfile.BadZipFile as exc:
        raise UnsafeZipError("ZIP archive is corrupt") from exc


def extract_zip_safely(
    archive: Path,
    destination: Path,
    limits: ZipLimits = DEFAULT_ZIP_LIMITS,
) -> tuple[Path, ...]:
    infos = validate_zip(archive, limits)
    destination.mkdir(parents=True, exist_ok=True)
    extracted: list[Path] = []
    try:
        with zipfile.ZipFile(archive, "r") as bundle:
            for info in infos:
                relative = _safe_path(info.filename, limits)
                target = destination.joinpath(*relative.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary = target.with_name(f".{target.name}.part")
                temporary.unlink(missing_ok=True)
                written = 0
                try:
                    with bundle.open(info, "r") as source, temporary.open("xb") as output:
                        while chunk := source.read(64 * 1024):
                            written += len(chunk)
                            if written > info.file_size or written > limits.max_member_bytes:
                                raise UnsafeZipError("Expanded ZIP member exceeded its limit")
                            output.write(chunk)
                        output.flush()
                        os.fsync(output.fileno())
                    if written != info.file_size:
                        raise UnsafeZipError("Expanded ZIP member size is inconsistent")
                    os.replace(temporary, target)
                    extracted.append(target)
                except BaseException:
                    temporary.unlink(missing_ok=True)
                    raise
    except zipfile.BadZipFile as exc:
        raise UnsafeZipError("ZIP archive failed during extraction") from exc
    return tuple(extracted)
