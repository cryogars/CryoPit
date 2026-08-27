"""Server-side expected-photo manifest and idempotent upload tests.

Runs without Flask by importing the real HTTP module behind a tiny decorator /
request stub.  Database, archive lifecycle, filesystem writes, upload route and
retry logic are production code.
"""
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
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
TMP = Path(tempfile.mkdtemp(prefix="cryopit-stage8-"))
os.environ["CRYOPIT_DB_PATH"] = str(TMP / "stage8.db")
os.environ["CRYOPIT_EXPORT_DIR"] = str(TMP / "exports")

PKG = "_cryopit_stage8_test"
pkg = types.ModuleType(PKG)
pkg.__path__ = [str(ROOT / "cryopit")]
sys.modules[PKG] = pkg


class _Blueprint:
    def __init__(self, *_a, **_k):
        pass

    @staticmethod
    def _decorator(*_a, **_k):
        return lambda fn: fn

    get = post = before_app_request = _decorator


class _Abort(RuntimeError):
    pass


def _abort(code, description=None):
    raise _Abort(f"{code}: {description}")


request = types.SimpleNamespace(
    form={}, files={}, headers={}, path="", method="POST",
    get_json=lambda silent=True: None,
)
flask_stub = types.ModuleType("flask")
flask_stub.Blueprint = _Blueprint
flask_stub.Response = lambda body=None, **kwargs: (body, kwargs)
flask_stub.abort = _abort
flask_stub.jsonify = lambda obj=None, **kwargs: obj if obj is not None else kwargs
flask_stub.request = request
flask_stub.has_request_context = lambda: False
sys.modules["flask"] = flask_stub

config = importlib.import_module(f"{PKG}.config")
db = importlib.import_module(f"{PKG}.db")
repository = importlib.import_module(f"{PKG}.repository")
lifecycle = importlib.import_module(f"{PKG}.archive_lifecycle")
web = importlib.import_module(f"{PKG}.web")


class Upload:
    def __init__(self, data: bytes, filename: str):
        self._data = data
        self.filename = filename
        self.stream = io.BytesIO(data)

    def read(self, size=-1):
        return self.stream.read(size)


def payload(pid="PHOTO", manifest=None):
    p = {
        "meta": {
            "pit_id": pid, "campaign": "WY2026", "date": "2026-02-10",
            "location": "Grand Mesa", "site": "Upper Ridge",
            "total_depth": 100, "recorded_by": "A", "surveyors": "B",
            "no_instruments": False, "no_tasks": False,
        },
        "weather": {}, "ground": {},
        "temperature": [], "density": [], "lwc": [],
        "stratigraphy": [], "ssa": [], "ssa_calibration": {},
        "instruments": [],
    }
    if manifest is not None:
        p["attachment_manifest"] = manifest
    return p


def render_ok(_payload):
    return b"\x89PNG\r\n\x1a\nTEST", b"%PDF-1.4\nTEST"


def reset():
    db_path = Path(os.environ["CRYOPIT_DB_PATH"])
    if db_path.exists():
        db_path.unlink()
    exports = Path(os.environ["CRYOPIT_EXPORT_DIR"])
    if exports.exists():
        shutil.rmtree(exports)
    db.init_db()


def manifest_row(data: bytes, *, category="pitwall", filename="wall.jpg",
                 top=None, bottom=None, queue_id=None):
    return {
        "queue_id": queue_id or str(uuid.uuid4()),
        "category": category,
        "filename": filename,
        "mime_type": "image/jpeg",
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "top_cm": top,
        "bottom_cm": bottom,
    }


def call_attach(site_id, row, data):
    request.form = {
        "category": row["category"],
        "queue_id": row["queue_id"],
    }
    if row.get("top_cm") is not None:
        request.form["top_cm"] = str(row["top_cm"])
    if row.get("bottom_cm") is not None:
        request.form["bottom_cm"] = str(row["bottom_cm"])
    request.files = {"file": Upload(data, row["filename"])}
    result = web.api_attach(site_id)
    if isinstance(result, tuple):
        return result[0], result[1]
    return result, 200


def test_schema_has_durable_upload_manifest():
    reset()
    conn = sqlite3.connect(os.environ["CRYOPIT_DB_PATH"])
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(attachment_uploads)")}
        assert {"queue_id", "site_id", "status", "attachment_id", "client_sha256"} <= cols
        assert not conn.execute("PRAGMA foreign_key_check").fetchall()
    finally:
        conn.close()


