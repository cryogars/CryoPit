"""Bounded-memory staging for inbound attachment uploads.

Multipart parsing may already spool large request bodies to disk, but CryoPit
must not call ``read()`` on the whole uploaded file and recreate a full Python
``bytes`` copy.  This module copies the incoming file stream into a private
scratch file beneath ``EXPORT_DIR/.upload-staging`` while incrementally
counting bytes and computing SHA-256.

These files are untrusted/incomplete request scratch, not scientific archive
products.  Callers remove them on every rejection path; application startup
sweeps leftovers from a killed process or host restart.
"""
from __future__ import annotations

import hashlib
import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from .config import EXPORT_DIR
from .storage_lifecycle import fsync_handle

_LOG = logging.getLogger(__name__)
UPLOAD_STAGING_DIRNAME = ".upload-staging"
_STAGE_SUFFIX = ".upload.part"
_DEFAULT_CHUNK_BYTES = 1024 * 1024
_SNIFF_BYTES = 32


class UploadStagingError(RuntimeError):
    """Base class for inbound staging failures."""


class EmptyUpload(UploadStagingError):
    pass


class UploadTooLarge(UploadStagingError):
    pass


@dataclass(frozen=True)
class StagedUpload:
    path: Path
    size_bytes: int
    sha256: str
    head: bytes


def staging_dir(export_dir: str | os.PathLike[str] | None = None) -> Path:
    root = Path(export_dir if export_dir is not None else EXPORT_DIR)
    path = root / UPLOAD_STAGING_DIRNAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def cleanup_staged_upload(path: str | os.PathLike[str]) -> None:
    """Best-effort deletion of one inbound scratch file."""
    p = Path(path)
    try:
        p.unlink(missing_ok=True)
    except OSError:
        _LOG.exception("could not remove staged upload %s", p)
        return
    try:
        p.parent.rmdir()
    except OSError:
        pass


def stage_upload_stream(stream: BinaryIO, *, max_bytes: int,
                        chunk_size: int = _DEFAULT_CHUNK_BYTES,
                        export_dir: str | os.PathLike[str] | None = None) -> StagedUpload:
    """Copy one upload stream to disk with bounded memory.

    Size enforcement and SHA-256 happen while the bytes are copied.  Only a
    small prefix is retained in memory for file-signature detection.  Any
    empty, oversized, or failed upload removes its partial scratch file before
    the exception reaches the HTTP layer.
    """
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")

    root = staging_dir(export_dir)
    fd, raw = tempfile.mkstemp(prefix="upload-", suffix=_STAGE_SUFFIX, dir=root)
    path = Path(raw)
    total = 0
    head = bytearray()
    digest = hashlib.sha256()
    try:
        with os.fdopen(fd, "wb") as handle:
            while True:
                chunk = stream.read(chunk_size)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise UploadTooLarge(f"upload exceeds {max_bytes} bytes")
                if len(head) < _SNIFF_BYTES:
                    needed = _SNIFF_BYTES - len(head)
                    head.extend(chunk[:needed])
                digest.update(chunk)
                handle.write(chunk)
            if total == 0:
                raise EmptyUpload("empty upload")
            handle.flush()
            fsync_handle(handle)
        return StagedUpload(path=path, size_bytes=total,
                            sha256=digest.hexdigest(), head=bytes(head))
    except Exception:
        # os.fdopen owns fd once entered. If construction failed before that,
        # close the raw descriptor explicitly before deleting the file.
        try:
            os.close(fd)
        except OSError:
            pass
        cleanup_staged_upload(path)
        raise


def sweep_staged_uploads(export_dir: str | os.PathLike[str] | None = None) -> int:
    """Remove inbound scratch files left by a previous CryoPit process."""
    export_root = Path(export_dir if export_dir is not None else EXPORT_DIR)
    root = export_root / UPLOAD_STAGING_DIRNAME
    if not root.exists():
        return 0
    removed = 0
    for path in root.glob(f"*{_STAGE_SUFFIX}"):
        try:
            path.unlink()
            removed += 1
        except FileNotFoundError:
            continue
        except OSError:
            _LOG.exception("could not sweep staged upload %s", path)
    try:
        root.rmdir()
    except OSError:
        pass
    return removed
