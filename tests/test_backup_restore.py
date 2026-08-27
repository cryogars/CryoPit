"""Stage 12 backup/verify/restore tests."""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cryopit.ops import (BackupError, MAINTENANCE_FILENAME, create_backup,
                         restore_backup, verify_backup)

TMP = Path(tempfile.mkdtemp(prefix="cryopit-stage12-ops-"))


def source_dataset(name="source"):
    root = TMP / name
    db = root / "cryopit.db"
    exports = root / "exports"
    exports.mkdir(parents=True)
    conn = sqlite3.connect(db)
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("CREATE TABLE sites(site_id TEXT PRIMARY KEY, pit_id TEXT NOT NULL)")
        conn.execute("INSERT INTO sites VALUES('site-1','PIT-001')")
        conn.commit()
    finally:
        conn.close()
    (exports / "WY2026_PIT-001" / "uploads" / "pitwall").mkdir(parents=True)
    (exports / "WY2026_PIT-001" / "uploads" / "pitwall" / "photo.jpg").write_bytes(b"field-photo")
    return db, exports


def test_backup_verify_and_restore_round_trip():
    db, exports = source_dataset("roundtrip")
    bundle = TMP / "roundtrip.zip"
    manifest = create_backup(bundle, db_path=db, export_dir=exports, quiesce_seconds=0)
    assert bundle.is_file()
    assert not (exports / MAINTENANCE_FILENAME).exists()
    assert manifest["format"] == "cryopit-backup-v1"
    verified = verify_backup(bundle)
    assert verified["files"] == manifest["files"]

    restored_db = TMP / "restored" / "cryopit.db"
    restored_exports = TMP / "restored" / "exports"
    result = restore_backup(bundle, db_path=restored_db, export_dir=restored_exports)
    assert result["ok"]
    conn = sqlite3.connect(restored_db)
    try:
        assert conn.execute("SELECT pit_id FROM sites").fetchone()[0] == "PIT-001"
    finally:
        conn.close()
    assert (restored_exports / "WY2026_PIT-001" / "uploads" / "pitwall" / "photo.jpg").read_bytes() == b"field-photo"



def test_backup_excludes_download_staging_scratch():
    db, exports = source_dataset("download-scratch")
    scratch = exports / ".download-staging"
    scratch.mkdir()
    (scratch / "download-active.zip.part").write_bytes(b"scratch-only")
    bundle = TMP / "download-scratch.zip"
    manifest = create_backup(bundle, db_path=db, export_dir=exports, quiesce_seconds=0)
    declared = {item["path"] for item in manifest["files"]}
    assert not any(".download-staging" in path for path in declared)
    with zipfile.ZipFile(bundle) as zf:
        assert not any(".download-staging" in name for name in zf.namelist())
    # Backup must not consume or delete a live download owned by the HTTP path.
    assert (scratch / "download-active.zip.part").read_bytes() == b"scratch-only"


def test_backup_excludes_upload_staging_scratch():
    db, exports = source_dataset("upload-scratch")
    scratch = exports / ".upload-staging"
    scratch.mkdir()
    staged = scratch / "upload-inflight.upload.part"
    staged.write_bytes(b"untrusted-inflight-upload")
    bundle = TMP / "upload-scratch.zip"
    manifest = create_backup(bundle, db_path=db, export_dir=exports, quiesce_seconds=0)
    declared = {item["path"] for item in manifest["files"]}
    assert not any(".upload-staging" in path for path in declared)
    with zipfile.ZipFile(bundle) as zf:
        assert not any(".upload-staging" in name for name in zf.namelist())
    # Backup must not consume or delete an active inbound upload scratch file.
    assert staged.read_bytes() == b"untrusted-inflight-upload"

def test_idle_wal_backup_ignores_transient_sidecars():
    root = TMP / "idle-wal"
    db = root / "cryopit.db"
    exports = root / "exports"
    exports.mkdir(parents=True)
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("PRAGMA journal_mode=WAL").fetchone()[0].lower() == "wal"
        conn.execute("CREATE TABLE sites(site_id TEXT PRIMARY KEY, pit_id TEXT NOT NULL)")
        conn.execute("INSERT INTO sites VALUES('site-wal','PIT-WAL')")
        conn.commit()
    finally:
        conn.close()
    # SQLite normally removes these when the final connection closes. Opening
    # the read-only backup source can recreate them; that is not a data change.
    assert not Path(str(db) + "-wal").exists()
    assert not Path(str(db) + "-shm").exists()
    bundle = TMP / "idle-wal.zip"
    create_backup(bundle, db_path=db, export_dir=exports, quiesce_seconds=0)
    assert bundle.is_file()
    verify_backup(bundle)