def test_archive_registers_expected_photos_but_not_in_raw_json():
    reset()
    data = b"\xff\xd8\xffPHOTO-A"
    item = manifest_row(data)
    result = lifecycle.archive_payload(payload(manifest=[item]), None, render_ok)
    assert result["ok"] and result["photo_uploads"]["pending"] == 1
    site_id = result["site_id"]
    conn = sqlite3.connect(os.environ["CRYOPIT_DB_PATH"])
    try:
        row = conn.execute(
            "SELECT queue_id,status,original_filename,client_sha256 FROM attachment_uploads"
        ).fetchone()
        assert row == (item["queue_id"], "pending", "wall.jpg", item["sha256"])
        raw = json.loads(conn.execute(
            "SELECT raw_json FROM sites WHERE site_id=?", (site_id,)).fetchone()[0])
        assert "attachment_manifest" not in raw
    finally:
        conn.close()
    listed = repository.list_pits(10)[0]
    assert listed["pending_photos"] == 1


def test_upload_is_idempotent_by_queue_id():
    reset()
    data = b"\xff\xd8\xffPHOTO-B"
    item = manifest_row(data)
    archived = lifecycle.archive_payload(payload(manifest=[item]), None, render_ok)
    site_id = archived["site_id"]

    first, code = call_attach(site_id, item, data)
    assert code == 200 and first["ok"] and first["attachment_id"]
    second, code = call_attach(site_id, item, data)
    assert code == 200 and second["ok"] and second["idempotent"]
    assert second["attachment_id"] == first["attachment_id"]

    conn = sqlite3.connect(os.environ["CRYOPIT_DB_PATH"])
    try:
        assert conn.execute("SELECT COUNT(*) FROM attachments").fetchone()[0] == 1
        state = conn.execute(
            "SELECT status,attachment_id FROM attachment_uploads WHERE queue_id=?",
            (item["queue_id"],)).fetchone()
        assert state == ("stored", first["attachment_id"])
    finally:
        conn.close()


def test_unregistered_or_mismatched_upload_stays_unstored():
    reset()
    data = b"\xff\xd8\xffPHOTO-C"
    registered = manifest_row(data)
    archived = lifecycle.archive_payload(payload(manifest=[registered]), None, render_ok)
    site_id = archived["site_id"]

    other = dict(registered, queue_id=str(uuid.uuid4()))
    response, code = call_attach(site_id, other, data)
    assert code == 409 and response["expected"] is False

    response, code = call_attach(site_id, registered, data + b"changed")
    assert code == 409 and "size" in response["msg"].lower()
    conn = sqlite3.connect(os.environ["CRYOPIT_DB_PATH"])
    try:
        assert conn.execute("SELECT COUNT(*) FROM attachments").fetchone()[0] == 0
        assert conn.execute(
            "SELECT status FROM attachment_uploads WHERE queue_id=?",
            (registered["queue_id"],)).fetchone()[0] == "pending"
    finally:
        conn.close()


def test_byte_duplicate_satisfies_each_expected_queue_item():
    reset()
    data = b"\xff\xd8\xffPHOTO-D"
    one = manifest_row(data, filename="one.jpg")
    two = manifest_row(data, filename="two.jpg")
    archived = lifecycle.archive_payload(payload(manifest=[one, two]), None, render_ok)
    site_id = archived["site_id"]
    first, _ = call_attach(site_id, one, data)
    second, _ = call_attach(site_id, two, data)
    assert first["ok"] and second["ok"] and second["duplicate"]
    assert first["attachment_id"] == second["attachment_id"]
    conn = sqlite3.connect(os.environ["CRYOPIT_DB_PATH"])
    try:
        assert conn.execute("SELECT COUNT(*) FROM attachments").fetchone()[0] == 1
        states = conn.execute(
            "SELECT status,attachment_id FROM attachment_uploads ORDER BY queue_id"
        ).fetchall()
        assert states == [("stored", first["attachment_id"]),
                          ("stored", first["attachment_id"])]
    finally:
        conn.close()


def test_cancellation_is_explicit_and_stored_items_cannot_be_cancelled():
    reset()
    data = b"\xff\xd8\xffPHOTO-E"
    pending = manifest_row(data)
    archived = lifecycle.archive_payload(payload(manifest=[pending]), None, render_ok)
    site_id = archived["site_id"]
    cancelled = repository.cancel_attachment_upload(site_id, pending["queue_id"])
    assert cancelled["ok"] and cancelled["cancelled"]
    response, code = call_attach(site_id, pending, data)
    assert code == 409 and response["cancelled"]

    stored = manifest_row(data + b"2", filename="stored.jpg")
    lifecycle.archive_payload(payload(manifest=[stored]), site_id, render_ok)
    uploaded, _ = call_attach(site_id, stored, data + b"2")
    refused = repository.cancel_attachment_upload(site_id, stored["queue_id"])
    assert uploaded["ok"] and refused["ok"] is False and refused["stored"]


