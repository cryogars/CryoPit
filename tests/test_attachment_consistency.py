"""Attachment filesystem/SQLite compensation and recovery tests."""
from __future__ import annotations

import hashlib
import io
import importlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import types
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TMP = Path(tempfile.mkdtemp(prefix="cryopit-stage9-"))
os.environ["CRYOPIT_DB_PATH"] = str(TMP / "stage9.db")
os.environ["CRYOPIT_EXPORT_DIR"] = str(TMP / "exports")

PKG = "_cryopit_stage9_test"
pkg = types.ModuleType(PKG)
pkg.__path__ = [str(ROOT / "cryopit")]
sys.modules[PKG] = pkg


class _Blueprint:
    def __init__(self, *_a, **_k): pass
    @staticmethod
    def _decorator(*_a, **_k): return lambda fn: fn
    get = post = before_app_request = _decorator


request = types.SimpleNamespace(form={}, files={}, headers={}, path="", method="POST",
                                get_json=lambda silent=True: None)
flask_stub = types.ModuleType("flask")
flask_stub.Blueprint = _Blueprint
flask_stub.Response = lambda body=None, **kwargs: (body, kwargs)
flask_stub.abort = lambda code, description=None: (_ for _ in ()).throw(RuntimeError(code))
flask_stub.jsonify = lambda obj=None, **kwargs: obj if obj is not None else kwargs
flask_stub.request = request
flask_stub.has_request_context = lambda: False
sys.modules["flask"] = flask_stub

config = importlib.import_module(f"{PKG}.config")
db = importlib.import_module(f"{PKG}.db")
repository = importlib.import_module(f"{PKG}.repository")
lifecycle = importlib.import_module(f"{PKG}.archive_lifecycle")
storage = importlib.import_module(f"{PKG}.attachment_storage")
web = importlib.import_module(f"{PKG}.web")


class Upload:
    def __init__(self, data, filename):
        self.data, self.filename = data, filename
        self.stream = io.BytesIO(data)
    def read(self, size=-1):
        return self.stream.read(size)


class FaultConn:
    def __init__(self, real, match): self.real, self.match, self.fired = real, match, False
    def execute(self, sql, *args):
        if not self.fired and self.match in " ".join(sql.split()):
            self.fired = True
            raise sqlite3.OperationalError("fault injected")
        return self.real.execute(sql, *args)
    def __getattr__(self, name): return getattr(self.real, name)


def reset():
    for p in (Path(os.environ["CRYOPIT_DB_PATH"]), Path(os.environ["CRYOPIT_EXPORT_DIR"])):
        if p.is_dir(): shutil.rmtree(p)
        elif p.exists(): p.unlink()
    db.init_db()


def payload(pid="S9", manifest=None):
    p = {"meta": {"pit_id": pid, "campaign": "WY2026", "date": "2026-02-10",
                  "location": "Grand Mesa", "site": "Ridge", "total_depth": 100,
                  "recorded_by": "A", "surveyors": "B", "no_instruments": False,
                  "no_tasks": False},
         "weather": {}, "ground": {}, "temperature": [], "density": [], "lwc": [],
         "stratigraphy": [], "ssa": [], "ssa_calibration": {}, "instruments": []}
    if manifest is not None: p["attachment_manifest"] = manifest
    return p


def render_ok(_payload): return b"\x89PNG\r\n\x1a\nTEST", b"%PDF-1.4\nTEST"


