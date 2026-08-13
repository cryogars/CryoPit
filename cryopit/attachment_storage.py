"""Recoverable attachment publication, deletion, and reconciliation.

SQLite and the filesystem cannot share one transaction.  This module records
filesystem intent in SQLite, performs same-filesystem renames, and makes every
interrupted state detectable and retryable.
"""
from __future__ import annotations

import contextlib
import hashlib
import os
import time
import uuid
from pathlib import Path

from .config import EXPORT_DIR
from .db import get_conn
from .storage_lifecycle import (durable_replace, durable_unlink, ensure_directory,
                                fsync_handle, storage_lock)


class AttachmentConflict(RuntimeError):
    pass


class AttachmentIntegrityError(RuntimeError):
    pass


def layer_folder(top_cm, bottom_cm):
    if top_cm is None:
        return ""
    if bottom_cm is None:
        return f"{int(round(top_cm)):03d}cm"
    return f"{int(round(top_cm)):03d}-{int(round(bottom_cm)):03d}cm"


def target_relpath(category, top_cm, bottom_cm, filename):
    parts = ["uploads", category]
    interval = layer_folder(top_cm, bottom_cm)
    if category == "stratigraphy" and interval:
        parts.append(interval)
    parts.append(filename)
    return "/".join(parts)


def stage_relpath(queue_id):
    return f".attachment-staging/{queue_id}.part"


def trash_relpath(attachment_id, filename):
    return f".attachment-trash/{attachment_id}-{filename}"


def _root():
    root = Path(EXPORT_DIR).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _pit_root(export_folder):
    if not export_folder or Path(export_folder).name != export_folder:
        raise AttachmentIntegrityError(f"Unsafe recorded export folder: {export_folder!r}")
    return _root() / export_folder


def _resolve(export_folder, relpath):
    rel = Path(relpath)
    if rel.is_absolute() or ".." in rel.parts:
        raise AttachmentIntegrityError(f"Unsafe attachment path: {relpath!r}")
    base = _pit_root(export_folder).resolve()
    path = (base / rel).resolve()
    try:
        path.relative_to(base)
    except ValueError as exc:
        raise AttachmentIntegrityError(f"Attachment path escapes pit folder: {relpath!r}") from exc
    return path


@contextlib.contextmanager
def attachment_lock():
    """Share one storage lifecycle lock with archive publication/renames."""
    with storage_lock(_root()):
        yield


def adopt_staged_file(export_folder, queue_id, source_path, *, site_id=None, owner=None):
    """Atomically adopt an already-fsynced inbound scratch file.

    The source must live beneath the configured export filesystem so the move
    into the pit-local attachment journal is same-filesystem and atomic.  The
    authoritative pit folder is re-read inside the lifecycle lock immediately
    before publication, matching ``write_staged_file`` recovery semantics.
    """
    source = Path(source_path).resolve()
    root = _root().resolve()
    try:
        source.relative_to(root)
    except ValueError as exc:
        raise AttachmentIntegrityError("Inbound staged upload is outside the export filesystem.") from exc

    with attachment_lock():
        if site_id is not None:
            conn = get_conn()
            try:
                row = _site_row(conn, site_id, owner)
            finally:
                conn.close()
            if row is None:
                raise AttachmentIntegrityError("Pit no longer exists or is not owned by this account.")
            current_folder, pending_folder = row
            if pending_folder or not current_folder:
                raise AttachmentConflict("Finish archive recovery before attaching files.")
            if current_folder != export_folder:
                raise AttachmentConflict(
                    "Pit folder moved while the upload was waiting; retry from the browser outbox."
                )

        rel = stage_relpath(queue_id)
        final = _resolve(export_folder, rel)
        ensure_directory(final.parent)
        durable_replace(source, final)
        return rel


