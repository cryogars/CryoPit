"""Immutable, revision-aware scientific record history.

A pit's ``site_id`` identifies the real-world record.  Each accepted form state
has its own globally unique ``revision_id`` and points to the revision it was
based on.  This gives disconnected field installations a safe fast-forward
rule without relying on timestamps or local integer keys.
"""
from __future__ import annotations

import hashlib
import json
import uuid

PAYLOAD_VERSION = 1
INSTALLATION_ID_KEY = "installation_id"


def canonical_json(payload) -> str:
    """Return one stable UTF-8 JSON representation for hashing and transfer."""
    if isinstance(payload, str):
        payload = json.loads(payload)
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)


def record_hash(payload) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def ensure_installation_id(conn) -> str:
    row = conn.execute(
        "SELECT value FROM app_metadata WHERE key=?", (INSTALLATION_ID_KEY,)
    ).fetchone()
    if row and row[0]:
        try:
            return str(uuid.UUID(row[0]))
        except (ValueError, TypeError, AttributeError):
            pass
    value = str(uuid.uuid4())
    conn.execute(
        "INSERT OR REPLACE INTO app_metadata(key,value,updated_at) "
        "VALUES (?,?,datetime('now'))",
        (INSTALLATION_ID_KEY, value),
    )
    return value


def current_revision(conn, site_id):
    return conn.execute(
        """SELECT r.revision_id,r.parent_revision_id,r.revision_number,
                  r.payload_version,r.record_hash,r.raw_json,
                  r.source_installation_id,r.source_owner,r.created_at,r.imported_at
             FROM sites s LEFT JOIN site_revisions r
               ON r.revision_id=s.current_revision_id
            WHERE s.site_id=?""",
        (site_id,),
    ).fetchone()


def record_revision(conn, site_id, owner, payload, *,
                    revision_id=None, parent_revision_id=None,
                    revision_number=None, source_installation_id=None,
                    source_owner=None, created_at=None, imported_at=None,
                    import_bundle_id=None, force=False):
    """Create a revision when the canonical scientific payload changed.

    Normal local saves omit all optional identity fields.  The importer supplies
    them so the original revision lineage survives the trip to the central DB.
    """
    raw_json = canonical_json(payload)
    digest = record_hash(raw_json)
    current = current_revision(conn, site_id)
    if not force and current and current[0] and current[4] == digest:
        return {
            "revision_id": current[0], "parent_revision_id": current[1],
            "revision_number": current[2], "record_hash": current[4],
            "created": False,
        }

    installation_id = source_installation_id or ensure_installation_id(conn)
    source_owner = source_owner or owner
    if revision_id is None:
        revision_id = str(uuid.uuid4())
    else:
        revision_id = str(uuid.UUID(str(revision_id)))
    if parent_revision_id is None and current and current[0]:
        parent_revision_id = current[0]
    if parent_revision_id is not None:
        parent_revision_id = str(uuid.UUID(str(parent_revision_id)))
    if revision_number is None:
        revision_number = (current[2] + 1) if current and current[0] else 1

    existing = conn.execute(
        "SELECT site_id,record_hash FROM site_revisions WHERE revision_id=?",
        (revision_id,),
    ).fetchone()
    if existing:
        if existing != (site_id, digest):
            raise ValueError(f"Revision ID {revision_id} already identifies different content.")
    else:
        conn.execute(
            """INSERT INTO site_revisions
               (revision_id,site_id,parent_revision_id,revision_number,
                payload_version,record_hash,raw_json,source_installation_id,
                source_owner,created_at,imported_at,import_bundle_id)
               VALUES (?,?,?,?,?,?,?,?,?,COALESCE(?,datetime('now')),?,?)""",
            (revision_id, site_id, parent_revision_id, int(revision_number),
             PAYLOAD_VERSION, digest, raw_json, installation_id, source_owner,
             created_at, imported_at, import_bundle_id),
        )
    conn.execute(
        "UPDATE sites SET current_revision_id=? WHERE site_id=?",
        (revision_id, site_id),
    )
    return {
        "revision_id": revision_id, "parent_revision_id": parent_revision_id,
        "revision_number": int(revision_number), "record_hash": digest,
        "created": not bool(existing),
    }


def backfill_revisions(conn) -> int:
    """Give pre-Stage-14 pits a revision-1 identity, idempotently."""
    installation_id = ensure_installation_id(conn)
    rows = conn.execute(
        """SELECT site_id,owner,raw_json,created_at,updated_at,current_revision_id
             FROM sites ORDER BY site_id"""
    ).fetchall()
    count = 0
    for site_id, owner, raw_json, created_at, updated_at, current_id in rows:
        if current_id:
            present = conn.execute(
                "SELECT 1 FROM site_revisions WHERE revision_id=? AND site_id=?",
                (current_id, site_id),
            ).fetchone()
            if present:
                continue
        try:
            payload = json.loads(raw_json or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        existing = conn.execute(
            "SELECT revision_id FROM site_revisions WHERE site_id=? "
            "ORDER BY revision_number DESC LIMIT 1", (site_id,)
        ).fetchone()
        if existing:
            conn.execute("UPDATE sites SET current_revision_id=? WHERE site_id=?",
                         (existing[0], site_id))
            continue
        record_revision(
            conn, site_id, owner, payload,
            source_installation_id=installation_id,
            source_owner=owner,
            revision_number=1,
            created_at=updated_at or created_at,
            force=True,
        )
        count += 1
    return count
