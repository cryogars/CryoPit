"""Bounded, disk-backed HEIC -> JPEG conversion.

HEIC decoding is one of CryoPit's genuinely memory-intensive paths because the
compressed source must become a full pixel buffer before Pillow can encode the
JPEG derivative.  This module keeps the avoidable pieces off the heap:

* input is opened directly from the Stage 2 upload scratch file;
* JPEG output is written directly to another scratch file;
* SHA-256 is computed from that file in bounded chunks; and
* a process-local semaphore caps simultaneous conversions.

The conversion permit is intentionally independent from the storage lifecycle
lock.  Callers must finish conversion before acquiring the archive/attachment
publication lock so an expensive decode cannot block pit-folder operations.
"""
from __future__ import annotations

import contextlib
import hashlib
import logging
import os
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

from .config import HEIC_CONCURRENCY
from .storage_lifecycle import fsync_handle
from .upload_staging import cleanup_staged_upload, staging_dir

_LOG = logging.getLogger(__name__)
_CONVERSION_SLOTS = threading.BoundedSemaphore(HEIC_CONCURRENCY)
_REGISTER_LOCK = threading.Lock()
_HEIF_REGISTERED = False
_STAGE_SUFFIX = ".upload.part"
_HASH_CHUNK_BYTES = 1024 * 1024


@dataclass(frozen=True)
class ConvertedHeic:
    path: Path
    sha256: str
    size_bytes: int


@contextlib.contextmanager
def heic_conversion_slot():
    """Acquire one conversion permit and always return it, even on failure."""
    _CONVERSION_SLOTS.acquire()
    try:
        yield
    finally:
        _CONVERSION_SLOTS.release()


def _hash_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    total = 0
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(_HASH_CHUNK_BYTES)
            if not chunk:
                break
            total += len(chunk)
            digest.update(chunk)
    return digest.hexdigest(), total


def _ensure_heif_opener(pillow_heif) -> None:
    """Register Pillow's HEIF opener once per process, safely across threads."""
    global _HEIF_REGISTERED
    if _HEIF_REGISTERED:
        return
    with _REGISTER_LOCK:
        if not _HEIF_REGISTERED:
            pillow_heif.register_heif_opener()
            _HEIF_REGISTERED = True


def _convert_heic_to_jpeg_file(source_path: str | os.PathLike[str], *,
                               export_dir: str | os.PathLike[str] | None = None) -> ConvertedHeic:
    """Decode one HEIC source and write a full-resolution JPEG scratch file.

    This low-level helper raises on decoder/encoder failures.  The public
    ``convert_heic_to_jpeg`` wrapper preserves CryoPit's historical behavior by
    treating conversion failure as "store the original HEIC unchanged".
    """
    import pillow_heif
    from PIL import Image

    _ensure_heif_opener(pillow_heif)
    root = staging_dir(export_dir)
    fd, raw = tempfile.mkstemp(prefix="heic-jpeg-", suffix=_STAGE_SUFFIX, dir=root)
    output = Path(raw)

    converted = None
    try:
        with os.fdopen(fd, "wb") as handle:
            with Image.open(source_path) as image:
                target = image
                if image.mode not in ("RGB", "L"):
                    converted = image.convert("RGB")
                    target = converted
                # Write directly to the securely-created scratch descriptor.
                # quality/subsampling intentionally match the pre-Stage-3
                # converter so scientific/photo behavior is unchanged.
                target.save(handle, format="JPEG", quality=95, subsampling=0,
                            optimize=True)
                handle.flush()
                fsync_handle(handle)
        digest, size_bytes = _hash_file(output)
        return ConvertedHeic(path=output, sha256=digest, size_bytes=size_bytes)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        cleanup_staged_upload(output)
        raise
    finally:
        if converted is not None:
            try:
                converted.close()
            except Exception:
                pass


def convert_heic_to_jpeg(source_path: str | os.PathLike[str], *,
                         export_dir: str | os.PathLike[str] | None = None) -> ConvertedHeic | None:
    """Convert one HEIC under the configured concurrency cap.

    Returns ``None`` when HEIC support is unavailable or conversion fails.  That
    is deliberate backwards-compatible behavior: the caller stores the original
    field photograph rather than losing it because a server lacks a decoder.
    """
    with heic_conversion_slot():
        try:
            return _convert_heic_to_jpeg_file(source_path, export_dir=export_dir)
        except Exception as exc:
            _LOG.warning("HEIC conversion unavailable/failed; retaining original: %s", exc)
            return None