def write_staged_file(export_folder, queue_id, data, *, site_id=None, owner=None):
    """Write bytes once to a hidden same-filesystem staging path.

    Archive and attachment work share the same lifecycle lock. When a site ID
    is supplied, the authoritative export folder is re-read *inside* that lock
    immediately before writing, so a request that waited behind a re-archive
    cannot recreate the old pit directory from stale state.
    """
    with attachment_lock():
        if site_id is not None:
            conn = get_conn()
            try:
                row = _site_row(conn, site_id, owner)
            finally:
                conn.close()
            if row is None:
                raise AttachmentIntegrityError("Pit no longer exists or is not owned by this account.")
            current_folder, pending_folder = row
            if pending_folder or not current_folder:
                raise AttachmentConflict("Finish archive recovery before attaching files.")
            if current_folder != export_folder:
                raise AttachmentConflict(
                    "Pit folder moved while the upload was waiting; retry from the browser outbox."
                )

        rel = stage_relpath(queue_id)
        final = _resolve(export_folder, rel)
        ensure_directory(final.parent)
        temp = final.with_name(f".{final.name}.{uuid.uuid4().hex}.tmp")
        try:
            with open(temp, "xb") as fh:
                fh.write(data)
                fh.flush()
                fsync_handle(fh)
            durable_replace(temp, final)
            return rel
        except Exception:
            try:
                durable_unlink(temp, missing_ok=True)
            except OSError:
                pass
            raise


def remove_relpath(export_folder, relpath):
    try:
        durable_unlink(_resolve(export_folder, relpath), missing_ok=True)
    except OSError:
        # Best-effort compensation. A referenced journal remains recoverable;
        # an unreferenced file is handled by full reconciliation.
        pass


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _site_row(conn, site_id, owner=None):
    sql = "SELECT export_folder,pending_export_folder FROM sites WHERE site_id=?"
    args = [site_id]
    if owner is not None:
        sql += " AND owner=?"
        args.append(owner)
    return conn.execute(sql, args).fetchone()


