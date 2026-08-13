"""Scratch-file lifecycle for browser download ZIPs.

Downloads are deliberately spooled beneath ``EXPORT_DIR/.download-staging``
so archive size does not become Python-process memory usage.  These files are
*not* scientific archive products: the response stream deletes them when it
finishes or is closed, and application startup removes leftovers from a killed
process or host restart.
"""
from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Iterator

from .config import EXPORT_DIR

_LOG = logging.getLogger(__name__)
DOWNLOAD_STAGING_DIRNAME = ".download-staging"
_STAGE_SUFFIX = ".zip.part"
_STREAM_CHUNK_BYTES = 1024 * 1024


def staging_dir(export_dir: str | os.PathLike[str] | None = None) -> Path:
    """Return the private download-scratch directory, creating it if needed."""
    root = Path(export_dir if export_dir is not None else EXPORT_DIR)
    path = root / DOWNLOAD_STAGING_DIRNAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def create_staged_zip_path(export_dir: str | os.PathLike[str] | None = None) -> Path:
    """Reserve a unique path for one in-progress download ZIP."""
    root = staging_dir(export_dir)
    fd, raw = tempfile.mkstemp(prefix="download-", suffix=_STAGE_SUFFIX, dir=root)
    os.close(fd)
    return Path(raw)


def cleanup_staged_zip(path: str | os.PathLike[str]) -> None:
    """Best-effort removal of one scratch ZIP.

    Cleanup must never turn an otherwise successful request into an error.
    Startup reconciliation is the backstop if deletion itself fails.
    """
    p = Path(path)
    try:
        p.unlink(missing_ok=True)
    except OSError:
        _LOG.exception("could not remove staged download %s", p)
        return
    # Keep the export root free of empty scratch directories. This also means a
    # stopped CryoPit instance does not make an otherwise-empty restore target
    # look populated merely because a download happened earlier. Concurrent
    # downloads make rmdir fail harmlessly until the last file is removed.
    try:
        p.parent.rmdir()
    except OSError:
        pass


def stream_staged_zip(path: str | os.PathLike[str], *,
                      chunk_size: int = _STREAM_CHUNK_BYTES) -> Iterator[bytes]:
    """Yield a staged ZIP in bounded chunks and always attempt cleanup.

    WSGI servers close the response iterable when a client disconnects.  The
    generator's ``finally`` therefore covers normal completion, response close,
    and interrupted transfers.  ``sweep_staged_downloads`` covers process/host
    crashes where Python never gets a chance to execute this block.
    """
    p = Path(path)
    try:
        with p.open("rb") as handle:
            while True:
                chunk = handle.read(chunk_size)
                if not chunk:
                    break
                yield chunk
    finally:
        cleanup_staged_zip(p)


def sweep_staged_downloads(export_dir: str | os.PathLike[str] | None = None) -> int:
    """Remove scratch ZIPs left by a previous CryoPit process.

    CryoPit's supported SQLite/export deployment is one application process, so
    no legitimate download from an earlier process can still be active during
    startup.  Only CryoPit-owned ``*.zip.part`` files are removed.
    """
    export_root = Path(export_dir if export_dir is not None else EXPORT_DIR)
    root = export_root / DOWNLOAD_STAGING_DIRNAME
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
            _LOG.exception("could not sweep staged download %s", path)
    try:
        root.rmdir()
    except OSError:
        pass
    return removed
