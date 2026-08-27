"""Stage 14 one-way, revision-aware field transfer tests.

Dependency-independent: Flask is stubbed because these tests exercise the
storage/repository layer and CLI bundle protocol rather than HTTP routing.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import types
import uuid
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
TMP = Path(tempfile.mkdtemp(prefix="cryopit-stage14-"))

if "flask" not in sys.modules:
    flask_stub = types.ModuleType("flask")
    flask_stub.request = types.SimpleNamespace(headers={})
    flask_stub.has_request_context = lambda: False
    sys.modules["flask"] = flask_stub


def load_instance(name, db_path, export_dir):
    os.environ["CRYOPIT_DB_PATH"] = str(db_path)
    os.environ["CRYOPIT_EXPORT_DIR"] = str(export_dir)
    pkg = types.ModuleType(name)
    pkg.__path__ = [str(ROOT / "cryopit")]
    pkg.__version__ = "3.7.0rc1"
    sys.modules[name] = pkg
    return types.SimpleNamespace(**{
        mod: importlib.import_module(f"{name}.{mod}")
        for mod in ("db", "repository", "archive_lifecycle", "attachment_storage",
                    "revisions", "transfer")
    })


SRC_DB = TMP / "source.db"
SRC_EXPORTS = TMP / "source-exports"
SRC = load_instance("_cryopit_stage14_source", SRC_DB, SRC_EXPORTS)


def payload(pid="FIELD-1", density=250, date="2026-02-10"):
    return {
        "meta": {
            "pit_id": pid, "campaign": "WY2026", "date": date,
            "location": "Grand Mesa", "site": "Upper Ridge",
            "total_depth": 100, "recorded_by": "Field Scientist",
            "surveyors": "Crew Member", "no_instruments": False,
            "no_tasks": False,
        },
        "weather": {}, "ground": {}, "temperature": [],
        "density": [{"top": 100, "bottom": 0, "a": density,
                     "b": density + 5, "c": None}],
        "lwc": [], "stratigraphy": [], "ssa": [],
        "ssa_calibration": {}, "instruments": [],
    }


def render_ok(_payload):
    return b"\x89PNG\r\n\x1a\nSTAGE14", b"%PDF-1.4\nSTAGE14"


def reset_source():
    for path in (SRC_DB, SRC_EXPORTS):
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()
    SRC.db.init_db()


def new_destination(label="dest"):
    db = TMP / f"{label}.db"
    exports = TMP / f"{label}-exports"
    for path in (db, exports):
        if path.is_dir(): shutil.rmtree(path)
        elif path.exists(): path.unlink()
    return db, exports


def archive_source(p=None, site_id=None):
    result = SRC.archive_lifecycle.archive_payload(p or payload(), site_id, render_ok)
    assert result["ok"], result
    return result


def add_source_attachment(site_id, data=b"field photo", *, pending=False):
    digest = hashlib.sha256(data).hexdigest()
    qid = str(uuid.uuid4())
    conn = sqlite3.connect(SRC_DB)
    try:
        folder = conn.execute("SELECT export_folder FROM sites WHERE site_id=?",
                              (site_id,)).fetchone()[0]
        filename = "wall.jpg"
        rel = SRC.attachment_storage.target_relpath("pitwall", None, None, filename)
        path = SRC_EXPORTS / folder / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        att_id = conn.execute(
            """INSERT INTO attachments
               (site_id,category,filename,sha256,storage_status,pending_delete)
               VALUES (?,'pitwall',?,?, 'stored',0)""",
            (site_id, filename, digest),
        ).lastrowid
        conn.execute(
            """INSERT INTO attachment_uploads
               (queue_id,site_id,category,original_filename,mime_type,size_bytes,
                client_sha256,status,attachment_id)
               VALUES (?,?,'pitwall',?,'image/jpeg',?,?, 'stored',?)""",
            (qid, site_id, filename, len(data), digest, att_id),
        )
        pending_qid = None
        if pending:
            pending_qid = str(uuid.uuid4())
            conn.execute(
                """INSERT INTO attachment_uploads
                   (queue_id,site_id,category,original_filename,mime_type,size_bytes,
                    client_sha256,status)
                   VALUES (?,?,'pitwall','later.jpg','image/jpeg',10,?,'pending')""",
                (pending_qid, site_id, "0" * 64),
            )
        conn.commit()
        return qid, pending_qid, digest
    finally:
        conn.close()


def bundle_for(path, site_ids=None):
    return SRC.transfer.create_transfer(
        path, db_path=SRC_DB, export_dir=SRC_EXPORTS,
        owner="local", site_ids=site_ids,
    )




def rewrite_bundle_record(source, target, mutate):
    """Rewrite one record and refresh the outer file manifest for semantic tests."""
    with zipfile.ZipFile(source) as src:
        contents = {info.filename: src.read(info.filename) for info in src.infolist()}
    manifest = json.loads(contents["manifest.json"])
    record_name = next(name for name in contents if name.endswith("/record.json"))
    record = json.loads(contents[record_name])
    mutate(record)
    record_bytes = (json.dumps(record, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()
    contents[record_name] = record_bytes
    for item in manifest["files"]:
        if item["path"] == record_name:
            item["size"] = len(record_bytes)
            item["sha256"] = hashlib.sha256(record_bytes).hexdigest()
            break
    contents["manifest.json"] = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as dst:
        for name, data in contents.items():
            dst.writestr(name, data)

def central_edit(db_path, site_id, new_payload, owner="alice"):
    conn = SRC.db.get_conn(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        camp = new_payload["meta"]["campaign"]
        conn.execute("INSERT OR IGNORE INTO campaigns(name) VALUES(?)", (camp,))
        campaign_id = conn.execute("SELECT campaign_id FROM campaigns WHERE name=?",
                                   (camp,)).fetchone()[0]
        raw = SRC.revisions.canonical_json(new_payload)
        values = SRC.repository._site_values(new_payload, owner, raw, campaign_id)
        assignments = ",".join(f'"{k}"=?' for k in values)
        conn.execute(f"UPDATE sites SET {assignments},updated_at=datetime('now') WHERE site_id=?",
                     [*values.values(), site_id])
        for table in ("site_observers", "site_instruments", "layers",
                      "ssa_calibration", "swe_samples"):
            conn.execute(f"DELETE FROM {table} WHERE site_id=?", (site_id,))
        SRC.repository._write_children(conn, new_payload, site_id)
        revision = SRC.revisions.record_revision(conn, site_id, owner, new_payload)
        conn.execute("COMMIT")
        return revision
    finally:
        conn.close()


def test_schema_backfills_installation_and_revision_identity():
    reset_source()
    first = archive_source()
    conn = sqlite3.connect(SRC_DB)
    try:
        columns = {r[1] for r in conn.execute("PRAGMA table_info(sites)")}
        assert "current_revision_id" in columns
        installation = conn.execute(
            "SELECT value FROM app_metadata WHERE key='installation_id'"
        ).fetchone()[0]
        uuid.UUID(installation)
        row = conn.execute(
            """SELECT s.current_revision_id,r.revision_number,r.parent_revision_id,
                      r.record_hash,r.source_installation_id
                 FROM sites s JOIN site_revisions r
                   ON r.revision_id=s.current_revision_id
                WHERE s.site_id=?""", (first["site_id"],)
        ).fetchone()
        assert row[0] == first["revision_id"] and row[1] == 1 and row[2] is None
        assert row[4] == installation
    finally:
        conn.close()



def test_existing_pit_without_revision_history_is_backfilled_idempotently():
    reset_source(); first = archive_source()
    conn = sqlite3.connect(SRC_DB)
    try:
        conn.execute("UPDATE sites SET current_revision_id=NULL WHERE site_id=?", (first["site_id"],))
        conn.execute("DELETE FROM site_revisions")
        conn.execute("DELETE FROM app_metadata WHERE key='installation_id'")
        conn.commit()
    finally:
        conn.close()
    SRC.db.init_db()
    SRC.db.init_db()
    conn = sqlite3.connect(SRC_DB)
    try:
        row = conn.execute(
            """SELECT s.current_revision_id,r.revision_number,r.parent_revision_id,
                      r.record_hash,m.value
                 FROM sites s JOIN site_revisions r ON r.revision_id=s.current_revision_id
                 JOIN app_metadata m ON m.key='installation_id'
                WHERE s.site_id=?""", (first["site_id"],)
        ).fetchone()
        assert row and row[1] == 1 and row[2] is None
        uuid.UUID(row[0]); uuid.UUID(row[4])
        assert conn.execute("SELECT COUNT(*) FROM site_revisions").fetchone()[0] == 1
    finally:
        conn.close()

def test_rearchive_creates_revision_only_when_payload_changes():
    reset_source()
    first = archive_source()
    same = archive_source(payload(), first["site_id"])
    assert same["revision_created"] is False and same["revision_number"] == 1
    changed = archive_source(payload(density=300), first["site_id"])
    assert changed["revision_created"] is True and changed["revision_number"] == 2
    conn = sqlite3.connect(SRC_DB)
    try:
        rows = conn.execute(
            "SELECT revision_id,parent_revision_id,revision_number FROM site_revisions ORDER BY revision_number"
        ).fetchall()
        assert len(rows) == 2 and rows[1][1] == rows[0][0]
    finally:
        conn.close()


def test_new_import_is_owner_mapped_idempotent_and_audited():
    reset_source(); first = archive_source()
    bundle = TMP / "new.zip"; manifest = bundle_for(bundle)
    assert not (SRC_EXPORTS / ".cryopit-maintenance").exists()
    dst_db, dst_exports = new_destination("new")
    dry = SRC.transfer.import_transfer(
        bundle, destination_owner="alice-subject", db_path=dst_db,
        export_dir=dst_exports, dry_run=True)
    assert dry["summary"] == {"new": 1}
    assert not dst_db.exists() and not dst_exports.exists()
    report = SRC.transfer.import_transfer(
        bundle, destination_owner="alice-subject", db_path=dst_db,
        export_dir=dst_exports)
    assert report["summary"] == {"imported": 1}
    assert not (dst_exports / ".cryopit-maintenance").exists()
    repeat = SRC.transfer.import_transfer(
        bundle, destination_owner="alice-subject", db_path=dst_db,
        export_dir=dst_exports)
    assert repeat["summary"] == {"already": 1}
    conn = sqlite3.connect(dst_db)
    try:
        site = conn.execute(
            "SELECT owner,current_revision_id,export_folder,pending_export_folder FROM sites"
        ).fetchone()
        assert site[0] == "alice-subject" and site[1] == first["revision_id"]
        assert site[2] and site[3] is None
        revision = conn.execute(
            "SELECT source_owner,source_installation_id,import_bundle_id FROM site_revisions"
        ).fetchone()
        assert revision[0] == "local" and revision[1] == manifest["source_installation_id"]
        assert revision[2] == manifest["bundle_id"]
        audit = conn.execute(
            "SELECT status,summary_json FROM transfer_imports"
        ).fetchone()
        assert audit[0] == "complete" and json.loads(audit[1]) == {"already": 1}
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        conn.close()


def test_fast_forward_accepts_field_revision_descended_from_central_tip():
    reset_source(); first = archive_source()
    b1 = TMP / "ff1.zip"; bundle_for(b1)
    dst_db, dst_exports = new_destination("ff")
    SRC.transfer.import_transfer(b1, destination_owner="alice", db_path=dst_db,
                                 export_dir=dst_exports)
    changed_payload = payload("FIELD-1-CORRECTED", density=310, date="2026-02-11")
    second = archive_source(changed_payload, first["site_id"])
    b2 = TMP / "ff2.zip"; bundle_for(b2)
    plan = SRC.transfer.inspect_transfer(
        b2, destination_owner="alice", db_path=dst_db, export_dir=dst_exports)
    assert plan["summary"] == {"fast_forward": 1}
    result = SRC.transfer.import_transfer(
        b2, destination_owner="alice", db_path=dst_db, export_dir=dst_exports)
    assert result["summary"] == {"imported": 1}
    conn = sqlite3.connect(dst_db)
    try:
        row = conn.execute(
            "SELECT pit_id,current_revision_id,export_folder FROM sites"
        ).fetchone()
        assert row == ("FIELD-1-CORRECTED", second["revision_id"],
                       "WY2026_FIELD-1-CORRECTED_20260211")
        assert conn.execute("SELECT COUNT(*) FROM site_revisions").fetchone()[0] == 2
        assert conn.execute("SELECT value_a FROM layers WHERE kind='density'").fetchone()[0] == 310
    finally:
        conn.close()
    assert not (dst_exports / "WY2026_FIELD-1_20260210").exists()


def test_diverged_central_revision_is_quarantined_as_conflict():
    reset_source(); first = archive_source()
    b1 = TMP / "div1.zip"; bundle_for(b1)
    dst_db, dst_exports = new_destination("div")
    SRC.transfer.import_transfer(b1, destination_owner="alice", db_path=dst_db,
                                 export_dir=dst_exports)
    central_edit(dst_db, first["site_id"], payload(density=275), "alice")
    archive_source(payload(density=325), first["site_id"])
    b2 = TMP / "div2.zip"; bundle_for(b2)
    plan = SRC.transfer.inspect_transfer(
        b2, destination_owner="alice", db_path=dst_db, export_dir=dst_exports)
    assert plan["summary"] == {"conflict": 1}
    result = SRC.transfer.import_transfer(
        b2, destination_owner="alice", db_path=dst_db, export_dir=dst_exports)
    assert result["summary"] == {"conflict": 1}
    quarantine = result["items"][0].get("quarantine")
    assert quarantine and (dst_exports / quarantine).is_file()
    assert (dst_exports / quarantine).with_name("record.json").is_file()
    assert not (dst_exports / ".cryopit-maintenance").exists()
    conn = sqlite3.connect(dst_db)
    try:
        assert conn.execute("SELECT value_a FROM layers WHERE kind='density'").fetchone()[0] == 275
        assert conn.execute("SELECT COUNT(*) FROM transfer_import_items WHERE result='conflict'").fetchone()[0] == 1
    finally:
        conn.close()


def test_multiple_field_databases_converge_into_one_central_database():
    reset_source(); one = archive_source(payload("LAPTOP-A"))
    b1 = TMP / "multi-a.zip"; bundle_for(b1)

    db2 = TMP / "source2.db"; exp2 = TMP / "source2-exports"
    SRC2 = load_instance("_cryopit_stage14_source2", db2, exp2)
    SRC2.db.init_db()
    two = SRC2.archive_lifecycle.archive_payload(payload("LAPTOP-B", density=330), None, render_ok)
    assert two["ok"]
    b2 = TMP / "multi-b.zip"
    SRC2.transfer.create_transfer(b2, db_path=db2, export_dir=exp2, owner="local")

    dst_db, dst_exports = new_destination("multi")
    SRC.transfer.import_transfer(b1, destination_owner="alice", db_path=dst_db,
                                 export_dir=dst_exports)
    SRC.transfer.import_transfer(b2, destination_owner="alice", db_path=dst_db,
                                 export_dir=dst_exports)
    conn = sqlite3.connect(dst_db)
    try:
        rows = conn.execute("SELECT site_id,pit_id FROM sites ORDER BY pit_id").fetchall()
        assert rows == sorted([(one["site_id"], "LAPTOP-A"),
                               (two["site_id"], "LAPTOP-B")], key=lambda x: x[1])
        installations = {r[0] for r in conn.execute(
            "SELECT DISTINCT source_installation_id FROM site_revisions")}
        assert len(installations) == 2
    finally:
        conn.close()


def test_different_site_with_same_human_pit_id_is_conflict():
    reset_source(); archive_source(payload("COLLIDE"))
    b1 = TMP / "collide-a.zip"; bundle_for(b1)
    dst_db, dst_exports = new_destination("collide")
    SRC.transfer.import_transfer(b1, destination_owner="alice", db_path=dst_db,
                                 export_dir=dst_exports)

    db2 = TMP / "collision-source2.db"; exp2 = TMP / "collision-source2-exports"
    SRC2 = load_instance("_cryopit_stage14_collision2", db2, exp2)
    SRC2.db.init_db(); second = SRC2.archive_lifecycle.archive_payload(payload("COLLIDE"), None, render_ok)
    assert second["ok"]
    b2 = TMP / "collide-b.zip"
    SRC2.transfer.create_transfer(b2, db_path=db2, export_dir=exp2, owner="local")
    plan = SRC.transfer.inspect_transfer(
        b2, destination_owner="alice", db_path=dst_db, export_dir=dst_exports)
    assert plan["summary"] == {"conflict": 1}


def test_attachments_and_pending_expectations_transfer_by_stable_identity():
    reset_source(); first = archive_source()
    stored_qid, pending_qid, digest = add_source_attachment(first["site_id"], pending=True)
    bundle = TMP / "attachments.zip"; bundle_for(bundle)
    dst_db, dst_exports = new_destination("attachments")
    result = SRC.transfer.import_transfer(
        bundle, destination_owner="alice", db_path=dst_db, export_dir=dst_exports)
    assert result["summary"] == {"imported": 1}
    conn = sqlite3.connect(dst_db)
    try:
        site = conn.execute("SELECT export_folder FROM sites").fetchone()[0]
        att = conn.execute(
            "SELECT attachment_id,filename,sha256,storage_status FROM attachments"
        ).fetchone()
        assert att[2:] == (digest, "stored")
        uploads = conn.execute(
            "SELECT queue_id,status,attachment_id FROM attachment_uploads ORDER BY status"
        ).fetchall()
        by_qid = {r[0]: r[1:] for r in uploads}
        assert by_qid[stored_qid] == ("stored", att[0])
        assert by_qid[pending_qid] == ("pending", None)
        assert hashlib.sha256((dst_exports / site / "uploads/pitwall/wall.jpg").read_bytes()).hexdigest() == digest
    finally:
        conn.close()



def test_same_revision_imports_new_and_progressed_photo_queue_state():
    reset_source(); first = archive_source()
    initial = TMP / "queue-state-initial.zip"; bundle_for(initial)
    dst_db, dst_exports = new_destination("queue-state")
    SRC.transfer.import_transfer(initial, destination_owner="alice", db_path=dst_db,
                                 export_dir=dst_exports)

    _stored_qid, pending_qid, _digest = add_source_attachment(first["site_id"], pending=True)
    with_pending = TMP / "queue-state-pending.zip"; bundle_for(with_pending)
    plan = SRC.transfer.inspect_transfer(
        with_pending, destination_owner="alice", db_path=dst_db, export_dir=dst_exports)
    assert plan["summary"] == {"attachments": 1}
    SRC.transfer.import_transfer(with_pending, destination_owner="alice", db_path=dst_db,
                                 export_dir=dst_exports)
    conn = sqlite3.connect(dst_db)
    try:
        assert conn.execute(
            "SELECT status FROM attachment_uploads WHERE queue_id=?", (pending_qid,)
        ).fetchone()[0] == "pending"
    finally:
        conn.close()

    conn = sqlite3.connect(SRC_DB)
    try:
        conn.execute(
            "UPDATE attachment_uploads SET status='cancelled',last_error='cancelled in field' "
            "WHERE queue_id=?", (pending_qid,)
        )
        conn.commit()
    finally:
        conn.close()
    cancelled = TMP / "queue-state-cancelled.zip"; bundle_for(cancelled)
    plan = SRC.transfer.inspect_transfer(
        cancelled, destination_owner="alice", db_path=dst_db, export_dir=dst_exports)
    assert plan["summary"] == {"attachments": 1}
    SRC.transfer.import_transfer(cancelled, destination_owner="alice", db_path=dst_db,
                                 export_dir=dst_exports)
    conn = sqlite3.connect(dst_db)
    try:
        assert conn.execute(
            "SELECT status,last_error FROM attachment_uploads WHERE queue_id=?", (pending_qid,)
        ).fetchone() == ("cancelled", "cancelled in field")
    finally:
        conn.close()

def test_corrupt_bundle_is_rejected_before_destination_changes():
    reset_source(); archive_source(); clean = TMP / "clean.zip"; bundle_for(clean)
    corrupt = TMP / "corrupt.zip"
    with zipfile.ZipFile(clean) as src, zipfile.ZipFile(corrupt, "w") as dst:
        for info in src.infolist():
            data = src.read(info.filename)
            if info.filename.endswith("record.json"):
                data += b" "
            dst.writestr(info, data)
    dst_db, dst_exports = new_destination("corrupt")
    try:
        SRC.transfer.import_transfer(corrupt, destination_owner="alice",
                                     db_path=dst_db, export_dir=dst_exports)
    except SRC.transfer.TransferError as exc:
        assert "checksum mismatch" in str(exc)
    else:
        raise AssertionError("corrupt transfer was accepted")
    assert not dst_db.exists()



def test_semantic_identity_and_provenance_tampering_is_rejected():
    reset_source(); archive_source(); clean = TMP / "semantic-clean.zip"; bundle_for(clean)

    bad_pit = TMP / "semantic-pit-id.zip"
    rewrite_bundle_record(clean, bad_pit, lambda record: record.__setitem__("pit_id", "LOOKALIKE"))
    try:
        SRC.transfer.verify_transfer(bad_pit)
    except SRC.transfer.TransferError as exc:
        assert "Pit ID" in str(exc)
    else:
        raise AssertionError("inconsistent human Pit ID was accepted")

    bad_provenance = TMP / "semantic-provenance.zip"
    rewrite_bundle_record(
        clean, bad_provenance,
        lambda record: record["revisions"][-1].__setitem__("source_owner", "alice\u200badmin"),
    )
    try:
        SRC.transfer.verify_transfer(bad_provenance)
    except SRC.transfer.TransferError as exc:
        assert "source provenance" in str(exc) or "source owner" in str(exc)
    else:
        raise AssertionError("malformed revision provenance was accepted")

def test_source_pending_archive_is_not_exportable():
    reset_source()
    with patch.object(__import__("logging").getLogger(SRC.archive_lifecycle.__name__), "exception") as expected_log:
        failed = SRC.archive_lifecycle.archive_payload(
            payload("PENDING"), None,
            lambda _p: (_ for _ in ()).throw(RuntimeError("render failed")),
        )
    expected_log.assert_called_once()
    assert failed["pending"]
    try:
        bundle_for(TMP / "pending.zip")
    except SRC.transfer.TransferError as exc:
        assert "recovery" in str(exc)
    else:
        raise AssertionError("pending source pit was exported")


def test_resume_repairs_db_committed_before_new_folder_publication():
    reset_source(); first = archive_source(); bundle = TMP / "resume.zip"; bundle_for(bundle)
    dst_db, dst_exports = new_destination("resume")
    verified = SRC.transfer.verify_transfer(bundle)
    record = verified["records"][first["site_id"]]
    SRC.db.init_db(dst_db)
    conn = SRC.db.get_conn(dst_db)
    try:
        conn.execute("BEGIN IMMEDIATE")
        plan = SRC.transfer._prepare_attachment_plan(
            conn, record, dst_exports, None,
            SRC.transfer._desired_folder(record["revisions"][-1]["payload"]),
        )
        SRC.transfer._upsert_import_db(
            conn, record, "alice",
            SRC.transfer._desired_folder(record["revisions"][-1]["payload"]),
            verified["manifest"]["bundle_id"], plan,
        )
        conn.execute("COMMIT")
    finally:
        conn.close()
    inspect = SRC.transfer.inspect_transfer(
        bundle, destination_owner="alice", db_path=dst_db, export_dir=dst_exports)
    assert inspect["summary"] == {"resume": 1}
    result = SRC.transfer.import_transfer(
        bundle, destination_owner="alice", db_path=dst_db, export_dir=dst_exports)
    assert result["summary"] == {"imported": 1}
    conn = sqlite3.connect(dst_db)
    try:
        assert conn.execute(
            "SELECT export_folder,pending_export_folder FROM sites"
        ).fetchone() == ("WY2026_FIELD-1_20260210", None)
    finally:
        conn.close()


if __name__ == "__main__":
    tests = sorted((name, fn) for name, fn in globals().items()
                   if name.startswith("test_") and callable(fn))
    for name, test in tests:
        test()
        print("PASS", name)
    print(f"{len(tests)} Stage 14 transfer tests passed")