def recover_upload(queue_id, owner=None):
    """Finish one journaled attachment publication if possible.

    Returns a small status dict and is safe to call repeatedly.
    """
    with attachment_lock():
        conn = get_conn()
        try:
            row = conn.execute(
                """SELECT u.site_id,u.status,u.category,u.top_cm,u.bottom_cm,
                          u.publication_state,u.staged_relpath,u.target_relpath,
                          u.server_sha256,u.attachment_id,s.export_folder,
                          s.pending_export_folder,s.owner,
                          CASE WHEN u.updated_at <= datetime('now','-5 minutes')
                               THEN 1 ELSE 0 END AS reservation_stale
                   FROM attachment_uploads u JOIN sites s ON s.site_id=u.site_id
                   WHERE u.queue_id=?""", (queue_id,)).fetchone()
            if row is None or (owner is not None and row[12] != owner):
                return {"ok": False, "missing": True}
            (site_id, status, category, top_cm, bottom_cm, publication_state,
             staged_rel, target_rel, digest, attachment_id, export_folder,
             pending_export, _owner, reservation_stale) = row
            if status == "stored":
                return {"ok": True, "stored": True, "attachment_id": attachment_id}
            if status == "cancelled":
                return {"ok": False, "cancelled": True}
            if pending_export or not export_folder:
                return {"ok": False, "pending_archive": True}
            if publication_state in (None, "reserved"):
                # Do not mistake an active upload for a crash merely because a
                # list/reconcile request arrives while the browser is writing
                # its stage file. Only old reservations are reset.
                if publication_state == "reserved" and not reservation_stale:
                    return {"ok": False, "in_progress": True}
                if publication_state == "reserved":
                    conn.execute("BEGIN IMMEDIATE")
                    conn.execute(
                        """UPDATE attachment_uploads SET publication_state=NULL,
                           staged_relpath=NULL,target_relpath=NULL,server_sha256=NULL,
                           last_error='Upload interrupted before local staging completed',
                           updated_at=datetime('now') WHERE queue_id=? AND status='pending'
                             AND publication_state='reserved'
                             AND updated_at <= datetime('now','-5 minutes')""",
                        (queue_id,))
                    conn.execute("COMMIT")
                return {"ok": False, "retry": True}
            if publication_state != "staged" or not staged_rel or not target_rel or not digest:
                return {"ok": False, "conflict": True,
                        "msg": "Incomplete attachment publication journal."}

            staged = _resolve(export_folder, staged_rel)
            target = _resolve(export_folder, target_rel)
            staged_exists, target_exists = staged.is_file(), target.is_file()
            if staged_exists and not target_exists:
                ensure_directory(target.parent)
                durable_replace(staged, target)
            elif staged_exists and target_exists:
                if _sha256_file(staged) != digest or _sha256_file(target) != digest:
                    raise AttachmentConflict("Both staged and final attachment files exist with different bytes.")
                durable_unlink(staged, missing_ok=True)
            elif not staged_exists and target_exists:
                if _sha256_file(target) != digest:
                    raise AttachmentConflict("Published attachment checksum does not match its journal.")
            else:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    """UPDATE attachment_uploads SET publication_state=NULL,
                       staged_relpath=NULL,target_relpath=NULL,server_sha256=NULL,
                       last_error='Staged attachment disappeared before publication',
                       updated_at=datetime('now') WHERE queue_id=? AND status='pending'""",
                    (queue_id,))
                conn.execute("COMMIT")
                return {"ok": False, "retry": True, "missing_file": True}

            # The file is now at its final path. Finalize both DB records in one
            # SQLite transaction. On ordinary DB failure, move it back to stage
            # so the already-committed journal remains truthful and retryable.
            try:
                conn.execute("BEGIN IMMEDIATE")
                filename = Path(target_rel).name
                conn.execute(
                    """INSERT OR IGNORE INTO attachments
                       (site_id,category,filename,sha256,top_cm,bottom_cm,
                        storage_status,storage_error,pending_delete,trash_relpath)
                       VALUES (?,?,?,?,?,?,'stored',NULL,0,NULL)""",
                    (site_id, category, filename, digest, top_cm, bottom_cm))
                att = conn.execute(
                    """SELECT attachment_id,filename,COALESCE(storage_status,'stored') FROM attachments
                       WHERE site_id=? AND category=? AND sha256=?
                         AND COALESCE(top_cm,-999)=? AND COALESCE(bottom_cm,-999)=?""",
                    (site_id, category, digest,
                     top_cm if top_cm is not None else -999,
                     bottom_cm if bottom_cm is not None else -999)).fetchone()
                if not att:
                    raise AttachmentIntegrityError("Could not create attachment metadata.")
                replacing_missing = att[2] == "missing"
                if replacing_missing:
                    conn.execute(
                        """UPDATE attachments SET filename=?,storage_status='stored',
                           storage_error=NULL,pending_delete=0,trash_relpath=NULL
                           WHERE attachment_id=?""", (filename, att[0]))
                conn.execute(
                    """UPDATE attachment_uploads SET status='stored',attachment_id=?,
                       publication_state=NULL,staged_relpath=NULL,target_relpath=NULL,
                       last_error=NULL,updated_at=datetime('now') WHERE queue_id=?""",
                    (att[0], queue_id))
                conn.execute("COMMIT")
                # Another queue item may have published the same bytes first.
                # Keep the canonical file named by the winning healthy row and
                # remove this operation's redundant final file. A missing row,
                # however, is repaired by adopting the newly published file.
                duplicate = not replacing_missing and att[1] != filename
                if duplicate:
                    try:
                        durable_unlink(target, missing_ok=True)
                    except OSError:
                        pass
                return {"ok": True, "stored": True, "attachment_id": att[0],
                        "filename": filename if replacing_missing else att[1],
                        "duplicate": duplicate, "repaired_missing": replacing_missing}
            except Exception:
                try:
                    conn.execute("ROLLBACK")
                except Exception:
                    pass
                try:
                    ensure_directory(staged.parent)
                    if target.exists() and not staged.exists():
                        durable_replace(target, staged)
                except Exception:
                    pass
                raise
        finally:
            conn.close()


