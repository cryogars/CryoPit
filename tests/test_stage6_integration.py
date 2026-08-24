"""Cumulative Stage 1-5 integration tests without Flask.

Covers immutable site identity, legacy migration, recoverable first archive,
recoverable re-archive folder renaming, in-place attachment preservation, edit
identity semantics, and the three-state instrument contract.
"""
from __future__ import annotations

import importlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import types
from unittest.mock import patch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TMP = Path(tempfile.mkdtemp(prefix="cryopit-stage6-"))
os.environ["CRYOPIT_DB_PATH"] = str(TMP / "stage6.db")
os.environ["CRYOPIT_EXPORT_DIR"] = str(TMP / "exports")

PKG = "_cryopit_stage6_test"
pkg = types.ModuleType(PKG)
pkg.__path__ = [str(ROOT / "cryopit")]
sys.modules[PKG] = pkg
_had_flask = "flask" in sys.modules
if not _had_flask:
    flask_stub = types.ModuleType("flask")
    flask_stub.request = types.SimpleNamespace(headers={})
    flask_stub.has_request_context = lambda: False
    sys.modules["flask"] = flask_stub
try:
    db = importlib.import_module(f"{PKG}.db")
    repository = importlib.import_module(f"{PKG}.repository")
    lifecycle = importlib.import_module(f"{PKG}.archive_lifecycle")
finally:
    if not _had_flask:
        sys.modules.pop("flask", None)


def payload(pid, campaign="WY2026", date="2026-02-10"):
    return {
        "meta": {
            "pit_id": pid, "campaign": campaign, "date": date,
            "location": "Grand Mesa", "site": "Upper Ridge",
            "total_depth": 100, "recorded_by": "A", "surveyors": "B",
            "no_instruments": False, "no_tasks": False,
        },
        "weather": {}, "ground": {},
        "temperature": [{"height": 100, "temp": -8}, {"height": 0, "temp": -2}],
        "density": [{"top": 100, "bottom": 0, "a": 250, "b": 260, "c": None}],
        "lwc": [],
        "stratigraphy": [{"top": 100, "bottom": 0, "gtype": "RG",
                           "hardness": "1F", "wetness": "D",
                           "layer_density_a": 250, "layer_density_b": 260,
                           "layer_density": 255}],
        "ssa": [], "ssa_calibration": {},
        "instruments": [
            {"name": "SMP", "used": "Y", "sn": "S-1"},
            {"name": "Digital LWC", "used": "N", "sn": ""},
            {"name": "Lyte Probe", "used": None, "sn": ""},
        ],
    }


def render_ok(_payload):
    return b"\x89PNG\r\n\x1a\nTEST", b"%PDF-1.4\nTEST"


def reset():
    for path in (Path(os.environ["CRYOPIT_DB_PATH"]), TMP / "exports"):
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()
    db.init_db()


def one_site():
    conn = sqlite3.connect(os.environ["CRYOPIT_DB_PATH"])
    try:
        return conn.execute(
            "SELECT site_id,pit_id,export_folder,pending_export_folder FROM sites"
        ).fetchone()
    finally:
        conn.close()


def test_fresh_schema_uses_immutable_site_id():
    reset()
    conn = sqlite3.connect(os.environ["CRYOPIT_DB_PATH"])
    try:
        cols = conn.execute("PRAGMA table_info(sites)").fetchall()
        assert cols[0][1] == "site_id" and cols[0][5] == 1
        assert any(c[1] == "pit_id" and c[5] == 0 for c in cols)
        assert not conn.execute("PRAGMA foreign_key_check").fetchall()
        fk = conn.execute("PRAGMA foreign_key_list(attachments)").fetchall()
        assert any(row[3] == "site_id" and row[4] == "site_id" for row in fk)
    finally:
        conn.close()



