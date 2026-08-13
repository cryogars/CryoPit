"""Backup, verification, and restore tools for CryoPit's logical dataset.

The SQLite database and export tree are one dataset. The backup command places
the application in maintenance mode, waits for in-flight work to settle, takes
a SQLite online backup, copies the complete export tree, verifies that sources
did not change during the copy, and writes a checksummed ZIP bundle.

Usage:
    python -m cryopit.ops backup --output /backups/cryopit-20260805.zip
    python -m cryopit.ops verify /backups/cryopit-20260805.zip
    python -m cryopit.ops restore /backups/cryopit-20260805.zip --force
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from . import __version__
from .config import DB_PATH, EXPORT_DIR
from .download_staging import DOWNLOAD_STAGING_DIRNAME
from .upload_staging import UPLOAD_STAGING_DIRNAME
from .storage_lifecycle import (durable_replace, durable_unlink, fsync_file,
                                storage_lock)

MAINTENANCE_FILENAME = ".cryopit-maintenance"
MANIFEST_NAME = "cryopit-backup-manifest.json"
DB_ARCHIVE_NAME = "database/cryopit.db"
EXPORT_PREFIX = "exports/"


class BackupError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_members(zf: zipfile.ZipFile):
    seen = set()
    for info in zf.infolist():
        name = PurePosixPath(info.filename)
        if name.is_absolute() or ".." in name.parts or "\x00" in info.filename:
            raise BackupError(f"Unsafe path in backup: {info.filename!r}")
        if info.filename in seen:
            raise BackupError(f"Duplicate path in backup: {info.filename!r}")
        seen.add(info.filename)
        # Unix symlinks are encoded in the high mode bits of external_attr.
        if ((info.external_attr >> 16) & 0o170000) == 0o120000:
            raise BackupError(f"Symlinks are not permitted in backups: {info.filename!r}")
        if info.flag_bits & 0x1:
            raise BackupError("Encrypted ZIP members are not supported")
        yield info


def _source_fingerprint(db_path: Path, export_dir: Path) -> dict[str, tuple[int, int]]:
    result: dict[str, tuple[int, int]] = {}
    for path, key in ((db_path, "database"),
                      (Path(str(db_path) + "-wal"), "database-wal"),
                      (Path(str(db_path) + "-shm"), "database-shm")):
        if path.exists():
            stat = path.stat()
            result[key] = (stat.st_size, stat.st_mtime_ns)
    if export_dir.exists():
        for path in sorted(export_dir.rglob("*")):
            rel = path.relative_to(export_dir)
            # Browser download/upload scratch is disposable request state, not
            # part of the scientific dataset. It may appear/disappear while a
            # backup is running and must never be fingerprinted or copied.
            if rel.parts and rel.parts[0] in {DOWNLOAD_STAGING_DIRNAME, UPLOAD_STAGING_DIRNAME}:
                continue
            if path.is_symlink():
                raise BackupError(f"Symlinks are not supported in the export tree: {path}")
            if not path.is_file() or path.name == MAINTENANCE_FILENAME:
                continue
            stat = path.stat()
            result[f"exports/{rel.as_posix()}"] = (
                stat.st_size, stat.st_mtime_ns)
    return result



def _unique_sibling(path: Path) -> Path:
    """Return ``path`` or a numbered sibling without overwriting prior recovery data."""
    if not path.exists():
        return path
    for index in range(1, 10000):
        candidate = path.with_name(f"{path.name}-{index}")
        if not candidate.exists():
            return candidate
    raise BackupError(f"Could not reserve a rollback path beside {path}")


def _validate_db(path: Path):
    conn = sqlite3.connect(path)
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise BackupError(f"SQLite integrity_check failed: {integrity}")
        foreign = conn.execute("PRAGMA foreign_key_check").fetchall()
        if foreign:
            raise BackupError(f"SQLite foreign_key_check found {len(foreign)} violation(s)")
    finally:
        conn.close()


def _write_zip(stage: Path, output: Path, metadata: dict):
    files = []
    db_copy = stage / "database" / "cryopit.db"
    files.append({"path": DB_ARCHIVE_NAME, "size": db_copy.stat().st_size,
                  "sha256": _sha256(db_copy)})
    export_copy = stage / "exports"
    if export_copy.exists():
        for path in sorted(export_copy.rglob("*")):
            if path.is_symlink():
                raise BackupError(f"Symlinks are not permitted in backups: {path}")
            if path.is_file():
                rel = EXPORT_PREFIX + path.relative_to(export_copy).as_posix()
                files.append({"path": rel, "size": path.stat().st_size,
                              "sha256": _sha256(path)})
    manifest = {
        "format": "cryopit-backup-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "cryopit_version": __version__,
        "database": DB_ARCHIVE_NAME,
        "exports_prefix": EXPORT_PREFIX,
        "files": files,
        **metadata,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    temp_output = output.with_name(f".{output.name}.tmp-{os.getpid()}")
    try:
        with zipfile.ZipFile(temp_output, "w", compression=zipfile.ZIP_DEFLATED,
                             compresslevel=6, allowZip64=True) as zf:
            zf.write(db_copy, DB_ARCHIVE_NAME)
            for item in files[1:]:
                zf.write(export_copy / item["path"][len(EXPORT_PREFIX):], item["path"])
            zf.writestr(MANIFEST_NAME,
                        json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        fsync_file(temp_output)
        durable_replace(temp_output, output)
    finally:
        durable_unlink(temp_output, missing_ok=True)
    return manifest


def create_backup(output, *, db_path=DB_PATH, export_dir=EXPORT_DIR,
                  quiesce_seconds=2.0):
    db_path = Path(db_path).resolve()
    export_dir = Path(export_dir).resolve()
    output = Path(output).resolve()
    if not db_path.is_file():
        raise BackupError(f"CryoPit database does not exist: {db_path}")
    export_dir.mkdir(parents=True, exist_ok=True)
    try:
        output.relative_to(export_dir)
    except ValueError:
        pass
    else:
        raise BackupError("Write backups outside CRYOPIT_EXPORT_DIR to avoid recursive backups.")
    if output == db_path:
        raise BackupError("Backup output cannot overwrite the active SQLite database.")
    marker = export_dir / MAINTENANCE_FILENAME
    try:
        fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise BackupError(f"Maintenance mode is already active: {marker}") from exc
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"operation": "backup", "pid": os.getpid(),
                                 "started_at": datetime.now(timezone.utc).isoformat()}))
        # The maintenance marker rejects new state-changing HTTP requests. The
        # shared storage lock then waits for an archive/attachment operation
        # that was already in flight and excludes folder publication while the
        # snapshot is copied. On platforms without process locking, the source
        # fingerprint remains the final consistency backstop.
        with storage_lock(export_dir):
            if quiesce_seconds:
                time.sleep(max(0.0, quiesce_seconds))
            with tempfile.TemporaryDirectory(prefix="cryopit-backup-") as temp:
                stage = Path(temp)
                (stage / "database").mkdir()
                db_copy = stage / DB_ARCHIVE_NAME
                # Open the source BEFORE fingerprinting and keep it open through
                # the after-check. On an idle WAL database this connection may
                # create -wal/-shm sidecars; measuring only after it opens makes
                # those transient files part of a stable baseline rather than a
                # false "data changed" signal. Keeping them in the fingerprint
                # still detects a genuine concurrent WAL write.
                src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
                try:
                    # Force SQLite to initialize any WAL/SHM reader state
                    # before the baseline is measured. sqlite3.connect() alone
                    # is lazy; the first real read can create the sidecars.
                    src.execute("PRAGMA schema_version").fetchone()
                    before = _source_fingerprint(db_path, export_dir)
                    dst = sqlite3.connect(db_copy)
                    try:
                        src.backup(dst)
                    finally:
                        dst.close()
                    _validate_db(db_copy)
                    shutil.copytree(
                        export_dir, stage / "exports", dirs_exist_ok=True,
                        symlinks=True,
                        ignore=shutil.ignore_patterns(
                            MAINTENANCE_FILENAME, DOWNLOAD_STAGING_DIRNAME,
                            UPLOAD_STAGING_DIRNAME
                        ),
                    )
                    after = _source_fingerprint(db_path, export_dir)
                    if before != after:
                        raise BackupError(
                            "CryoPit data changed while the backup was being copied. "
                            "No backup was published; retry after active requests finish."
                        )
                finally:
                    src.close()
                return _write_zip(stage, output, {
                    "source_database_name": db_path.name,
                    "source_export_name": export_dir.name,
                    "quiesce_seconds": quiesce_seconds,
                })
    finally:
        durable_unlink(marker, missing_ok=True)


def _zip_member_digest(zf: zipfile.ZipFile, info: zipfile.ZipInfo) -> tuple[int, str]:
    h = hashlib.sha256()
    size = 0
    with zf.open(info, "r") as src:
        for chunk in iter(lambda: src.read(1024 * 1024), b""):
            size += len(chunk)
            h.update(chunk)
    return size, h.hexdigest()


def _extract_member(zf: zipfile.ZipFile, info: zipfile.ZipInfo, target: Path):
    target.parent.mkdir(parents=True, exist_ok=True)
    with zf.open(info, "r") as src, target.open("wb") as dst:
        shutil.copyfileobj(src, dst, length=1024 * 1024)


def verify_backup(bundle) -> dict:
    bundle = Path(bundle)
    if not bundle.is_file():
        raise BackupError(f"Backup does not exist: {bundle}")
    with zipfile.ZipFile(bundle) as zf:
        members = list(_safe_members(zf))
        try:
            manifest = json.loads(zf.read(MANIFEST_NAME))
        except KeyError as exc:
            raise BackupError("Backup manifest is missing") from exc
        if manifest.get("format") != "cryopit-backup-v1":
            raise BackupError("Unsupported CryoPit backup format")
        files = manifest.get("files")
        if not isinstance(files, list) or not all(isinstance(item, dict) for item in files):
            raise BackupError("Backup manifest files must be a list of objects")
        actual_files = {i.filename for i in members if not i.is_dir()}
        declared_files = {item.get("path") for item in files}
        if actual_files != declared_files | {MANIFEST_NAME}:
            raise BackupError("Backup contains undeclared or missing files")
        seen = set()
        for item in files:
            name = item.get("path")
            if not isinstance(name, str) or name in seen:
                raise BackupError("Backup manifest contains an invalid or duplicate path")
            if name != DB_ARCHIVE_NAME and not name.startswith(EXPORT_PREFIX):
                raise BackupError(f"Backup manifest contains an unsupported path: {name}")
            seen.add(name)
            try:
                info = zf.getinfo(name)
            except KeyError as exc:
                raise BackupError(f"Backup file is missing: {name}") from exc
            size, digest = _zip_member_digest(zf, info)
            if size != item.get("size"):
                raise BackupError(f"Backup size mismatch: {name}")
            if digest != item.get("sha256"):
                raise BackupError(f"Backup checksum mismatch: {name}")
        if DB_ARCHIVE_NAME not in seen:
            raise BackupError("Backup manifest does not contain the SQLite database")
        with tempfile.TemporaryDirectory(prefix="cryopit-verify-") as temp:
            db_copy = Path(temp) / "cryopit.db"
            _extract_member(zf, zf.getinfo(DB_ARCHIVE_NAME), db_copy)
            _validate_db(db_copy)
    return manifest


def restore_backup(bundle, *, db_path=DB_PATH, export_dir=EXPORT_DIR, force=False):
    bundle = Path(bundle).resolve()
    manifest = verify_backup(bundle)
    db_path = Path(db_path).resolve()
    export_dir = Path(export_dir).resolve()
    if not force and (db_path.exists() or (export_dir.exists() and any(export_dir.iterdir()))):
        raise BackupError("Restore targets are not empty; pass --force after taking a current backup.")

    parent = export_dir.parent
    parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    rollback_db = _unique_sibling(db_path.with_name(f"{db_path.name}.pre-restore-{stamp}"))
    rollback_exports = _unique_sibling(export_dir.with_name(f"{export_dir.name}.pre-restore-{stamp}"))

    db_path.parent.mkdir(parents=True, exist_ok=True)
    same_fs_db = db_path.with_name(f".{db_path.name}.restore-{os.getpid()}")
    same_fs_db.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(prefix=".cryopit-restore-", dir=parent) as temp:
        stage = Path(temp)
        staged_db = stage / "cryopit.db"
        staged_exports = stage / "exports"
        staged_exports.mkdir()
        allowed = {item["path"] for item in manifest["files"]}
        with zipfile.ZipFile(bundle) as zf:
            for info in _safe_members(zf):
                if info.is_dir() or info.filename == MANIFEST_NAME:
                    continue
                if info.filename not in allowed:
                    raise BackupError(f"Undeclared restore member: {info.filename}")
                if info.filename == DB_ARCHIVE_NAME:
                    _extract_member(zf, info, staged_db)
                elif info.filename.startswith(EXPORT_PREFIX) and not info.is_dir():
                    rel = PurePosixPath(info.filename).relative_to(PurePosixPath(EXPORT_PREFIX))
                    target = staged_exports.joinpath(*rel.parts)
                    _extract_member(zf, info, target)
        _validate_db(staged_db)
        shutil.copy2(staged_db, same_fs_db)

        moved_db = moved_exports = False
        try:
            for suffix in ("-wal", "-shm"):
                Path(str(db_path) + suffix).unlink(missing_ok=True)
            if db_path.exists():
                os.replace(db_path, rollback_db)
                moved_db = True
            if export_dir.exists():
                os.replace(export_dir, rollback_exports)
                moved_exports = True
            os.replace(same_fs_db, db_path)
            os.replace(staged_exports, export_dir)
        except Exception:
            db_path.unlink(missing_ok=True)
            if export_dir.exists():
                shutil.rmtree(export_dir, ignore_errors=True)
            if moved_db and rollback_db.exists():
                os.replace(rollback_db, db_path)
            if moved_exports and rollback_exports.exists():
                os.replace(rollback_exports, export_dir)
            raise
        finally:
            same_fs_db.unlink(missing_ok=True)
    return {"ok": True, "manifest": manifest,
            "previous_database": str(rollback_db) if rollback_db.exists() else None,
            "previous_exports": str(rollback_exports) if rollback_exports.exists() else None}


def _parser():
    parser = argparse.ArgumentParser(description="CryoPit backup and restore tools")
    parser.add_argument("--db", default=DB_PATH, help="SQLite database path")
    parser.add_argument("--exports", default=EXPORT_DIR, help="export directory")
    commands = parser.add_subparsers(dest="command", required=True)
    backup = commands.add_parser("backup", help="create a checksummed dataset backup")
    backup.add_argument("--output", required=True)
    backup.add_argument("--quiesce-seconds", type=float, default=2.0)
    verify = commands.add_parser("verify", help="verify checksums and SQLite integrity")
    verify.add_argument("bundle")
    restore = commands.add_parser("restore", help="restore a verified backup")
    restore.add_argument("bundle")
    restore.add_argument("--force", action="store_true")
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    try:
        if args.command == "backup":
            manifest = create_backup(args.output, db_path=args.db, export_dir=args.exports,
                                     quiesce_seconds=args.quiesce_seconds)
            print(f"Backup created: {Path(args.output).resolve()}")
            print(f"Files: {len(manifest['files'])}")
        elif args.command == "verify":
            manifest = verify_backup(args.bundle)
            print(f"Backup valid: {Path(args.bundle).resolve()}")
            print(f"Created: {manifest['created_at']}")
        else:
            result = restore_backup(args.bundle, db_path=args.db, export_dir=args.exports,
                                    force=args.force)
            print(f"Restore complete: {Path(args.db).resolve()}")
            if result["previous_database"] or result["previous_exports"]:
                print("Previous data was retained with a .pre-restore timestamp.")
    except BackupError as exc:
        raise SystemExit(f"CryoPit operation failed: {exc}") from exc


if __name__ == "__main__":
    main()