def begin_delete(site_id, attachment_id, owner):
    """Record and finish a recoverable user-requested attachment deletion."""
    with attachment_lock():
        conn = get_conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """SELECT a.category,a.filename,a.top_cm,a.bottom_cm,a.pending_delete,
                          a.trash_relpath,s.export_folder,s.pending_export_folder
                   FROM attachments a JOIN sites s ON s.site_id=a.site_id
                   WHERE a.site_id=? AND a.attachment_id=? AND s.owner=?""",
                (site_id, attachment_id, owner)).fetchone()
            if row is None:
                conn.execute("ROLLBACK")
                return {"ok": False, "missing": True}
            category, filename, top_cm, bottom_cm, pending, trash_rel, export_folder, pending_export = row
            if pending_export or not export_folder:
                conn.execute("ROLLBACK")
                return {"ok": False, "pending_archive": True}
            if not pending:
                trash_rel = trash_relpath(attachment_id, filename)
                conn.execute(
                    """UPDATE attachments SET pending_delete=1,trash_relpath=?,
                       storage_status='delete_pending',storage_error=NULL
                       WHERE attachment_id=?""", (trash_rel, attachment_id))
            conn.execute("COMMIT")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            conn.close()
    return recover_delete(site_id, attachment_id, owner)


def recover_delete(site_id, attachment_id, owner=None):
    with attachment_lock():
        conn = get_conn()
        try:
            row = conn.execute(
                """SELECT a.category,a.filename,a.top_cm,a.bottom_cm,a.pending_delete,
                          a.trash_relpath,s.export_folder,s.owner
                   FROM attachments a JOIN sites s ON s.site_id=a.site_id
                   WHERE a.site_id=? AND a.attachment_id=?""",
                (site_id, attachment_id)).fetchone()
            if row is None:
                return {"ok": True, "already_deleted": True}
            category, filename, top_cm, bottom_cm, pending, trash_rel, export_folder, row_owner = row
            if owner is not None and row_owner != owner:
                return {"ok": False, "missing": True}
            if not pending or not trash_rel:
                return {"ok": False, "msg": "Attachment is not marked for deletion."}
            original = _resolve(export_folder, target_relpath(category, top_cm, bottom_cm, filename))
            trash = _resolve(export_folder, trash_rel)
            original_exists, trash_exists = original.is_file(), trash.is_file()
            if original_exists and not trash_exists:
                ensure_directory(trash.parent)
                durable_replace(original, trash)
            elif original_exists and trash_exists:
                if _sha256_file(original) != _sha256_file(trash):
                    raise AttachmentConflict("Both live and trashed attachment files exist with different bytes.")
                durable_unlink(original, missing_ok=True)
            # If neither exists, deletion intent still wins: removing the stale
            # metadata repairs a DB row that already pointed to no file.
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    """UPDATE attachment_uploads SET status='cancelled',attachment_id=NULL,
                       publication_state=NULL,staged_relpath=NULL,target_relpath=NULL,
                       last_error='Attachment deleted by user',updated_at=datetime('now')
                       WHERE attachment_id=?""", (attachment_id,))
                conn.execute("DELETE FROM attachments WHERE attachment_id=?", (attachment_id,))
                conn.execute("COMMIT")
            except Exception:
                try:
                    conn.execute("ROLLBACK")
                except Exception:
                    pass
                # Keep the trash file and pending DB row for a later retry.
                raise
            cleanup_pending = False
            try:
                durable_unlink(trash, missing_ok=True)
            except FileNotFoundError:
                pass
            except OSError:
                cleanup_pending = True
            return {"ok": True, "deleted": True, "attachment_id": attachment_id,
                    "cleanup_pending": cleanup_pending}
        finally:
            conn.close()


def reconcile_site(site_id, owner=None, *, full=True, stale_seconds=24 * 3600):
    """Recover journals and scan while excluding pit-folder renames."""
    with attachment_lock():
        return _reconcile_site_locked(
            site_id, owner, full=full, stale_seconds=stale_seconds
        )