def test_restore_refuses_nonempty_targets_without_force_and_keeps_rollback():
    db, exports = source_dataset("force-source")
    bundle = TMP / "force.zip"
    create_backup(bundle, db_path=db, export_dir=exports, quiesce_seconds=0)

    target_db, target_exports = source_dataset("force-target")
    conn = sqlite3.connect(target_db)
    try:
        conn.execute("UPDATE sites SET pit_id='OLD-PIT'")
        conn.commit()
    finally:
        conn.close()
    try:
        restore_backup(bundle, db_path=target_db, export_dir=target_exports)
    except BackupError as exc:
        assert "not empty" in str(exc)
    else:
        raise AssertionError("nonempty restore target was overwritten without --force")

    result = restore_backup(bundle, db_path=target_db, export_dir=target_exports, force=True)
    assert result["previous_database"] and Path(result["previous_database"]).exists()
    assert result["previous_exports"] and Path(result["previous_exports"]).exists()
    conn = sqlite3.connect(target_db)
    try:
        assert conn.execute("SELECT pit_id FROM sites").fetchone()[0] == "PIT-001"
    finally:
        conn.close()


def test_tampered_bundle_is_rejected():
    db, exports = source_dataset("tamper")
    bundle = TMP / "tamper.zip"
    create_backup(bundle, db_path=db, export_dir=exports, quiesce_seconds=0)
    tampered = TMP / "tampered.zip"
    with zipfile.ZipFile(bundle) as src, zipfile.ZipFile(tampered, "w") as dst:
        for info in src.infolist():
            data = src.read(info)
            if info.filename.endswith("photo.jpg"):
                data += b"tampered"
            dst.writestr(info, data)
    try:
        verify_backup(tampered)
    except BackupError as exc:
        assert "mismatch" in str(exc)
    else:
        raise AssertionError("tampered backup passed verification")


def test_undeclared_and_traversal_members_are_rejected():
    db, exports = source_dataset("unsafe")
    bundle = TMP / "unsafe-base.zip"
    create_backup(bundle, db_path=db, export_dir=exports, quiesce_seconds=0)
    extra = TMP / "unsafe-extra.zip"
    with zipfile.ZipFile(bundle) as src, zipfile.ZipFile(extra, "w") as dst:
        for info in src.infolist():
            dst.writestr(info, src.read(info))
        dst.writestr("exports/undeclared.txt", b"no")
    try:
        verify_backup(extra)
    except BackupError as exc:
        assert "undeclared" in str(exc).lower()
    else:
        raise AssertionError("undeclared file was accepted")

    traversal = TMP / "unsafe-traversal.zip"
    with zipfile.ZipFile(bundle) as src, zipfile.ZipFile(traversal, "w") as dst:
        for info in src.infolist():
            dst.writestr(info, src.read(info))
        dst.writestr("../outside.txt", b"no")
    try:
        verify_backup(traversal)
    except BackupError as exc:
        assert "unsafe path" in str(exc).lower()
    else:
        raise AssertionError("path traversal was accepted")


def test_backup_refuses_symlinks_in_export_tree():
    db, exports = source_dataset("symlink")
    outside = TMP / "outside-secret.txt"
    outside.write_text("secret", encoding="utf-8")
    link = exports / "linked-secret.txt"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        return  # platform policy does not permit creating a test symlink
    try:
        create_backup(TMP / "symlink.zip", db_path=db, export_dir=exports,
                      quiesce_seconds=0)
    except BackupError as exc:
        assert "symlink" in str(exc).lower()
    else:
        raise AssertionError("export-tree symlink was included in a backup")


def test_backup_refuses_output_inside_export_tree():
    db, exports = source_dataset("recursive")
    try:
        create_backup(exports / "backup.zip", db_path=db, export_dir=exports,
                      quiesce_seconds=0)
    except BackupError as exc:
        assert "outside" in str(exc)
    else:
        raise AssertionError("recursive backup destination was accepted")


TESTS = [v for k, v in list(globals().items()) if k.startswith("test_")]
if __name__ == "__main__":
    failures = 0
    for test in TESTS:
        try:
            test()
            print("PASS", test.__name__)
        except Exception as exc:
            failures += 1
            print("FAIL", test.__name__, repr(exc))
    if failures:
        raise SystemExit(f"{failures} Stage 12 operations tests failed")
    print(f"{len(TESTS)} Stage 12 operations tests passed")
