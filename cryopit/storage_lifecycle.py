"""Shared storage lifecycle locking and best-effort crash durability.

Pit-folder publication and attachment publication touch the same directory
namespace. They therefore use one lock, rather than independent archive and
attachment locks that can race while a pit folder is being renamed.

The in-process lock is always available. On POSIX, ``flock`` also excludes
other CryoPit processes. Where process locking is unavailable, CryoPit logs a
warning and remains safe only when run as a single application process.
"""
from __future__ import annotations

import contextlib
import errno
import logging
import os
import threading
from pathlib import Path

_LOG = logging.getLogger(__name__)
_THREAD_LOCK = threading.RLock()
_LOCAL = threading.local()
_WARNED_PROCESS_LOCK = False
_WARNED_DIR_SYNC = False
_WARNED_FILE_SYNC = False


def _warn_process_lock_once(exc: BaseException) -> None:
    global _WARNED_PROCESS_LOCK
    if _WARNED_PROCESS_LOCK:
        return
    _WARNED_PROCESS_LOCK = True
    _LOG.warning(
        "Cross-process storage locking is unavailable (%s). "
        "CryoPit remains thread-safe in one process, but multiple workers or "
        "processes must not share this database/export directory.",
        exc,
    )


def _warn_dir_sync_once(exc: BaseException) -> None:
    global _WARNED_DIR_SYNC
    if _WARNED_DIR_SYNC:
        return
    _WARNED_DIR_SYNC = True
    _LOG.warning(
        "Directory fsync is unavailable on this platform/filesystem (%s). "
        "Atomic renames remain recoverable, but sudden power loss may delay "
        "directory-entry durability.",
        exc,
    )


def _warn_file_sync_once(exc: BaseException) -> None:
    global _WARNED_FILE_SYNC
    if _WARNED_FILE_SYNC:
        return
    _WARNED_FILE_SYNC = True
    _LOG.warning(
        "File fsync is unavailable on this platform/filesystem (%s). "
        "CryoPit operations remain journaled and recoverable, but sudden power "
        "loss may occur before recently written bytes reach stable storage.",
        exc,
    )


def _unsupported_sync_error(exc: OSError) -> bool:
    return exc.errno in {
        errno.EACCES, errno.EBADF, errno.EINVAL, errno.ENOTSUP,
        getattr(errno, "EOPNOTSUPP", errno.ENOTSUP),
    }


@contextlib.contextmanager
def storage_lock(export_root: str | os.PathLike[str]):
    """Serialize every archive/attachment filesystem lifecycle operation.

    The lock is re-entrant within a thread. Only the outermost acquisition for
    a given export root opens and locks the shared lock file, preventing nested
    recovery helpers from accidentally releasing another layer's process lock.
    """
    root = Path(export_root).resolve()
    key = os.fspath(root)
    with _THREAD_LOCK:
        depths = getattr(_LOCAL, "depths", None)
        if depths is None:
            depths = {}
            _LOCAL.depths = depths
        depth = depths.get(key, 0)
        if depth:
            depths[key] = depth + 1
            try:
                yield
            finally:
                depths[key] -= 1
                if depths[key] == 0:
                    depths.pop(key, None)
            return

        root.mkdir(parents=True, exist_ok=True)
        lock_dir = root / ".locks"
        lock_dir.mkdir(parents=True, exist_ok=True)
        fh = open(lock_dir / "storage.lock", "a+b")
        locked = False
        depths[key] = 1
        try:
            try:
                import fcntl  # POSIX only
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
                locked = True
            except (ImportError, OSError) as exc:
                _warn_process_lock_once(exc)
            yield
        finally:
            if locked:
                try:
                    import fcntl
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
                except (ImportError, OSError) as exc:
                    _LOG.warning("Could not release CryoPit process lock cleanly: %s", exc)
            depths.pop(key, None)
            fh.close()


def ensure_directory(path: str | os.PathLike[str]) -> Path:
    """Create a directory tree and durably publish newly created components."""
    target = Path(path)
    missing = []
    cursor = target
    while not cursor.exists():
        missing.append(cursor)
        cursor = cursor.parent
    target.mkdir(parents=True, exist_ok=True)
    for created in reversed(missing):
        fsync_directory(created)
        fsync_directory(created.parent)
    return target


def fsync_handle(handle) -> None:
    """Flush an open file handle, degrading visibly on unsupported filesystems."""
    try:
        os.fsync(handle.fileno())
    except OSError as exc:
        if _unsupported_sync_error(exc):
            _warn_file_sync_once(exc)
            return
        raise


def fsync_file(path: str | os.PathLike[str]) -> None:
    """Flush one closed regular file to stable storage when supported."""
    p = Path(path)
    with open(p, "rb") as fh:
        fsync_handle(fh)


def fsync_directory(path: str | os.PathLike[str]) -> None:
    """Best-effort directory-entry durability after rename/unlink."""
    p = Path(path)
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    try:
        fd = os.open(p, flags)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError as exc:
        # Windows and some network filesystems do not support opening/fsyncing
        # directories. The journal/pending state still makes operations
        # recoverable, so this is a visible degradation rather than a failure.
        if _unsupported_sync_error(exc):
            _warn_dir_sync_once(exc)
            return
        raise


def sync_tree(root: str | os.PathLike[str]) -> None:
    """Flush every file and directory in a complete staged publication tree."""
    base = Path(root)
    if not base.exists():
        return
    files = sorted((p for p in base.rglob("*") if p.is_file()), key=lambda p: len(p.parts))
    for path in files:
        fsync_file(path)
    dirs = sorted((p for p in base.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True)
    for path in dirs:
        fsync_directory(path)
    fsync_directory(base)


def durable_replace(source: str | os.PathLike[str], target: str | os.PathLike[str]) -> None:
    """Atomically replace ``target`` and sync affected directory entries."""
    src = Path(source)
    dst = Path(target)
    os.replace(src, dst)
    fsync_directory(dst.parent)
    if src.parent != dst.parent:
        fsync_directory(src.parent)


def durable_rename(source: str | os.PathLike[str], target: str | os.PathLike[str]) -> None:
    """Atomically rename ``source`` and sync affected directory entries."""
    src = Path(source)
    dst = Path(target)
    os.rename(src, dst)
    fsync_directory(dst.parent)
    if src.parent != dst.parent:
        fsync_directory(src.parent)


def durable_unlink(path: str | os.PathLike[str], *, missing_ok: bool = False) -> None:
    """Delete a file and sync its containing directory."""
    p = Path(path)
    try:
        p.unlink()
    except FileNotFoundError:
        if missing_ok:
            return
        raise
    fsync_directory(p.parent)


def durable_rmtree(path: str | os.PathLike[str]) -> None:
    """Remove a directory tree and sync its parent directory."""
    import shutil

    p = Path(path)
    if not p.exists():
        return
    parent = p.parent
    shutil.rmtree(p)
    fsync_directory(parent)