def _reconcile_site_locked(site_id, owner=None, *, full=True, stale_seconds=24 * 3600):
    """Implementation for ``reconcile_site``; caller holds the lifecycle lock."""
    report = {"site_id": site_id, "recovered_uploads": 0, "recovered_deletes": 0,
              "missing": [], "quarantined": [], "removed_temps": [], "errors": []}
    conn = get_conn()
    try:
        site = _site_row(conn, site_id, owner)
        if site is None:
            return {**report, "ok": False, "missing_site": True}
        export_folder, pending_export = site
        if pending_export or not export_folder:
            return {**report, "ok": False, "pending_archive": True}
        uploads = conn.execute(
            """SELECT queue_id FROM attachment_uploads
               WHERE site_id=? AND status='pending' AND publication_state IS NOT NULL""",
            (site_id,)).fetchall()
        deletes = conn.execute(
            "SELECT attachment_id FROM attachments WHERE site_id=? AND pending_delete=1",
            (site_id,)).fetchall()
    finally:
        conn.close()

    for (queue_id,) in uploads:
        try:
            result = recover_upload(queue_id, owner)
            if result.get("stored"):
                report["recovered_uploads"] += 1
        except Exception as exc:
            report["errors"].append(f"upload {queue_id}: {exc}")
    for (attachment_id,) in deletes:
        try:
            result = recover_delete(site_id, attachment_id, owner)
            if result.get("deleted"):
                report["recovered_deletes"] += 1
        except Exception as exc:
            report["errors"].append(f"delete {attachment_id}: {exc}")

    if not full or report["errors"]:
        report["ok"] = not report["errors"]
        return report

    with attachment_lock():
        conn = get_conn()
        try:
            rows = conn.execute(
                """SELECT attachment_id,category,filename,top_cm,bottom_cm
                   FROM attachments WHERE site_id=? AND pending_delete=0""", (site_id,)).fetchall()
            referenced_stage = {r[0] for r in conn.execute(
                """SELECT staged_relpath FROM attachment_uploads
                   WHERE site_id=? AND staged_relpath IS NOT NULL""", (site_id,)).fetchall()}
            referenced_trash = {r[0] for r in conn.execute(
                """SELECT trash_relpath FROM attachments
                   WHERE site_id=? AND trash_relpath IS NOT NULL""", (site_id,)).fetchall()}
            expected = {}
            for att_id, category, filename, top_cm, bottom_cm in rows:
                rel = target_relpath(category, top_cm, bottom_cm, filename)
                expected[rel] = att_id
                path = _resolve(export_folder, rel)
                if not path.is_file():
                    report["missing"].append(rel)
                    conn.execute(
                        """UPDATE attachments SET storage_status='missing',
                           storage_error='File missing during reconciliation'
                           WHERE attachment_id=?""", (att_id,))
                    conn.execute(
                        """UPDATE attachment_uploads SET status='pending',
                           publication_state=NULL,staged_relpath=NULL,target_relpath=NULL,
                           server_sha256=NULL,
                           last_error='Stored file is missing; reselect and upload it again',
                           updated_at=datetime('now') WHERE attachment_id=?""", (att_id,))
                else:
                    conn.execute(
                        """UPDATE attachments SET storage_status='stored',storage_error=NULL
                           WHERE attachment_id=?""", (att_id,))

            uploads_dir = _pit_root(export_folder) / "uploads"
            if uploads_dir.is_dir():
                stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
                for path in [p for p in uploads_dir.rglob("*") if p.is_file()]:
                    rel = path.relative_to(_pit_root(export_folder)).as_posix()
                    if rel in expected:
                        continue
                    qrel = f".attachment-orphans/{stamp}/{rel}"
                    qpath = _resolve(export_folder, qrel)
                    ensure_directory(qpath.parent)
                    durable_replace(path, qpath)
                    report["quarantined"].append(rel)

            cutoff = time.time() - stale_seconds
            for dirname, referenced in ((".attachment-staging", referenced_stage),
                                        (".attachment-trash", referenced_trash)):
                folder = _pit_root(export_folder) / dirname
                if not folder.is_dir():
                    continue
                for path in [p for p in folder.rglob("*") if p.is_file()]:
                    rel = path.relative_to(_pit_root(export_folder)).as_posix()
                    if rel in referenced or path.stat().st_mtime >= cutoff:
                        continue
                    durable_unlink(path, missing_ok=True)
                    report["removed_temps"].append(rel)
            conn.commit()
        finally:
            conn.close()
    report["ok"] = not report["errors"]
    return report


def reconcile_all(*, full=False):
    conn = get_conn()
    try:
        sites = [r[0] for r in conn.execute(
            "SELECT site_id FROM sites WHERE export_folder IS NOT NULL AND pending_export_folder IS NULL")]
    finally:
        conn.close()
    return [reconcile_site(site_id, full=full) for site_id in sites]
