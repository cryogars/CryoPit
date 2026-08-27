"""Stage 12 shared storage lock and crash-durability tests."""
from __future__ import annotations

import builtins
import importlib
import logging
import os
import sqlite3
import sys
import tempfile
import threading
import time
import types
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
TMP = Path(tempfile.mkdtemp(prefix="cryopit-stage12-storage-"))
os.environ["CRYOPIT_DB_PATH"] = str(TMP / "storage.db")
os.environ["CRYOPIT_EXPORT_DIR"] = str(TMP / "exports")

# archive_lifecycle imports repository -> auth. The storage tests do not need a
# live Flask app, so provide the same tiny request-context seam used by the
# earlier integration suites.
flask_stub = types.ModuleType("flask")
flask_stub.has_request_context = lambda: False
flask_stub.request = types.SimpleNamespace(headers={})
flask_stub.abort = lambda code, description=None: (_ for _ in ()).throw(RuntimeError(code))
sys.modules.setdefault("flask", flask_stub)
sys.path.insert(0, str(ROOT))

from cryopit import archive_lifecycle as archive
from cryopit import attachment_storage as attachments
from cryopit import db
from cryopit import storage_lifecycle as lifecycle


def reset():
    db_path = Path(os.environ["CRYOPIT_DB_PATH"])
    export = Path(os.environ["CRYOPIT_EXPORT_DIR"])
    if db_path.exists():
        db_path.unlink()
    if export.exists():
        import shutil
        shutil.rmtree(export)
    db.init_db()


def test_archive_and_attachment_operations_share_one_lock():
    reset()
    archive_entered = threading.Event()
    attachment_attempted = threading.Event()
    attachment_entered = threading.Event()
    release_archive = threading.Event()

    def hold_archive():
        with archive.archive_lock():
            archive_entered.set()
            assert release_archive.wait(3)

    def enter_attachment():
        assert archive_entered.wait(3)
        attachment_attempted.set()
        with attachments.attachment_lock():
            attachment_entered.set()

    first = threading.Thread(target=hold_archive, daemon=True)
    second = threading.Thread(target=enter_attachment, daemon=True)
    first.start(); second.start()
    assert archive_entered.wait(3)
    assert attachment_attempted.wait(3)
    # The attachment must remain outside until the archive releases the shared
    # lifecycle lock. Separate Stage 9 lock files would fail this assertion.
    assert not attachment_entered.wait(0.15)
    release_archive.set()
    assert attachment_entered.wait(3)
    first.join(3); second.join(3)
    assert not first.is_alive() and not second.is_alive()
    assert (Path(os.environ["CRYOPIT_EXPORT_DIR"]) / ".locks" / "storage.lock").is_file()
    assert not (Path(os.environ["CRYOPIT_EXPORT_DIR"]) / ".locks" / "archive.lock").exists()
    assert not (Path(os.environ["CRYOPIT_EXPORT_DIR"]) / ".locks" / "attachments.lock").exists()


def test_stale_export_folder_is_rejected_before_any_old_path_is_created():
    reset()
    site_id = "site-stale-folder"
    conn = sqlite3.connect(os.environ["CRYOPIT_DB_PATH"])
    try:
        conn.execute(
            "INSERT INTO sites(site_id,pit_id,owner,raw_json,export_folder,pending_export_folder) "
            "VALUES(?,?,?,?,?,NULL)",
            (site_id, "PIT-1", "local", "{}", "new-folder"),
        )
        conn.commit()
    finally:
        conn.close()
    (Path(os.environ["CRYOPIT_EXPORT_DIR"]) / "new-folder").mkdir(parents=True)

    try:
        attachments.write_staged_file(
            "old-folder", "queue-stale", b"photo",
            site_id=site_id, owner="local",
        )
    except attachments.AttachmentConflict as exc:
        assert "moved" in str(exc)
    else:
        raise AssertionError("stale export folder was accepted")
    assert not (Path(os.environ["CRYOPIT_EXPORT_DIR"]) / "old-folder").exists()


def test_missing_process_lock_emits_one_visible_warning():
    reset()
    real_import = builtins.__import__

    def import_without_fcntl(name, *args, **kwargs):
        if name == "fcntl":
            raise ImportError("simulated platform without flock")
        return real_import(name, *args, **kwargs)

    lifecycle._WARNED_PROCESS_LOCK = False
    records = []

    class Capture(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = Capture()
    logger = logging.getLogger(lifecycle.__name__)
    logger.addHandler(handler)
    old_level = logger.level
    logger.setLevel(logging.WARNING)
    try:
        with mock.patch("builtins.__import__", side_effect=import_without_fcntl):
            with lifecycle.storage_lock(Path(os.environ["CRYOPIT_EXPORT_DIR"])):
                pass
            with lifecycle.storage_lock(Path(os.environ["CRYOPIT_EXPORT_DIR"])):
                pass
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)
    messages = [r.getMessage() for r in records if "Cross-process storage locking" in r.getMessage()]
    assert len(messages) == 1
    assert "multiple workers" in messages[0]


def test_durable_replace_syncs_the_published_directory_entry():
    root = TMP / "durability"
    root.mkdir(parents=True, exist_ok=True)
    source = root / "source.tmp"
    target = root / "target.dat"
    source.write_bytes(b"durable")
    synced = []
    original = lifecycle.fsync_directory
    lifecycle.fsync_directory = lambda path: synced.append(Path(path))
    try:
        lifecycle.durable_replace(source, target)
    finally:
        lifecycle.fsync_directory = original
    assert target.read_bytes() == b"durable"
    assert root in synced


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
        raise SystemExit(f"{failures} Stage 12 storage-lifecycle tests failed")
    print(f"{len(TESTS)} Stage 12 storage-lifecycle tests passed")