def manifest(data, category="pitwall", filename="wall.jpg", top=None, bottom=None):
    return {"queue_id": str(uuid.uuid4()), "category": category, "filename": filename,
            "mime_type": "image/jpeg", "size_bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(), "top_cm": top, "bottom_cm": bottom}


def archive_one(data=b"\xff\xd8\xffSTAGE9", item=None):
    item = item or manifest(data)
    result = lifecycle.archive_payload(payload(manifest=[item]), None, render_ok)
    assert result["ok"]
    return result["site_id"], item


def call_attach(site_id, item, data):
    request.form = {"category": item["category"], "queue_id": item["queue_id"]}
    if item.get("top_cm") is not None: request.form["top_cm"] = str(item["top_cm"])
    if item.get("bottom_cm") is not None: request.form["bottom_cm"] = str(item["bottom_cm"])
    request.files = {"file": Upload(data, item["filename"])}
    result = web.api_attach(site_id)
    return result if isinstance(result, tuple) else (result, 200)


def site_folder(site_id):
    conn = sqlite3.connect(os.environ["CRYOPIT_DB_PATH"])
    try: folder = conn.execute("SELECT export_folder FROM sites WHERE site_id=?", (site_id,)).fetchone()[0]
    finally: conn.close()
    return Path(os.environ["CRYOPIT_EXPORT_DIR"]) / folder


def prepare_staged(site_id, item, data, filename="reserved.jpg"):
    digest = hashlib.sha256(data).hexdigest()
    rel = storage.target_relpath(item["category"], item.get("top_cm"), item.get("bottom_cm"), filename)
    conn = sqlite3.connect(os.environ["CRYOPIT_DB_PATH"])
    try:
        folder = conn.execute("SELECT export_folder FROM sites WHERE site_id=?", (site_id,)).fetchone()[0]
        conn.execute("""UPDATE attachment_uploads SET publication_state='reserved',
                        target_relpath=?,server_sha256=? WHERE queue_id=?""",
                     (rel, digest, item["queue_id"]))
        conn.commit()
    finally: conn.close()
    staged = storage.write_staged_file(folder, item["queue_id"], data)
    conn = sqlite3.connect(os.environ["CRYOPIT_DB_PATH"])
    try:
        conn.execute("UPDATE attachment_uploads SET publication_state='staged',staged_relpath=? WHERE queue_id=?",
                     (staged, item["queue_id"]))
        conn.commit()
    finally: conn.close()
    return folder, staged, rel


def test_legacy_database_migrates_before_new_index_is_created():
    # CREATE TABLE IF NOT EXISTS cannot add columns to an existing table, so
    # indexes involving later attachment columns must be created by _migrate afterward.
    db_path = Path(os.environ["CRYOPIT_DB_PATH"])
    exports = Path(os.environ["CRYOPIT_EXPORT_DIR"])
    if db_path.exists(): db_path.unlink()
    if exports.exists(): shutil.rmtree(exports)
    conn = sqlite3.connect(db_path)
    try:
        legacy_sql = (ROOT / "cryopit" / "schema.sql").read_text()
        for line in (
            "    storage_status TEXT DEFAULT 'stored',\n",
            "    storage_error TEXT,\n",
            "    pending_delete INTEGER NOT NULL DEFAULT 0,\n",
            "    trash_relpath TEXT\n",
            "    publication_state TEXT,\n",
            "    staged_relpath TEXT,\n",
            "    target_relpath TEXT,\n",
            "    server_sha256 TEXT,\n",
        ):
            legacy_sql = legacy_sql.replace(line, "")
        # Removing the final attachment column leaves the preceding timestamp
        # with a comma; restore the legacy closing form.
        legacy_sql = legacy_sql.replace(
            "    uploaded_at TEXT DEFAULT (datetime('now')),\n);",
            "    uploaded_at TEXT DEFAULT (datetime('now'))\n);")
        legacy_sql = legacy_sql.replace(
            "    last_error TEXT,\n    created_at TEXT",
            "    last_error TEXT,\n    created_at TEXT")
        conn.executescript(legacy_sql)
        conn.commit()
    finally: conn.close()
    db.init_db()
    conn = sqlite3.connect(db_path)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(attachment_uploads)")}
        assert "target_relpath" in cols
        assert conn.execute("SELECT name FROM sqlite_master WHERE type='index' AND name='idx_attachment_uploads_target'").fetchone()
    finally: conn.close()


def test_active_reservation_is_not_reset_by_concurrent_reconciliation():
    reset(); data = b"\xff\xd8\xffACTIVE"; site_id, item = archive_one(data)
    conn = sqlite3.connect(os.environ["CRYOPIT_DB_PATH"])
    try:
        conn.execute("UPDATE attachment_uploads SET publication_state='reserved',target_relpath='uploads/pitwall/x.jpg',server_sha256=? WHERE queue_id=?",
                     (hashlib.sha256(data).hexdigest(), item["queue_id"]))
        conn.commit()
    finally: conn.close()
    result = storage.recover_upload(item["queue_id"], "local")
    assert result["in_progress"]
    conn = sqlite3.connect(os.environ["CRYOPIT_DB_PATH"])
    try: assert conn.execute("SELECT publication_state FROM attachment_uploads").fetchone() == ("reserved",)
    finally: conn.close()


def test_stale_reservation_is_released_for_browser_retry():
    reset(); data = b"\xff\xd8\xffSTALE"; site_id, item = archive_one(data)
    conn = sqlite3.connect(os.environ["CRYOPIT_DB_PATH"])
    try:
        conn.execute("UPDATE attachment_uploads SET publication_state='reserved',target_relpath='uploads/pitwall/x.jpg',server_sha256=?,updated_at=datetime('now','-10 minutes') WHERE queue_id=?",
                     (hashlib.sha256(data).hexdigest(), item["queue_id"]))
        conn.commit()
    finally: conn.close()
    result = storage.recover_upload(item["queue_id"], "local")
    assert result["retry"]
    conn = sqlite3.connect(os.environ["CRYOPIT_DB_PATH"])
    try: assert conn.execute("SELECT publication_state,target_relpath FROM attachment_uploads").fetchone() == (None, None)
    finally: conn.close()


def test_schema_has_recovery_journal_columns():
    reset()
    conn = sqlite3.connect(os.environ["CRYOPIT_DB_PATH"])
    try:
        ac = {r[1] for r in conn.execute("PRAGMA table_info(attachments)")}
        uc = {r[1] for r in conn.execute("PRAGMA table_info(attachment_uploads)")}
        assert {"storage_status", "storage_error", "pending_delete", "trash_relpath"} <= ac
        assert {"publication_state", "staged_relpath", "target_relpath", "server_sha256"} <= uc
    finally: conn.close()


def test_normal_upload_publishes_and_clears_journal():
    reset(); data = b"\xff\xd8\xffNORMAL"; site_id, item = archive_one(data)
    response, code = call_attach(site_id, item, data)
    assert code == 200 and response["ok"]
    conn = sqlite3.connect(os.environ["CRYOPIT_DB_PATH"])
    try:
        row = conn.execute("SELECT status,publication_state,staged_relpath,target_relpath FROM attachment_uploads").fetchone()
        att = conn.execute("SELECT filename,storage_status FROM attachments").fetchone()
        assert row == ("stored", None, None, None) and att[1] == "stored"
        assert (site_folder(site_id) / "uploads" / "pitwall" / att[0]).is_file()
        assert not list((site_folder(site_id) / ".attachment-staging").glob("*.part"))
        inbound = Path(os.environ["CRYOPIT_EXPORT_DIR"]) / ".upload-staging"
        assert not inbound.exists() or not list(inbound.glob("*.upload.part"))
    finally: conn.close()



def test_rejected_manifest_mismatch_removes_inbound_upload_scratch():
    reset(); data = b"\xff\xd8\xffMISMATCH"; site_id, item = archive_one(data)
    wrong = dict(item); wrong["category"] = "sheet"
    response, code = call_attach(site_id, wrong, data)
    assert code == 409 and not response["ok"]
    scratch = Path(os.environ["CRYOPIT_EXPORT_DIR"]) / ".upload-staging"
    assert not scratch.exists() or not list(scratch.glob("*.upload.part"))

def test_crash_after_publish_before_db_commit_recovers():
    reset(); data = b"\xff\xd8\xffCRASH"; site_id, item = archive_one(data)
    folder, staged_rel, target_rel = prepare_staged(site_id, item, data)
    stage = Path(os.environ["CRYOPIT_EXPORT_DIR"]) / folder / staged_rel
    target = Path(os.environ["CRYOPIT_EXPORT_DIR"]) / folder / target_rel
    target.parent.mkdir(parents=True, exist_ok=True); os.replace(stage, target)
    result = storage.recover_upload(item["queue_id"], "local")
    assert result["stored"] and target.is_file()
    conn = sqlite3.connect(os.environ["CRYOPIT_DB_PATH"])
    try: assert conn.execute("SELECT status FROM attachment_uploads").fetchone() == ("stored",)
    finally: conn.close()


def test_db_failure_after_publish_moves_file_back_to_stage():
    reset(); data = b"\xff\xd8\xffDBFAIL"; site_id, item = archive_one(data)
    folder, staged_rel, target_rel = prepare_staged(site_id, item, data)
    original_get_conn = storage.get_conn
    storage.get_conn = lambda: FaultConn(original_get_conn(), "INSERT OR IGNORE INTO attachments")
    try:
        try: storage.recover_upload(item["queue_id"], "local")
        except sqlite3.OperationalError: pass
        else: raise AssertionError("fault did not fire")
    finally: storage.get_conn = original_get_conn
    root = Path(os.environ["CRYOPIT_EXPORT_DIR"]) / folder
    assert (root / staged_rel).is_file() and not (root / target_rel).exists()
    conn = sqlite3.connect(os.environ["CRYOPIT_DB_PATH"])
    try:
        assert conn.execute("SELECT COUNT(*) FROM attachments").fetchone()[0] == 0
        assert conn.execute("SELECT publication_state FROM attachment_uploads").fetchone() == ("staged",)
    finally: conn.close()
    assert storage.recover_upload(item["queue_id"], "local")["stored"]


def test_missing_file_can_be_repaired_with_same_queue_id():
    reset(); data = b"\xff\xd8\xffREPAIR"; site_id, item = archive_one(data)
    response, _ = call_attach(site_id, item, data)
    root = site_folder(site_id)
    (root / "uploads" / "pitwall" / response["filename"]).unlink()
    storage.reconcile_site(site_id, "local", full=True)
    repaired, code = call_attach(site_id, item, data)
    assert code == 200 and repaired["ok"] and not repaired.get("duplicate")
    conn = sqlite3.connect(os.environ["CRYOPIT_DB_PATH"])
    try:
        assert conn.execute("SELECT storage_status FROM attachments").fetchone() == ("stored",)
        assert conn.execute("SELECT status FROM attachment_uploads").fetchone() == ("stored",)
    finally: conn.close()
    assert (root / "uploads" / "pitwall" / repaired["filename"]).is_file()


def test_reconciliation_quarantines_orphans_and_marks_missing():
    reset(); data = b"\xff\xd8\xffSCAN"; site_id, item = archive_one(data)
    response, _ = call_attach(site_id, item, data)
    root = site_folder(site_id)
    orphan = root / "uploads" / "pitwall" / "manual-orphan.jpg"
    orphan.write_bytes(b"orphan")
    stored = root / "uploads" / "pitwall" / response["filename"]
    stored.unlink()
    report = storage.reconcile_site(site_id, "local", full=True, stale_seconds=0)
    assert report["missing"] and "uploads/pitwall/manual-orphan.jpg" in report["quarantined"]
    assert not orphan.exists()
    assert list((root / ".attachment-orphans").rglob("manual-orphan.jpg"))
    conn = sqlite3.connect(os.environ["CRYOPIT_DB_PATH"])
    try: assert conn.execute("SELECT storage_status FROM attachments").fetchone() == ("missing",)
    finally: conn.close()
    assert web._existing_uploads(site_id) == {}


def test_reconciliation_removes_only_unreferenced_stale_temp_files():
    reset(); site_id, _ = archive_one()
    root = site_folder(site_id); stage = root / ".attachment-staging"; stage.mkdir()
    stale = stage / "abandoned.part"; stale.write_bytes(b"x"); os.utime(stale, (1, 1))
    report = storage.reconcile_site(site_id, "local", full=True, stale_seconds=0)
    assert not stale.exists() and ".attachment-staging/abandoned.part" in report["removed_temps"]


def test_safe_delete_cancels_manifest_and_removes_file():
    reset(); data = b"\xff\xd8\xffDELETE"; site_id, item = archive_one(data)
    response, _ = call_attach(site_id, item, data)
    result = storage.begin_delete(site_id, response["attachment_id"], "local")
    assert result["deleted"]
    conn = sqlite3.connect(os.environ["CRYOPIT_DB_PATH"])
    try:
        assert conn.execute("SELECT COUNT(*) FROM attachments").fetchone()[0] == 0
        assert conn.execute("SELECT status,attachment_id FROM attachment_uploads").fetchone() == ("cancelled", None)
    finally: conn.close()
    assert not list((site_folder(site_id) / "uploads").rglob(response["filename"]))


def test_delete_db_failure_leaves_recoverable_trash():
    reset(); data = b"\xff\xd8\xffDELFAIL"; site_id, item = archive_one(data)
    response, _ = call_attach(site_id, item, data)
    original_get_conn = storage.get_conn
    storage.get_conn = lambda: FaultConn(original_get_conn(), "DELETE FROM attachments")
    try:
        try: storage.begin_delete(site_id, response["attachment_id"], "local")
        except sqlite3.OperationalError: pass
        else: raise AssertionError("delete fault did not fire")
    finally: storage.get_conn = original_get_conn
    root = site_folder(site_id)
    conn = sqlite3.connect(os.environ["CRYOPIT_DB_PATH"])
    try:
        pending, trash_rel = conn.execute("SELECT pending_delete,trash_relpath FROM attachments").fetchone()
        assert pending == 1 and (root / trash_rel).is_file()
    finally: conn.close()
    result = storage.recover_delete(site_id, response["attachment_id"], "local")
    assert result["deleted"] and not (root / trash_rel).exists()


def test_delete_enforces_owner():
    reset(); data = b"\xff\xd8\xffOWNER"; site_id, item = archive_one(data)
    response, _ = call_attach(site_id, item, data)
    assert storage.begin_delete(site_id, response["attachment_id"], "someone-else")["missing"]


if __name__ == "__main__":
    tests = sorted((n, f) for n, f in globals().items() if n.startswith("test_") and callable(f))
    for name, fn in tests:
        fn(); print("PASS", name)
    print(f"{len(tests)} attachment-consistency tests passed")