def _make_minimal_legacy(path, pit_id="OLD"):
    path.unlink(missing_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE campaigns(campaign_id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL);
        CREATE TABLE sites(pit_id TEXT PRIMARY KEY, owner TEXT, raw_json TEXT,
          campaign_id INTEGER, date TEXT, created_at TEXT, updated_at TEXT);
        CREATE TABLE observers(observer_id INTEGER PRIMARY KEY, name TEXT UNIQUE, email TEXT, institution TEXT);
        CREATE TABLE instruments(instrument_id INTEGER PRIMARY KEY, name TEXT UNIQUE, model TEXT);
        CREATE TABLE site_observers(pit_id TEXT, observer_id INTEGER, role TEXT);
        CREATE TABLE site_instruments(pit_id TEXT, instrument_id INTEGER, serial_number TEXT, used TEXT);
        CREATE TABLE layers(layer_id INTEGER PRIMARY KEY, pit_id TEXT, kind TEXT,
          top_cm REAL,bottom_cm REAL,height_cm REAL,value_a REAL,value_b REAL,value_c REAL,
          grain_size_min_mm REAL,grain_size_max_mm REAL,grain_size_avg_mm REAL,
          grain_type TEXT,hand_hardness TEXT,manual_wetness TEXT,signal_v REAL,
          reflectance_pct REAL,ssa_m2kg REAL,layer_density_kgm3 REAL,time_recorded TEXT,
          comments TEXT,instrument_id INTEGER);
        CREATE TABLE ssa_calibration(calib_id INTEGER PRIMARY KEY,pit_id TEXT,
          instrument_id INTEGER,operator TEXT,spectralon_level REAL,calib_value_v REAL,
          measured_at TEXT,notes TEXT);
        CREATE TABLE attachments(attachment_id INTEGER PRIMARY KEY,pit_id TEXT,
          category TEXT,filename TEXT,sha256 TEXT,top_cm REAL,bottom_cm REAL,uploaded_at TEXT);
        CREATE TABLE swe_samples(pit_id TEXT,sample TEXT,depth_cm REAL,swe_mm REAL,density_kgm3 REAL);
    """)
    conn.execute("INSERT INTO campaigns VALUES(1,'WY2026')")
    raw = json.dumps({"meta": {"pit_id": pit_id, "campaign": "WY2026", "date": "2026-02-10"}})
    conn.execute("INSERT INTO sites VALUES(?, 'local', ?, 1, '2026-02-10', 'x', 'x')", (pit_id, raw))
    conn.commit(); conn.close()

def test_legacy_database_migrates_transactionally():
    legacy = TMP / "legacy.db"
    _make_minimal_legacy(legacy)
    conn = sqlite3.connect(legacy)
    conn.execute("INSERT INTO layers(layer_id,pit_id,kind,height_cm,value_a) VALUES(1,'OLD','temperature',10,-5)")
    conn.execute("INSERT INTO attachments VALUES(7,'OLD','stratigraphy','x.jpg','abc',10,5,'x')")
    conn.commit(); conn.close()

    assert db._upgrade_legacy_file(str(legacy)) is True
    conn = sqlite3.connect(legacy)
    try:
        site = conn.execute("SELECT site_id,pit_id,export_folder FROM sites").fetchone()
        assert site[0] and site[1:] == ("OLD", "WY2026_OLD_20260210")
        assert conn.execute("SELECT site_id FROM layers").fetchone()[0] == site[0]
        att = conn.execute("SELECT attachment_id,site_id,top_cm,bottom_cm FROM attachments").fetchone()
        assert att == (7, site[0], 10.0, 5.0)
        assert not conn.execute("PRAGMA foreign_key_check").fetchall()
    finally:
        conn.close()
    assert Path(str(legacy) + ".pre-site-id.bak").exists()



def test_failed_legacy_migration_leaves_source_byte_identical():
    legacy = TMP / "legacy-fail.db"
    _make_minimal_legacy(legacy, "UNCHANGED")
    before = legacy.read_bytes()
    original = db._copy_rows
    db._copy_rows = lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("injected copy failure"))
    try:
        try:
            db._upgrade_legacy_file(str(legacy))
        except RuntimeError as exc:
            assert "injected copy failure" in str(exc)
        else:
            raise AssertionError("migration failure was not propagated")
    finally:
        db._copy_rows = original
    assert legacy.read_bytes() == before
    conn = sqlite3.connect(legacy)
    try:
        assert "site_id" not in {r[1] for r in conn.execute("PRAGMA table_info(sites)")}
        assert conn.execute("SELECT pit_id FROM sites").fetchone()[0] == "UNCHANGED"
    finally:
        conn.close()

def test_first_archive_is_hidden_until_publication_finishes():
    reset()
    def fail(_payload):
        raise RuntimeError("injected render failure")
    with patch.object(__import__("logging").getLogger(lifecycle.__name__), "exception") as expected_log:
        result = lifecycle.archive_payload(payload("PENDING"), None, fail)
    expected_log.assert_called_once()
    assert result["ok"] is False and result["pending"] is True
    sid, _, export_folder, pending = one_site()
    assert export_folder is None and pending == "WY2026_PENDING_20260210"
    assert repository.list_pits(10) == []
    assert repository.list_pending_pits()[0]["site_id"] == sid

    recovered = lifecycle.recover_pending(sid, render_ok)
    assert recovered["ok"] and Path(recovered["folder"]).is_dir()
    assert repository.list_pending_pits() == []
    assert repository.list_pits(10)[0]["site_id"] == sid


def test_first_archive_publishes_complete_folder_then_finalizes_db():
    reset()
    result = lifecycle.archive_payload(payload("FIRST"), None, render_ok)
    assert result["ok"] and result["updated"] is False
    folder = Path(result["folder"])
    assert len(list((folder / "csv").glob("*.csv"))) == 7
    assert (folder / "figures").is_dir() and (folder / "uploads").is_dir()
    assert (folder / ".cryopit-archive.json").is_file()
    site = one_site()
    assert site[0] == result["site_id"] and site[2] == folder.name and site[3] is None


def test_rearchive_renames_folder_and_preserves_attachments_in_place():
    reset()
    first = lifecycle.archive_payload(payload("EDIT"), None, render_ok)
    sid = first["site_id"]
    old = Path(first["folder"])
    photo = old / "uploads" / "stratigraphy" / "100-050cm" / "photo.jpg"
    photo.parent.mkdir(parents=True); photo.write_bytes(b"photo")
    conn = sqlite3.connect(os.environ["CRYOPIT_DB_PATH"])
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("INSERT INTO attachments(attachment_id,site_id,category,filename,sha256,top_cm,bottom_cm) VALUES(?,?,?,?,?,?,?)",
                 (42, sid, "stratigraphy", "photo.jpg", "abc", 100, 50))
    conn.commit(); conn.close()

    changed = payload("EDIT-CORRECTED", campaign="NIVAL", date="2026-02-11")
    result = lifecycle.archive_payload(changed, sid, render_ok)
    assert result["ok"] and result["updated"] is True
    new = Path(result["folder"])
    assert not old.exists() and new.name == "NIVAL_EDIT-CORRECTED_20260211"
    assert (new / "uploads" / "stratigraphy" / "100-050cm" / "photo.jpg").read_bytes() == b"photo"
    conn = sqlite3.connect(os.environ["CRYOPIT_DB_PATH"])
    try:
        row = conn.execute("SELECT attachment_id,site_id,top_cm,bottom_cm FROM attachments").fetchone()
        assert row == (42, sid, 100.0, 50.0)
        assert conn.execute("SELECT pit_id FROM sites WHERE site_id=?", (sid,)).fetchone()[0] == "EDIT-CORRECTED"
    finally:
        conn.close()


def test_rearchive_collision_stays_pending_and_does_not_guess():
    reset()
    first = lifecycle.archive_payload(payload("COLLIDE"), None, render_ok)
    sid = first["site_id"]
    desired = "NEW_COLLIDE_20260211"
    Path(os.environ["CRYOPIT_EXPORT_DIR"], desired).mkdir(parents=True)
    result = lifecycle.archive_payload(payload("COLLIDE", "NEW", "2026-02-11"), sid, render_ok)
    assert result["ok"] is False and result["pending"] is True
    assert "Both recorded and desired" in result["msg"]
    assert repository.list_pits(10) == []
    assert repository.list_pending_pits()[0]["site_id"] == sid


def test_new_form_cannot_overwrite_existing_pit_id():
    reset()
    first = lifecycle.archive_payload(payload("UNIQUE"), None, render_ok)
    duplicate = lifecycle.archive_payload(payload("UNIQUE"), None, render_ok)
    assert first["ok"] and duplicate["ok"] is False and duplicate["exists"] is True
    conn = sqlite3.connect(os.environ["CRYOPIT_DB_PATH"])
    try:
        assert conn.execute("SELECT COUNT(*) FROM sites").fetchone()[0] == 1
    finally:
        conn.close()


def test_instrument_three_states_survive_cumulative_build():
    reset()
    result = lifecycle.archive_payload(payload("TRISTATE"), None, render_ok)
    sid = result["site_id"]
    conn = sqlite3.connect(os.environ["CRYOPIT_DB_PATH"])
    try:
        states = dict(conn.execute("""SELECT i.name,si.used FROM site_instruments si
            JOIN instruments i USING(instrument_id) WHERE si.site_id=?""", (sid,)))
        assert states["SMP"] == "Y" and states["Digital LWC"] == "N"
        assert "Lyte Probe" in states and states["Lyte Probe"] is None
        raw = json.loads(conn.execute("SELECT raw_json FROM sites WHERE site_id=?", (sid,)).fetchone()[0])
        assert {x["name"]: x["used"] for x in raw["instruments"]}["Lyte Probe"] is None
    finally:
        conn.close()


def test_first_archive_recovers_after_publish_before_sqlite_finalize():
    reset()
    original = lifecycle.finalize_export
    lifecycle.finalize_export = lambda _sid, _folder: False
    try:
        failed = lifecycle.archive_payload(payload("FINALIZE"), None, render_ok)
    finally:
        lifecycle.finalize_export = original
    assert failed["ok"] is False and failed["pending"] is True
    sid, _, export_folder, pending = one_site()
    assert export_folder is None and pending == "WY2026_FINALIZE_20260210"
    final = Path(os.environ["CRYOPIT_EXPORT_DIR"], pending)
    assert final.is_dir() and (final / ".cryopit-archive.json").is_file()

    recovered = lifecycle.recover_pending(sid, render_ok)
    assert recovered["ok"] and recovered["recovered"] is True
    assert one_site()[2:] == (pending, None)


def test_rearchive_recovers_when_directory_rename_already_happened():
    reset()
    first = lifecycle.archive_payload(payload("MOVED"), None, render_ok)
    sid = first["site_id"]
    old = Path(first["folder"])
    changed = payload("MOVED", "NEWCAMPAIGN", "2026-02-11")
    desired = lifecycle.derive_export_folder(changed)
    status, info = repository.save_pit(changed, site_id=sid,
                                       pending_export_folder=desired)
    assert status == "ok" and info["updated"] is True
    new = Path(os.environ["CRYOPIT_EXPORT_DIR"], desired)
    os.rename(old, new)  # crash immediately after the filesystem rename

    recovered = lifecycle.recover_pending(sid, render_ok)
    assert recovered["ok"] and new.is_dir() and not old.exists()
    assert one_site()[2:] == (desired, None)


def test_rearchive_recovers_when_rename_has_not_happened():
    reset()
    first = lifecycle.archive_payload(payload("NOTMOVED"), None, render_ok)
    sid = first["site_id"]
    old = Path(first["folder"])
    changed = payload("NOTMOVED", "NEWCAMPAIGN", "2026-02-11")
    desired = lifecycle.derive_export_folder(changed)
    status, _ = repository.save_pit(changed, site_id=sid,
                                    pending_export_folder=desired)
    assert status == "ok" and old.is_dir()

    recovered = lifecycle.recover_pending(sid, render_ok)
    new = Path(os.environ["CRYOPIT_EXPORT_DIR"], desired)
    assert recovered["ok"] and new.is_dir() and not old.exists()
    assert one_site()[2:] == (desired, None)


def test_archive_retry_returns_pending_conflict_instead_of_raising():
    reset()
    first = lifecycle.archive_payload(payload("RETRYCONFLICT"), None, render_ok)
    sid = first["site_id"]
    changed = payload("RETRYCONFLICT", "NEW", "2026-02-11")
    desired = lifecycle.derive_export_folder(changed)
    Path(os.environ["CRYOPIT_EXPORT_DIR"], desired).mkdir(parents=True)
    first_failure = lifecycle.archive_payload(changed, sid, render_ok)
    assert first_failure["pending"] is True

    retry = lifecycle.archive_payload(changed, sid, render_ok)
    assert retry["ok"] is False and retry["pending"] is True
    assert "Both recorded and desired" in retry["msg"]

if __name__ == "__main__":
    db.init_db()
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print("PASS", test.__name__)
    print(f"{len(tests)} Stage 6 integration tests passed")