def test_manifest_enforces_category_and_sheet_limits_before_upload():
    reset()
    data = b"\xff\xd8\xffLIMIT"
    too_many = [manifest_row(data + bytes([i]), filename=f"p{i}.jpg") for i in range(7)]
    result = lifecycle.archive_payload(payload(manifest=too_many), None, render_ok)
    assert result["ok"] is False and "6-file pitwall limit" in result["msg"]
    conn = sqlite3.connect(os.environ["CRYOPIT_DB_PATH"])
    try:
        assert conn.execute("SELECT COUNT(*) FROM sites").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM attachment_uploads").fetchone()[0] == 0
    finally:
        conn.close()

    pdf = manifest_row(b"%PDF-1.4", category="sheet", filename="sheet.pdf")
    pdf["mime_type"] = "application/pdf"
    image = manifest_row(data, category="sheet", filename="sheet.jpg")
    result = lifecycle.archive_payload(payload(pid="MIX", manifest=[pdf, image]), None, render_ok)
    assert result["ok"] is False and "never a mix" in result["msg"]


def test_queue_id_cannot_be_reused_for_different_file_metadata():
    reset()
    data = b"\xff\xd8\xffPHOTO-G"
    item = manifest_row(data)
    first = lifecycle.archive_payload(payload(manifest=[item]), None, render_ok)
    changed = dict(item, filename="different.jpg", sha256=hashlib.sha256(data + b"x").hexdigest())
    result = lifecycle.archive_payload(payload(manifest=[changed]), first["site_id"], render_ok)
    assert result["ok"] is False and "changed filename" in result["msg"]
    conn = sqlite3.connect(os.environ["CRYOPIT_DB_PATH"])
    try:
        row = conn.execute(
            "SELECT original_filename,client_sha256,status FROM attachment_uploads WHERE queue_id=?",
            (item["queue_id"],)).fetchone()
        assert row == (item["filename"], item["sha256"], "pending")
    finally:
        conn.close()


def test_existing_stage7_database_gets_manifest_table_on_startup():
    reset()
    conn = sqlite3.connect(os.environ["CRYOPIT_DB_PATH"])
    conn.execute("DROP TABLE attachment_uploads")
    conn.commit(); conn.close()
    db.init_db()
    conn = sqlite3.connect(os.environ["CRYOPIT_DB_PATH"])
    try:
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='attachment_uploads'"
        ).fetchone()
    finally:
        conn.close()


def test_interrupted_archive_keeps_expected_manifest_through_recovery():
    reset()
    data = b"\xff\xd8\xffPHOTO-RECOVERY"
    item = manifest_row(data)

    def render_fail(_payload):
        raise RuntimeError("fault injected while building archive")

    with patch.object(__import__("logging").getLogger(lifecycle.__name__), "exception") as expected_log:
        failed = lifecycle.archive_payload(payload(manifest=[item]), None, render_fail)
    expected_log.assert_called_once()
    assert failed["ok"] is False and failed["pending"]
    site_id = failed["site_id"]
    conn = sqlite3.connect(os.environ["CRYOPIT_DB_PATH"])
    try:
        assert conn.execute(
            "SELECT status FROM attachment_uploads WHERE queue_id=?",
            (item["queue_id"],)).fetchone() == ("pending",)
    finally:
        conn.close()

    recovered = lifecycle.recover_pending(site_id, render_ok)
    assert recovered["ok"] and recovered["photo_uploads"]["pending"] == 1
    listed = repository.list_pits(10)
    assert listed and listed[0]["site_id"] == site_id and listed[0]["pending_photos"] == 1


def test_rearchive_does_not_silently_cancel_absent_expectations():
    reset()
    data = b"\xff\xd8\xffPHOTO-F"
    item = manifest_row(data)
    first = lifecycle.archive_payload(payload(manifest=[item]), None, render_ok)
    second = lifecycle.archive_payload(payload(pid="PHOTO", manifest=[]), first["site_id"], render_ok)
    assert second["ok"] and second["photo_uploads"]["pending"] == 1
    assert repository.list_attachment_uploads(first["site_id"])[0]["queue_id"] == item["queue_id"]


if __name__ == "__main__":
    tests = sorted((name, fn) for name, fn in globals().items()
                   if name.startswith("test_") and callable(fn))
    for name, fn in tests:
        fn()
        print("PASS", name)
    print(f"{len(tests)} photo-manifest tests passed")
