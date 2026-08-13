"""SQLite schema, initialization, connections, and legacy identity migration.

The current schema gives every pit an immutable UUID ``site_id``.  Databases
created by pre-Stage-1 builds used the editable ``pit_id`` as the primary key.
That change cannot be expressed safely as a chain of ALTER TABLE statements,
so startup upgrades a legacy file by constructing a complete replacement next
to it and atomically swapping the file only after every row has copied.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import tempfile
import uuid
from pathlib import Path

from .config import CAMPAIGN, DB_PATH, DEV_USER

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCHEMA_SQL = os.path.join(_HERE, "schema.sql")
_SAFE = re.compile(r"[^A-Za-z0-9_-]+")


def _load_schema():
    with open(_SCHEMA_SQL, encoding="utf-8") as f:
        return f.read()


def _safe_name(value, fallback="unnamed"):
    value = _SAFE.sub("_", (value or "").strip()).strip("_")
    return value or fallback


def _derived_folder(meta):
    dstr = (meta.get("date") or "").replace("-", "")
    raw = f"{meta.get('campaign') or CAMPAIGN}_{meta.get('pit_id') or 'pit'}_{dstr}"
    return _safe_name(raw, "pit")


def _tables(conn):
    return {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}


def _columns(conn, table):
    return [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')]


def _guard_foreign_db(conn, path=None):
    tables = _tables(conn)
    if not tables:
        return
    if "sites" in tables:
        cols = set(_columns(conn, "sites"))
        if "pit_id" in cols or "site_id" in cols:
            return
    target = path or DB_PATH
    raise SystemExit(
        f"CRYOPIT_DB_PATH points at an existing non-CryoPit SQLite database "
        f"({target!r} contains tables: {', '.join(sorted(tables)) or 'none'}). "
        "Refusing to modify it."
    )


def _copy_rows(src, dst, table, columns=None):
    if table not in _tables(src):
        return
    src_cols = set(_columns(src, table))
    dst_cols = set(_columns(dst, table))
    cols = columns or [c for c in _columns(src, table) if c in dst_cols]
    cols = [c for c in cols if c in src_cols and c in dst_cols]
    if not cols:
        return
    qcols = ", ".join(f'"{c}"' for c in cols)
    marks = ",".join("?" for _ in cols)
    rows = src.execute(f'SELECT {qcols} FROM "{table}"').fetchall()
    if rows:
        dst.executemany(
            f'INSERT OR IGNORE INTO "{table}" ({qcols}) VALUES ({marks})', rows)


def _upgrade_legacy_file(path):
    """Upgrade a pit_id-primary-key database without modifying it in place.

    A fully formed new database is built beside the old file.  ``os.replace``
    publishes it only after validation and commit, so a failed migration leaves
    the source database untouched.
    """
    path = os.path.abspath(path)
    src = sqlite3.connect(path)
    src.row_factory = sqlite3.Row
    try:
        _guard_foreign_db(src, path)
        if "sites" not in _tables(src) or "site_id" in set(_columns(src, "sites")):
            return False
        try:
            src.execute("PRAGMA wal_checkpoint(FULL)")
        except sqlite3.DatabaseError:
            pass

        parent = os.path.dirname(path) or "."
        fd, tmp = tempfile.mkstemp(prefix=".cryopit-site-id-", suffix=".sqlite", dir=parent)
        os.close(fd)
        try:
            dst = sqlite3.connect(tmp)
            try:
                dst.execute("PRAGMA foreign_keys=OFF")
                dst.executescript(_load_schema())
                dst.execute("BEGIN IMMEDIATE")

                # Lookup tables retain their local integer identities so child
                # instrument/observer references remain valid.
                for table in ("campaigns", "observers", "instruments"):
                    _copy_rows(src, dst, table)

                src_site_cols = set(_columns(src, "sites"))
                dst_site_cols = set(_columns(dst, "sites"))
                site_payload_cols = [c for c in _columns(src, "sites")
                                     if c in dst_site_cols and c not in
                                     {"site_id", "export_folder", "pending_export_folder"}]
                site_map = {}
                rows = src.execute("SELECT * FROM sites ORDER BY pit_id").fetchall()
                for row in rows:
                    sid = str(uuid.uuid4())
                    old_pid = row["pit_id"]
                    site_map[old_pid] = sid
                    owner = row["owner"] if "owner" in src_site_cols and row["owner"] else DEV_USER
                    try:
                        raw = json.loads(row["raw_json"] or "{}") if "raw_json" in src_site_cols else {}
                    except (TypeError, ValueError, json.JSONDecodeError):
                        raw = {}
                    meta = dict(raw.get("meta") or {})
                    meta.setdefault("pit_id", old_pid)
                    if not meta.get("date") and "date" in src_site_cols:
                        meta["date"] = row["date"]
                    if not meta.get("campaign"):
                        campaign_name = None
                        if "campaign_id" in src_site_cols and row["campaign_id"] is not None:
                            crow = src.execute(
                                "SELECT name FROM campaigns WHERE campaign_id=?",
                                (row["campaign_id"],)).fetchone()
                            campaign_name = crow[0] if crow else None
                        if not campaign_name and "campaign" in src_site_cols:
                            campaign_name = row["campaign"]
                        meta["campaign"] = campaign_name or CAMPAIGN

                    values = {c: row[c] for c in site_payload_cols}
                    values["owner"] = owner
                    values["site_id"] = sid
                    values["export_folder"] = _derived_folder(meta)
                    values["pending_export_folder"] = None
                    cols = [c for c in _columns(dst, "sites") if c in values]
                    qcols = ", ".join(f'"{c}"' for c in cols)
                    dst.execute(
                        f'INSERT INTO sites ({qcols}) VALUES ({",".join("?" for _ in cols)})',
                        [values[c] for c in cols])

                def copy_child(table, old_key="pit_id"):
                    if table not in _tables(src):
                        return
                    s_cols = _columns(src, table)
                    d_cols = set(_columns(dst, table))
                    payload_cols = [c for c in s_cols if c != old_key and c in d_cols]
                    select_cols = [old_key] + payload_cols
                    q = ", ".join(f'"{c}"' for c in select_cols)
                    out_cols = ["site_id"] + payload_cols
                    oq = ", ".join(f'"{c}"' for c in out_cols)
                    marks = ",".join("?" for _ in out_cols)
                    batch = []
                    for r in src.execute(f'SELECT {q} FROM "{table}"'):
                        sid = site_map.get(r[0])
                        if sid:
                            batch.append((sid, *r[1:]))
                    if batch:
                        dst.executemany(
                            f'INSERT OR IGNORE INTO "{table}" ({oq}) VALUES ({marks})', batch)

                for table in ("site_observers", "site_instruments", "layers",
                              "ssa_calibration", "attachments", "attachment_uploads", "swe_samples"):
                    copy_child(table)

                dst.execute("COMMIT")
                dst.execute("PRAGMA foreign_keys=ON")
                bad = dst.execute("PRAGMA foreign_key_check").fetchall()
                if bad:
                    raise RuntimeError(f"site_id migration produced foreign-key errors: {bad[:3]}")
                if dst.execute("SELECT COUNT(*) FROM sites").fetchone()[0] != len(rows):
                    raise RuntimeError("site_id migration did not copy every pit")
            except Exception:
                try:
                    dst.execute("ROLLBACK")
                except sqlite3.DatabaseError:
                    pass
                raise
            finally:
                dst.close()

            # Windows will not replace a SQLite file while another handle is
            # still open. Close the source after the replacement DB has been
            # fully validated, but before copying the backup or publishing it.
            src.close()
            src = None

            # Keep one explicit pre-migration backup; do not overwrite an older
            # backup from a prior attempt.
            backup = path + ".pre-site-id.bak"
            if not os.path.exists(backup):
                with open(path, "rb") as rf, open(backup, "xb") as wf:
                    while True:
                        block = rf.read(1024 * 1024)
                        if not block:
                            break
                        wf.write(block)
            os.replace(tmp, path)
            for suffix in ("-wal", "-shm"):
                try:
                    os.remove(path + suffix)
                except FileNotFoundError:
                    pass
            return True
        finally:
            try:
                os.remove(tmp)
            except FileNotFoundError:
                pass
    finally:
        if src is not None:
            src.close()


def _migrate(conn):
    """Idempotent additive migrations for current-site-id development DBs."""
    cols = set(_columns(conn, "sites"))
    add = {
        "owner": "TEXT",
        "raw_json": "TEXT",
        "current_revision_id": "TEXT",
        "pit_open_time": "TEXT",
        "gps_uncertainty_m": "REAL",
        "comment_weather": "TEXT",
        "comment_pit": "TEXT",
        "comment_hardness": "TEXT",
        "comment_misc": "TEXT",
        "temp_time_start": "TEXT",
        "temp_time_end": "TEXT",
        "updated_at": "TEXT",
        "campaign_id": "INTEGER REFERENCES campaigns(campaign_id)",
        "swe_melt_evidence": "TEXT",
        "export_folder": "TEXT",
        "pending_export_folder": "TEXT",
    }
    for col, typ in add.items():
        if col not in cols:
            conn.execute(f'ALTER TABLE sites ADD COLUMN "{col}" {typ}')
    conn.execute("UPDATE sites SET owner=? WHERE owner IS NULL", (DEV_USER,))

    acols = set(_columns(conn, "attachments"))
    attachment_add = {
        "top_cm": "REAL",
        "bottom_cm": "REAL",
        "storage_status": "TEXT DEFAULT 'stored'",
        "storage_error": "TEXT",
        "pending_delete": "INTEGER NOT NULL DEFAULT 0",
        "trash_relpath": "TEXT",
    }
    for col, typ in attachment_add.items():
        if col not in acols:
            conn.execute(f'ALTER TABLE attachments ADD COLUMN "{col}" {typ}')

    ucols = set(_columns(conn, "attachment_uploads"))
    upload_add = {
        "publication_state": "TEXT",
        "staged_relpath": "TEXT",
        "target_relpath": "TEXT",
        "server_sha256": "TEXT",
    }
    for col, typ in upload_add.items():
        if col not in ucols:
            conn.execute(f'ALTER TABLE attachment_uploads ADD COLUMN "{col}" {typ}')

    lcols = set(_columns(conn, "layers"))
    for col in ("signal_v", "reflectance_pct", "ssa_m2kg", "layer_density_kgm3"):
        if col not in lcols:
            conn.execute(f'ALTER TABLE layers ADD COLUMN "{col}" REAL')

    ocols = set(_columns(conn, "observers"))
    for col in ("email", "institution"):
        if col not in ocols:
            conn.execute(f'ALTER TABLE observers ADD COLUMN "{col}" TEXT')

    # Collapse old duplicates before enforcing attachment identity.
    conn.execute("""
        DELETE FROM attachments
         WHERE attachment_id NOT IN (
               SELECT MIN(attachment_id) FROM attachments
                GROUP BY site_id, category, sha256,
                         COALESCE(top_cm,-999), COALESCE(bottom_cm,-999))
    """)
    conn.execute("DROP INDEX IF EXISTS idx_attachments_identity")
    conn.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_attachments_identity
                    ON attachments(site_id, category, sha256,
                                   COALESCE(top_cm,-999), COALESCE(bottom_cm,-999))""")
    # Multiple queue operations may legitimately converge on the same byte-
    # identical attachment, so this lookup index must not be UNIQUE.
    conn.execute("DROP INDEX IF EXISTS idx_attachment_uploads_attachment")
    conn.execute("""CREATE INDEX IF NOT EXISTS idx_attachment_uploads_attachment
                    ON attachment_uploads(attachment_id)
                    WHERE attachment_id IS NOT NULL""")
    conn.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_attachment_uploads_target
                    ON attachment_uploads(site_id, target_relpath)
                    WHERE target_relpath IS NOT NULL AND status='pending'""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sites_campaign_date ON sites(campaign_id, date)")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_sites_owner_pit_id ON sites(owner, pit_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sites_owner_pit_search ON sites(owner, pit_id COLLATE NOCASE)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sites_owner_site_search ON sites(owner, site COLLATE NOCASE)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sites_owner_campaign_date ON sites(owner, campaign_id, date DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sites_pending_updated ON sites(owner, pending_export_folder, updated_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_attachments_site_storage ON attachments(site_id, storage_status, pending_delete)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sites_current_revision ON sites(current_revision_id)")

    # Stage 14: one persistent installation UUID and a revision-1 backfill for
    # every pre-existing pit. The schema script creates the history tables;
    # this additive migration fills them without changing the scientific data.
    from .revisions import backfill_revisions
    backfill_revisions(conn)


def init_db(path=None):
    path = os.path.abspath(path or DB_PATH)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path) and os.path.getsize(path):
        probe = sqlite3.connect(path)
        try:
            _guard_foreign_db(probe, path)
            legacy = "sites" in _tables(probe) and "site_id" not in set(_columns(probe, "sites"))
        finally:
            probe.close()
        if legacy:
            _upgrade_legacy_file(path)

    conn = sqlite3.connect(path)
    try:
        _guard_foreign_db(conn, path)
        conn.executescript(_load_schema())
        _migrate(conn)
        conn.commit()
    finally:
        conn.close()


def get_conn(path=None):
    conn = sqlite3.connect(path or DB_PATH, timeout=10, isolation_level=None)
    from .config import SQLITE_JOURNAL
    conn.execute(f"PRAGMA journal_mode={SQLITE_JOURNAL}")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn
