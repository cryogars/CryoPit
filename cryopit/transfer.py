"""One-way, revision-aware CryoPit field transfer bundles.

This module deliberately does not merge SQLite rows.  A bundle carries stable
UUID identities, canonical scientific JSON, revision ancestry, attachment
metadata, and checksum-verified files.  The destination rebuilds its local
normalized rows and accepts updates only when they fast-forward the revision it
already has.

CLI examples::

    python -m cryopit.transfer export --output field-day.zip
    python -m cryopit.transfer inspect field-day.zip --owner <sso-subject>
    python -m cryopit.transfer import field-day.zip --owner <sso-subject> --dry-run
    python -m cryopit.transfer import field-day.zip --owner <sso-subject>
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import uuid
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from . import __version__
from .attachment_storage import target_relpath
from .auth import normalize_identity
from .config import DB_PATH, DEV_USER, EXPORT_DIR
from .db import get_conn, init_db
from .export import _safe_name
from .repository import (_ATTACH_LIMITS, _ATTACH_TOTAL, _STRAT_PER_LAYER,
                         _site_values, _validate_payload, _write_children)
from .revisions import PAYLOAD_VERSION, canonical_json, ensure_installation_id, record_hash
from .storage_lifecycle import (durable_rename, durable_replace, durable_rmtree,
                                durable_unlink, ensure_directory, fsync_file,
                                storage_lock, sync_tree)

FORMAT = "cryopit-transfer-v1"
MANIFEST = "manifest.json"
MAX_MEMBERS = 20000
MAX_UNCOMPRESSED = 50 * 1024 * 1024 * 1024


class TransferError(RuntimeError):
    pass


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _zip_member_digest(zf: zipfile.ZipFile, info: zipfile.ZipInfo):
    h = hashlib.sha256()
    size = 0
    with zf.open(info, "r") as src:
        for block in iter(lambda: src.read(1024 * 1024), b""):
            size += len(block)
            h.update(block)
    return size, h.hexdigest()


def _safe_member(info: zipfile.ZipInfo) -> bool:
    name = info.filename
    p = PurePosixPath(name)
    if not name or name.startswith(("/", "\\")) or "\\" in name:
        return False
    if any(part in {"", ".", ".."} for part in p.parts):
        return False
    mode = (info.external_attr >> 16) & 0o170000
    if mode == 0o120000:  # symlink
        return False
    if info.flag_bits & 0x1:  # encrypted
        return False
    return True


def _zip_members(zf: zipfile.ZipFile):
    infos = zf.infolist()
    if len(infos) > MAX_MEMBERS:
        raise TransferError("Transfer bundle contains too many files.")
    total = 0
    seen = set()
    for info in infos:
        if not _safe_member(info):
            raise TransferError(f"Unsafe transfer member: {info.filename!r}")
        if info.filename in seen:
            raise TransferError(f"Duplicate transfer member: {info.filename}")
        seen.add(info.filename)
        total += info.file_size
        if total > MAX_UNCOMPRESSED:
            raise TransferError("Transfer bundle expands beyond the supported size limit.")
        yield info


def _attachment_key(item):
    return (
        item["category"], item["sha256"],
        item.get("top_cm"), item.get("bottom_cm"),
    )


def _key_json(key):
    return json.dumps(list(key), separators=(",", ":"), ensure_ascii=False)


def _bundle_capacity_error(record):
    """Validate one bundle's stored plus pending attachment inventory."""
    stored = list(record.get("attachments") or [])
    pending = [
        {
            "category": upload.get("category"),
            "top_cm": upload.get("top_cm"),
            "bottom_cm": upload.get("bottom_cm"),
            "filename": upload.get("original_filename"),
            "mime_type": upload.get("mime_type"),
        }
        for upload in (record.get("attachment_uploads") or [])
        if upload.get("status") == "pending"
    ]
    items = stored + pending
    if len(items) > _ATTACH_TOTAL:
        return f"Attachment manifest exceeds the {_ATTACH_TOTAL}-file pit limit."
    for category, limit in _ATTACH_LIMITS.items():
        count = sum(1 for item in items if item.get("category") == category)
        if count > limit:
            return f"Attachment manifest exceeds the {limit}-file {category} limit."
    intervals = Counter(
        (item.get("top_cm"), item.get("bottom_cm"))
        for item in items if item.get("category") == "stratigraphy"
    )
    for interval, count in intervals.items():
        if count > _STRAT_PER_LAYER:
            return (f"Attachment manifest exceeds the {_STRAT_PER_LAYER}-file "
                    f"stratigraphy limit for interval {interval}.")
    sheets = [item for item in items if item.get("category") == "sheet"]
    pdfs = [item for item in sheets if
            item.get("mime_type") == "application/pdf" or
            str(item.get("filename") or "").lower().endswith(".pdf")]
    if pdfs and len(sheets) > 1:
        return "Attachment manifest mixes a pit-sheet PDF with other sheet files."
    return None


def _desired_folder(payload):
    m = payload.get("meta") or {}
    dstr = (m.get("date") or "").replace("-", "")
    return _safe_name(
        f"{m.get('campaign') or 'campaign'}_{m.get('pit_id') or 'pit'}_{dstr}", "pit"
    )


def _payload_digest(payload):
    return record_hash({k: v for k, v in payload.items()
                        if k not in {"site_id", "overwrite", "attachment_manifest"}})


def _read_marker(folder: Path):
    try:
        return json.loads((folder / ".cryopit-archive.json").read_text("utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def _source_attachment_rows(conn, site_id, folder: Path):
    rows = conn.execute(
        """SELECT attachment_id,category,filename,sha256,top_cm,bottom_cm,
                  uploaded_at,COALESCE(storage_status,'stored'),storage_error,
                  pending_delete,trash_relpath
             FROM attachments WHERE site_id=? ORDER BY attachment_id""",
        (site_id,),
    ).fetchall()
    out = []
    referenced = set()
    by_id = {}
    for row in rows:
        (attachment_id, category, filename, digest, top, bottom, uploaded_at,
         status, storage_error, pending_delete, trash_relpath) = row
        if pending_delete or trash_relpath:
            raise TransferError(
                f"Pit {site_id} has an unfinished attachment deletion; reconcile it first."
            )
        if status != "stored" or storage_error:
            raise TransferError(
                f"Pit {site_id} has an attachment that is not safely stored; reconcile it first."
            )
        rel = target_relpath(category, top, bottom, filename)
        path = folder / rel
        if not path.is_file():
            raise TransferError(f"Pit {site_id} is missing stored attachment {rel}.")
        actual = _sha256_file(path)
        if actual != digest:
            raise TransferError(f"Pit {site_id} attachment checksum mismatch: {rel}.")
        item = {
            "category": category, "filename": filename, "sha256": digest,
            "top_cm": top, "bottom_cm": bottom, "uploaded_at": uploaded_at,
            "relpath": rel,
        }
        out.append(item)
        referenced.add(rel)
        by_id[attachment_id] = _attachment_key(item)

    uploads_root = folder / "uploads"
    actual_files = set()
    if uploads_root.exists():
        for path in uploads_root.rglob("*"):
            if path.is_symlink():
                raise TransferError(f"Symlinks are not supported in pit folders: {path}")
            if path.is_file():
                actual_files.add(path.relative_to(folder).as_posix())
    if actual_files != referenced:
        extra = sorted(actual_files - referenced)
        missing = sorted(referenced - actual_files)
        details = []
        if extra:
            details.append("untracked files: " + ", ".join(extra[:3]))
        if missing:
            details.append("missing files: " + ", ".join(missing[:3]))
        raise TransferError(
            f"Pit {site_id} attachment folder does not match SQLite ({'; '.join(details)})."
        )
    return out, by_id


def _source_upload_rows(conn, site_id, attachment_by_id):
    rows = conn.execute(
        """SELECT queue_id,category,original_filename,mime_type,size_bytes,
                  client_sha256,top_cm,bottom_cm,status,attachment_id,last_error,
                  publication_state,staged_relpath,target_relpath,server_sha256,
                  created_at,updated_at
             FROM attachment_uploads WHERE site_id=? ORDER BY created_at,queue_id""",
        (site_id,),
    ).fetchall()
    out = []
    for row in rows:
        (queue_id, category, filename, mime, size, client_sha, top, bottom,
         status, attachment_id, last_error, publication_state, staged_rel,
         target_rel, server_sha, created_at, updated_at) = row
        if publication_state or staged_rel or target_rel or server_sha:
            raise TransferError(
                f"Pit {site_id} has an unfinished upload journal; reconcile it first."
            )
        attachment_key = None
        if status == "stored":
            attachment_key = attachment_by_id.get(attachment_id)
            if attachment_key is None:
                raise TransferError(
                    f"Pit {site_id} has a stored queue item without a stored attachment."
                )
        out.append({
            "queue_id": queue_id, "category": category,
            "original_filename": filename, "mime_type": mime,
            "size_bytes": size, "client_sha256": client_sha,
            "top_cm": top, "bottom_cm": bottom, "status": status,
            "attachment_key": list(attachment_key) if attachment_key else None,
            "last_error": last_error, "created_at": created_at,
            "updated_at": updated_at,
        })
    return out


def _source_revisions(conn, site_id, current_revision_id):
    rows = conn.execute(
        """SELECT revision_id,parent_revision_id,revision_number,payload_version,
                  record_hash,raw_json,source_installation_id,source_owner,
                  created_at,imported_at,import_bundle_id
             FROM site_revisions WHERE site_id=? ORDER BY revision_number""",
        (site_id,),
    ).fetchall()
    if not rows or not current_revision_id:
        raise TransferError(f"Pit {site_id} has no revision history; initialize the database first.")
    out = []
    previous = None
    for row in rows:
        (revision_id, parent, number, payload_version, digest, raw_json,
         source_installation_id, source_owner, created_at, imported_at,
         import_bundle_id) = row
        try:
            payload = json.loads(raw_json)
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise TransferError(f"Revision {revision_id} has invalid JSON.") from exc
        if record_hash(payload) != digest:
            raise TransferError(f"Revision {revision_id} checksum does not match its JSON.")
        if number != len(out) + 1 or parent != previous:
            raise TransferError(f"Pit {site_id} revision ancestry is not a continuous chain.")
        out.append({
            "revision_id": revision_id, "parent_revision_id": parent,
            "revision_number": number, "payload_version": payload_version,
            "record_hash": digest, "payload": payload,
            "source_installation_id": source_installation_id,
            "source_owner": source_owner, "created_at": created_at,
            "imported_at": imported_at, "import_bundle_id": import_bundle_id,
        })
        previous = revision_id
    if out[-1]["revision_id"] != current_revision_id:
        raise TransferError(f"Pit {site_id} current revision is not the tip of its history.")
    return out


def create_transfer(output, *, db_path=DB_PATH, export_dir=EXPORT_DIR,
                    owner=DEV_USER, site_ids=None):
    """Create one checksum-verified field-to-central transfer ZIP."""
    output = Path(output).resolve()
    export_dir = Path(export_dir).resolve()
    db_path = Path(db_path).resolve()
    try:
        owner = normalize_identity(owner)
    except ValueError as exc:
        raise TransferError("A valid source owner is required for transfer export.") from exc
    export_dir.mkdir(parents=True, exist_ok=True)
    try:
        output.relative_to(export_dir)
    except ValueError:
        pass
    else:
        raise TransferError("Write transfer bundles outside CRYOPIT_EXPORT_DIR.")

    bundle_id = str(uuid.uuid4())
    maintenance = _maintenance_mode(
        export_dir, bundle_id, operation="transfer-export")
    maintenance.__enter__()
    lock = storage_lock(export_dir)
    lock_entered = False
    conn = None
    try:
        init_db(db_path)
        lock.__enter__()
        lock_entered = True
        conn = get_conn(db_path)
        installation_id = ensure_installation_id(conn)
        params = [owner]
        sql = """SELECT site_id,pit_id,raw_json,current_revision_id,export_folder,
                        pending_export_folder,created_at,updated_at
                   FROM sites WHERE owner=?"""
        try:
            wanted = [str(uuid.UUID(str(x))) for x in (site_ids or [])]
        except (ValueError, TypeError, AttributeError) as exc:
            raise TransferError("Every --site-id must be a valid UUID.") from exc
        if wanted:
            sql += " AND site_id IN (%s)" % ",".join("?" for _ in wanted)
            params.extend(wanted)
        sql += " ORDER BY date,pit_id"
        sites = conn.execute(sql, params).fetchall()
        if wanted and len(sites) != len(set(wanted)):
            found = {r[0] for r in sites}
            missing = sorted(set(wanted) - found)
            raise TransferError("Requested pits were not found for this owner: " + ", ".join(missing))
        if not sites:
            raise TransferError(f"No archived pits found for owner {owner!r}.")

        with tempfile.TemporaryDirectory(prefix="cryopit-transfer-") as tmp:
            stage = Path(tmp)
            pit_summaries = []
            for (site_id, pit_id, raw_json, current_revision_id, export_folder,
                 pending_export_folder, created_at, updated_at) in sites:
                if pending_export_folder or not export_folder:
                    raise TransferError(
                        f"Pit {pit_id} ({site_id}) needs archive recovery before transfer."
                    )
                try:
                    payload = json.loads(raw_json or "{}")
                except (ValueError, TypeError, json.JSONDecodeError) as exc:
                    raise TransferError(f"Pit {site_id} has unreadable raw JSON.") from exc
                err = _validate_payload(payload)
                if err:
                    raise TransferError(f"Pit {site_id} failed current validation: {err}")
                desired = _desired_folder(payload)
                if export_folder != desired:
                    raise TransferError(
                        f"Pit {site_id} recorded folder {export_folder!r} does not match its payload; re-archive first."
                    )
                folder = export_dir / export_folder
                if not folder.is_dir():
                    raise TransferError(f"Pit {site_id} recorded folder is missing: {folder}")
                marker = _read_marker(folder)
                if not marker or marker.get("site_id") != site_id or \
                        marker.get("payload_sha256") != _payload_digest(payload):
                    raise TransferError(f"Pit {site_id} archive marker does not match its current record.")
                for hidden in folder.rglob("*"):
                    rel = hidden.relative_to(folder)
                    if hidden.is_symlink():
                        raise TransferError(f"Symlinks are not supported in pit folders: {hidden}")
                    if hidden.is_file() and any(part.startswith(".") for part in rel.parts) \
                            and rel.as_posix() != ".cryopit-archive.json":
                        raise TransferError(
                            f"Pit {site_id} contains unfinished hidden storage state: {rel.as_posix()}"
                        )
                if not (folder / "csv").is_dir() or not (folder / "figures").is_dir():
                    raise TransferError(f"Pit {site_id} archive is missing generated output directories.")

                attachments, attachment_by_id = _source_attachment_rows(conn, site_id, folder)
                uploads = _source_upload_rows(conn, site_id, attachment_by_id)
                revisions = _source_revisions(conn, site_id, current_revision_id)
                if canonical_json(revisions[-1]["payload"]) != canonical_json(payload):
                    raise TransferError(f"Pit {site_id} raw JSON differs from its current revision.")

                prefix = f"pits/{site_id}/folder"
                target_folder = stage / prefix
                shutil.copytree(folder, target_folder)
                record = {
                    "format": "cryopit-pit-transfer-v1",
                    "site_id": site_id, "pit_id": pit_id,
                    "source_owner": owner, "source_installation_id": installation_id,
                    "export_folder": export_folder,
                    "created_at": created_at, "updated_at": updated_at,
                    "current_revision_id": current_revision_id,
                    "revisions": revisions,
                    "attachments": attachments,
                    "attachment_uploads": uploads,
                    "folder_prefix": prefix,
                }
                record_path = stage / f"pits/{site_id}/record.json"
                record_path.parent.mkdir(parents=True, exist_ok=True)
                record_path.write_text(json.dumps(record, indent=2, sort_keys=True,
                                                  ensure_ascii=False) + "\n", "utf-8")
                pit_summaries.append({
                    "site_id": site_id, "pit_id": pit_id,
                    "current_revision_id": current_revision_id,
                    "record_hash": revisions[-1]["record_hash"],
                    "record_path": f"pits/{site_id}/record.json",
                    "folder_prefix": prefix,
                    "attachments": len(attachments),
                    "pending_uploads": sum(1 for u in uploads if u["status"] == "pending"),
                })

            files = []
            for path in sorted(stage.rglob("*")):
                if path.is_symlink():
                    raise TransferError(f"Symlinks are not supported in transfer bundles: {path}")
                if path.is_file():
                    rel = path.relative_to(stage).as_posix()
                    files.append({"path": rel, "size": path.stat().st_size,
                                  "sha256": _sha256_file(path)})
            manifest = {
                "format": FORMAT, "bundle_id": bundle_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "cryopit_version": __version__,
                "source_installation_id": installation_id,
                "source_owner": owner, "payload_version": PAYLOAD_VERSION,
                "pits": pit_summaries, "files": files,
            }
            output.parent.mkdir(parents=True, exist_ok=True)
            temp_output = output.with_name(f".{output.name}.tmp-{os.getpid()}")
            try:
                with zipfile.ZipFile(temp_output, "w", compression=zipfile.ZIP_DEFLATED,
                                     compresslevel=6, allowZip64=True) as zf:
                    for item in files:
                        zf.write(stage / item["path"], item["path"])
                    zf.writestr(MANIFEST,
                                json.dumps(manifest, indent=2, sort_keys=True) + "\n")
                fsync_file(temp_output)
                # Verify the generated artifact before publishing its final
                # name. This catches source-row inconsistencies as well as ZIP
                # construction defects while the storage lock is still held.
                verify_transfer(temp_output)
                durable_replace(temp_output, output)
            finally:
                try:
                    temp_output.unlink()
                except FileNotFoundError:
                    pass
            return manifest
    finally:
        if conn is not None:
            conn.close()
        if lock_entered:
            lock.__exit__(None, None, None)
        maintenance.__exit__(None, None, None)


def verify_transfer(bundle):
    """Validate ZIP structure, every checksum, revision ancestry and payload."""
    bundle = Path(bundle)
    if not bundle.is_file():
        raise TransferError(f"Transfer bundle does not exist: {bundle}")
    records = {}
    with zipfile.ZipFile(bundle) as zf:
        members = list(_zip_members(zf))
        try:
            manifest = json.loads(zf.read(MANIFEST))
        except KeyError as exc:
            raise TransferError("Transfer manifest is missing.") from exc
        except (ValueError, json.JSONDecodeError) as exc:
            raise TransferError("Transfer manifest is invalid JSON.") from exc
        if manifest.get("format") != FORMAT:
            raise TransferError("Unsupported CryoPit transfer format.")
        try:
            str(uuid.UUID(manifest["bundle_id"]))
            str(uuid.UUID(manifest["source_installation_id"]))
            manifest_owner = normalize_identity(manifest["source_owner"])
        except (KeyError, ValueError, TypeError, AttributeError) as exc:
            raise TransferError("Transfer manifest has invalid bundle, installation, or owner identity.") from exc
        if manifest_owner != manifest.get("source_owner"):
            raise TransferError("Transfer manifest source owner is not canonical.")
        if manifest.get("payload_version") != PAYLOAD_VERSION:
            raise TransferError("Transfer manifest uses an unsupported payload version.")
        files = manifest.get("files")
        pits = manifest.get("pits")
        if not isinstance(files, list) or not isinstance(pits, list) or not pits:
            raise TransferError("Transfer manifest files and pits must be non-empty lists.")
        declared = {}
        for item in files:
            if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                raise TransferError("Transfer file manifest contains an invalid entry.")
            if item["path"] in declared:
                raise TransferError(f"Transfer file manifest repeats {item['path']}.")
            declared[item["path"]] = item
        actual = {i.filename for i in members if not i.is_dir()}
        if actual != set(declared) | {MANIFEST}:
            raise TransferError("Transfer contains undeclared or missing files.")
        for name, item in declared.items():
            info = zf.getinfo(name)
            size, digest = _zip_member_digest(zf, info)
            if size != item.get("size") or digest != item.get("sha256"):
                raise TransferError(f"Transfer checksum mismatch: {name}")

        seen_sites = set()
        seen_pit_ids = set()
        seen_export_folders = set()
        seen_revision_ids = set()
        seen_queue_ids = set()
        for summary in pits:
            if not isinstance(summary, dict):
                raise TransferError("Transfer pit summary must be an object.")
            try:
                site_id = str(uuid.UUID(summary["site_id"]))
            except (KeyError, ValueError, TypeError, AttributeError) as exc:
                raise TransferError("Transfer contains an invalid site_id.") from exc
            if site_id in seen_sites:
                raise TransferError(f"Transfer repeats site_id {site_id}.")
            seen_sites.add(site_id)
            record_path = summary.get("record_path")
            expected_record_path = f"pits/{site_id}/record.json"
            if record_path != expected_record_path or record_path not in declared:
                raise TransferError(f"Transfer record is missing or misplaced for site {site_id}.")
            try:
                record = json.loads(zf.read(record_path))
            except (ValueError, json.JSONDecodeError) as exc:
                raise TransferError(f"Pit record is invalid JSON: {record_path}") from exc
            if record.get("format") != "cryopit-pit-transfer-v1" or record.get("site_id") != site_id:
                raise TransferError(f"Pit record identity mismatch: {record_path}")
            revisions = record.get("revisions")
            if not isinstance(revisions, list) or not revisions:
                raise TransferError(f"Pit {site_id} has no revision chain.")
            previous = None
            seen_revisions = set()
            for index, revision in enumerate(revisions, 1):
                try:
                    rid = str(uuid.UUID(revision["revision_id"]))
                    parent = revision.get("parent_revision_id")
                    if parent is not None:
                        parent = str(uuid.UUID(parent))
                except (KeyError, ValueError, TypeError, AttributeError) as exc:
                    raise TransferError(f"Pit {site_id} has an invalid revision identity.") from exc
                if rid in seen_revisions or rid in seen_revision_ids or \
                        revision.get("revision_number") != index or parent != previous:
                    raise TransferError(f"Pit {site_id} revision chain is not continuous or globally unique.")
                if revision.get("payload_version") != PAYLOAD_VERSION:
                    raise TransferError(f"Pit {site_id} uses unsupported payload version.")
                payload = revision.get("payload")
                if not isinstance(payload, dict) or record_hash(payload) != revision.get("record_hash"):
                    raise TransferError(f"Pit {site_id} revision {rid} failed its record hash.")
                try:
                    str(uuid.UUID(revision["source_installation_id"]))
                    revision_owner = normalize_identity(revision["source_owner"])
                except (KeyError, ValueError, TypeError, AttributeError) as exc:
                    raise TransferError(
                        f"Pit {site_id} revision {rid} has invalid source provenance."
                    ) from exc
                if revision_owner != revision.get("source_owner"):
                    raise TransferError(
                        f"Pit {site_id} revision {rid} has a non-canonical source owner."
                    )
                seen_revisions.add(rid)
                seen_revision_ids.add(rid)
                previous = rid
            if record.get("current_revision_id") != revisions[-1]["revision_id"]:
                raise TransferError(f"Pit {site_id} current revision is not the chain tip.")
            if (summary.get("current_revision_id") != record["current_revision_id"] or
                    summary.get("record_hash") != revisions[-1]["record_hash"]):
                raise TransferError(f"Pit {site_id} summary does not match its current revision.")
            if (record.get("source_owner") != manifest.get("source_owner") or
                    record.get("source_installation_id") != manifest.get("source_installation_id")):
                raise TransferError(f"Pit {site_id} source provenance does not match the bundle.")
            payload = revisions[-1]["payload"]
            payload_pit_id = (payload.get("meta") or {}).get("pit_id")
            if not payload_pit_id or record.get("pit_id") != payload_pit_id or \
                    summary.get("pit_id") != payload_pit_id:
                raise TransferError(f"Pit {site_id} human Pit ID is inconsistent across the bundle.")
            if payload_pit_id in seen_pit_ids:
                raise TransferError(f"Transfer repeats human Pit ID {payload_pit_id!r}.")
            seen_pit_ids.add(payload_pit_id)
            err = _validate_payload(payload)
            if err:
                raise TransferError(f"Pit {site_id} failed current validation: {err}")
            desired = _desired_folder(payload)
            if desired in seen_export_folders:
                raise TransferError(f"Transfer repeats derived export folder {desired!r}.")
            seen_export_folders.add(desired)
            if record.get("export_folder") != desired:
                raise TransferError(f"Pit {site_id} transfer folder does not match its payload.")
            prefix = record.get("folder_prefix")
            expected_prefix = f"pits/{site_id}/folder"
            if prefix != expected_prefix or prefix != summary.get("folder_prefix"):
                raise TransferError(f"Pit {site_id} folder prefix mismatch.")
            marker_path = f"{prefix}/.cryopit-archive.json"
            if marker_path not in declared:
                raise TransferError(f"Pit {site_id} archive marker is missing.")
            try:
                marker = json.loads(zf.read(marker_path))
            except (ValueError, json.JSONDecodeError) as exc:
                raise TransferError(f"Pit {site_id} archive marker is invalid JSON.") from exc
            if marker.get("site_id") != site_id or marker.get("payload_sha256") != _payload_digest(payload):
                raise TransferError(f"Pit {site_id} archive marker does not match its payload.")
            folder_files = {name for name in declared if name.startswith(prefix + "/")}
            if not any(name.startswith(prefix + "/csv/") for name in folder_files) or \
                    not any(name.startswith(prefix + "/figures/") for name in folder_files):
                raise TransferError(f"Pit {site_id} archive lacks generated CSV or figure files.")
            hidden = [name for name in folder_files
                      if any(part.startswith(".") for part in PurePosixPath(name).parts[len(PurePosixPath(prefix).parts):])
                      and name != marker_path]
            if hidden:
                raise TransferError(f"Pit {site_id} contains unsupported hidden storage state.")
            attachment_keys = set()
            attachment_paths = set()
            attachments = record.get("attachments") or []
            if not isinstance(attachments, list) or summary.get("attachments") != len(attachments):
                raise TransferError(f"Pit {site_id} attachment summary is inconsistent.")
            for item in attachments:
                if not isinstance(item, dict) or item.get("category") not in _ATTACH_LIMITS:
                    raise TransferError(f"Pit {site_id} has invalid attachment metadata.")
                digest = item.get("sha256")
                if not isinstance(digest, str) or len(digest) != 64 or \
                        any(ch not in "0123456789abcdef" for ch in digest.lower()):
                    raise TransferError(f"Pit {site_id} has an invalid attachment checksum.")
                key = _attachment_key(item)
                if key in attachment_keys:
                    raise TransferError(f"Pit {site_id} repeats an attachment identity.")
                attachment_keys.add(key)
                rel = item.get("relpath")
                if rel != target_relpath(item.get("category"), item.get("top_cm"),
                                         item.get("bottom_cm"), item.get("filename")):
                    raise TransferError(f"Pit {site_id} attachment path is inconsistent.")
                full = f"{prefix}/{rel}"
                if full not in declared or declared[full]["sha256"] != item.get("sha256"):
                    raise TransferError(f"Pit {site_id} attachment bytes are missing or mismatched: {rel}")
                attachment_paths.add(rel)
            expected_upload_paths = {f"{prefix}/{rel}" for rel in attachment_paths}
            actual_upload_paths = {name for name in folder_files
                                   if name.startswith(prefix + "/uploads/")}
            if actual_upload_paths != expected_upload_paths:
                raise TransferError(f"Pit {site_id} upload folder does not match its attachment manifest.")
            uploads = record.get("attachment_uploads") or []
            if not isinstance(uploads, list):
                raise TransferError(f"Pit {site_id} upload manifest must be a list.")
            pending_count = 0
            for upload in uploads:
                if not isinstance(upload, dict):
                    raise TransferError(f"Pit {site_id} has invalid upload metadata.")
                try:
                    queue_id = str(uuid.UUID(upload["queue_id"]))
                except (KeyError, ValueError, TypeError, AttributeError) as exc:
                    raise TransferError(f"Pit {site_id} has an invalid upload queue ID.") from exc
                if queue_id in seen_queue_ids:
                    raise TransferError(f"Transfer repeats upload queue ID {queue_id}.")
                seen_queue_ids.add(queue_id)
                if upload.get("status") == "stored":
                    key = tuple(upload.get("attachment_key") or [])
                    if key not in attachment_keys:
                        raise TransferError(f"Pit {site_id} stored upload has no attachment identity.")
                elif upload.get("status") not in {"pending", "cancelled"}:
                    raise TransferError(f"Pit {site_id} has an invalid upload status.")
                if upload.get("status") == "pending":
                    pending_count += 1
            if summary.get("pending_uploads") != pending_count:
                raise TransferError(f"Pit {site_id} pending-upload summary is inconsistent.")
            capacity_error = _bundle_capacity_error(record)
            if capacity_error:
                raise TransferError(f"Pit {site_id}: {capacity_error}")
            records[site_id] = record
        allowed = set()
        for site_id, record in records.items():
            allowed.add(f"pits/{site_id}/record.json")
            prefix = record["folder_prefix"] + "/"
            allowed.update(name for name in declared if name.startswith(prefix))
        unsupported = set(declared) - allowed
        if unsupported:
            raise TransferError(
                "Transfer contains files outside its declared pit records: "
                + ", ".join(sorted(unsupported)[:3])
            )
    return {"manifest": manifest, "records": records,
            "bundle_sha256": _sha256_file(bundle)}


def _destination_attachment_keys(conn, site_id):
    return {
        (r[0], r[1], r[2], r[3])
        for r in conn.execute(
            "SELECT category,sha256,top_cm,bottom_cm FROM attachments WHERE site_id=?",
            (site_id,),
        )
    }


def _capacity_error(conn, record):
    """Return a merge-capacity conflict without changing destination state."""
    site_id = record["site_id"]
    stored = [
        {"category": r[0], "sha256": r[1], "top_cm": r[2],
         "bottom_cm": r[3], "filename": r[4]}
        for r in conn.execute(
            "SELECT category,sha256,top_cm,bottom_cm,filename FROM attachments WHERE site_id=?",
            (site_id,),
        )
    ]
    stored_keys = {_attachment_key(item) for item in stored}
    for item in record.get("attachments") or []:
        if _attachment_key(item) not in stored_keys:
            stored.append(item)
            stored_keys.add(_attachment_key(item))

    pending = [
        {"queue_id": r[0], "category": r[1], "top_cm": r[2],
         "bottom_cm": r[3], "filename": r[4], "mime_type": r[5]}
        for r in conn.execute(
            """SELECT queue_id,category,top_cm,bottom_cm,original_filename,mime_type
                 FROM attachment_uploads WHERE site_id=? AND status='pending'""",
            (site_id,),
        )
    ]
    existing_upload_ids = {r[0] for r in conn.execute(
        "SELECT queue_id FROM attachment_uploads WHERE site_id=?", (site_id,)
    )}
    pending_ids = {item["queue_id"] for item in pending}
    for upload in record.get("attachment_uploads") or []:
        if upload.get("status") == "pending" and upload["queue_id"] not in existing_upload_ids:
            pending.append({
                "queue_id": upload["queue_id"], "category": upload["category"],
                "top_cm": upload.get("top_cm"), "bottom_cm": upload.get("bottom_cm"),
                "filename": upload["original_filename"],
                "mime_type": upload.get("mime_type"),
            })
            pending_ids.add(upload["queue_id"])

    if len(stored) + len(pending) > _ATTACH_TOTAL:
        return f"Combined attachment set exceeds the {_ATTACH_TOTAL}-file pit limit."
    for category, limit in _ATTACH_LIMITS.items():
        count = sum(1 for item in stored + pending if item["category"] == category)
        if count > limit:
            return f"Combined {category} set exceeds the {limit}-file limit."
    intervals = Counter(
        (item.get("top_cm"), item.get("bottom_cm"))
        for item in stored + pending if item["category"] == "stratigraphy"
    )
    for interval, count in intervals.items():
        if count > _STRAT_PER_LAYER:
            return (f"Combined stratigraphy photographs exceed the {_STRAT_PER_LAYER}-file "
                    f"limit for interval {interval}.")
    sheets = [item for item in stored + pending if item["category"] == "sheet"]
    sheet_pdfs = [item for item in sheets if
                  item.get("mime_type") == "application/pdf" or
                  str(item.get("filename") or "").lower().endswith(".pdf")]
    if sheet_pdfs and len(sheets) > 1:
        return "Combined pit-sheet set mixes a PDF with other sheet files."
    return None


def _classify_record(conn, record, owner, export_dir: Path):
    site_id = record["site_id"]
    current = record["revisions"][-1]
    payload = current["payload"]
    pit_id = (payload.get("meta") or {}).get("pit_id") or record.get("pit_id")
    desired = _desired_folder(payload)

    for revision in record["revisions"]:
        row = conn.execute(
            "SELECT site_id,record_hash FROM site_revisions WHERE revision_id=?",
            (revision["revision_id"],),
        ).fetchone()
        if row and row != (site_id, revision["record_hash"]):
            return "conflict", f"Revision ID {revision['revision_id']} already identifies another record."
    for upload in record.get("attachment_uploads") or []:
        row = conn.execute("SELECT site_id FROM attachment_uploads WHERE queue_id=?",
                           (upload["queue_id"],)).fetchone()
        if row and row[0] != site_id:
            return "conflict", f"Photo queue ID {upload['queue_id']} belongs to another pit."

    duplicate = conn.execute(
        "SELECT site_id FROM sites WHERE owner=? AND pit_id=? AND site_id<>?",
        (owner, pit_id, site_id),
    ).fetchone()
    if duplicate:
        return "conflict", f"Pit ID {pit_id!r} is already used by site {duplicate[0]}."

    row = conn.execute(
        """SELECT owner,current_revision_id,export_folder,pending_export_folder
             FROM sites WHERE site_id=?""", (site_id,)
    ).fetchone()
    if not row:
        folder_owner = conn.execute(
            "SELECT site_id FROM sites WHERE export_folder=? OR pending_export_folder=?",
            (desired, desired),
        ).fetchone()
        if folder_owner or (export_dir / desired).exists():
            return "conflict", f"Destination folder {desired!r} is already occupied."
        return "new", "New pit."
    row_owner, destination_revision, export_folder, pending = row
    if row_owner != owner:
        return "conflict", "This site_id already belongs to another destination owner."

    has_upload_updates = False
    for upload in record.get("attachment_uploads") or []:
        destination_upload = conn.execute(
            """SELECT u.status,a.category,a.sha256,a.top_cm,a.bottom_cm
                 FROM attachment_uploads u
                 LEFT JOIN attachments a ON a.attachment_id=u.attachment_id
                WHERE u.queue_id=?""",
            (upload["queue_id"],),
        ).fetchone()
        if destination_upload is None:
            has_upload_updates = True
            continue
        destination_status = destination_upload[0]
        incoming_status = upload["status"]
        if destination_status == incoming_status:
            if incoming_status == "stored":
                destination_key = tuple(destination_upload[1:])
                incoming_key = tuple(upload.get("attachment_key") or [])
                if destination_key != incoming_key:
                    return "conflict", (
                        f"Photo queue ID {upload['queue_id']} identifies different stored content."
                    )
            continue
        if destination_status == "pending" and incoming_status in {"stored", "cancelled"}:
            has_upload_updates = True
            continue
        if destination_status in {"stored", "cancelled"} and incoming_status == "pending":
            # The central workflow has already reached a terminal state; a
            # stale field pending record must not reopen it.
            continue
        if destination_status == "stored" and incoming_status == "cancelled":
            # Do not delete an already stored central photograph because an
            # independently retained field queue was later cancelled.
            continue
        return "conflict", (
            f"Photo queue ID {upload['queue_id']} has incompatible field and central states."
        )

    capacity_error = _capacity_error(conn, record)
    if capacity_error:
        return "conflict", capacity_error

    incoming_ids = [r["revision_id"] for r in record["revisions"]]
    if pending:
        if pending == desired and destination_revision == current["revision_id"]:
            return "resume", "Resume an interrupted import publication."
        return "conflict", "Destination pit already needs unrelated archive recovery."
    if not export_folder or not (export_dir / export_folder).is_dir():
        return "conflict", "Destination pit folder is missing; recover it before importing."

    incoming_keys = {_attachment_key(a) for a in record.get("attachments") or []}
    destination_keys = _destination_attachment_keys(conn, site_id)
    has_new_attachments = bool(incoming_keys - destination_keys)
    if destination_revision == current["revision_id"]:
        return ("attachments", "Current revision already exists; import attachment or queue updates.") \
            if has_new_attachments or has_upload_updates else ("already", "Already imported.")
    if destination_revision in incoming_ids[:-1]:
        folder_owner = conn.execute(
            "SELECT site_id FROM sites WHERE site_id<>? AND (export_folder=? OR pending_export_folder=?)",
            (site_id, desired, desired),
        ).fetchone()
        if folder_owner or ((export_dir / desired).exists() and desired != export_folder):
            return "conflict", f"Fast-forward destination folder {desired!r} is occupied."
        return "fast_forward", "Incoming revision descends from the destination revision."
    return "conflict", "Pit changed independently on the field and central installations."


def inspect_transfer(bundle, *, destination_owner=None, db_path=DB_PATH,
                     export_dir=EXPORT_DIR, _verified=None):
    if destination_owner is not None:
        try:
            destination_owner = normalize_identity(destination_owner)
        except ValueError as exc:
            raise TransferError(
                "A valid trusted destination owner is required for classification."
            ) from exc
    verified = _verified or verify_transfer(bundle)
    report = {
        "format": FORMAT,
        "bundle_id": verified["manifest"]["bundle_id"],
        "source_installation_id": verified["manifest"]["source_installation_id"],
        "source_owner": verified["manifest"].get("source_owner"),
        "bundle_sha256": verified["bundle_sha256"],
        "destination_owner": destination_owner,
        "items": [],
    }
    if destination_owner is None:
        for site_id, record in verified["records"].items():
            report["items"].append({
                "site_id": site_id, "pit_id": record.get("pit_id"),
                "revision_id": record["current_revision_id"],
                "revision_number": record["revisions"][-1]["revision_number"],
                "attachments": len(record.get("attachments") or []),
                "result": "verified", "message": "Bundle entry is internally valid.",
            })
    else:
        root = Path(export_dir).resolve()
        db_file = Path(db_path).resolve()
        if not db_file.exists():
            # A dry-run against a fresh destination must remain genuinely
            # read-only. Classify against the filesystem without creating the
            # SQLite schema; the actual import initializes it later.
            for site_id, record in verified["records"].items():
                desired = _desired_folder(record["revisions"][-1]["payload"])
                if (root / desired).exists():
                    result, message = "conflict", f"Destination folder {desired!r} is already occupied."
                else:
                    result, message = "new", "New pit."
                report["items"].append({
                    "site_id": site_id, "pit_id": record.get("pit_id"),
                    "revision_id": record["current_revision_id"],
                    "revision_number": record["revisions"][-1]["revision_number"],
                    "attachments": len(record.get("attachments") or []),
                    "result": result, "message": message,
                })
        else:
            try:
                conn = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True)
                required = {r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}
                if not {"sites", "site_revisions", "attachments", "attachment_uploads"} <= required:
                    raise TransferError(
                        "Destination database is not initialized for Stage 14; start CryoPit once before inspection."
                    )
                for site_id, record in verified["records"].items():
                    result, message = _classify_record(conn, record, destination_owner, root)
                    report["items"].append({
                        "site_id": site_id, "pit_id": record.get("pit_id"),
                        "revision_id": record["current_revision_id"],
                        "revision_number": record["revisions"][-1]["revision_number"],
                        "attachments": len(record.get("attachments") or []),
                        "result": result, "message": message,
                    })
            finally:
                try:
                    conn.close()
                except UnboundLocalError:
                    pass
    report["summary"] = dict(Counter(item["result"] for item in report["items"]))
    return report


def _copy_file_durable(source: Path, target: Path):
    ensure_directory(target.parent)
    temp = target.with_name(f".{target.name}.transfer-{uuid.uuid4().hex}.tmp")
    try:
        shutil.copyfile(source, temp)
        fsync_file(temp)
        durable_replace(temp, target)
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


def _replace_generated(final: Path, stage: Path, name: str):
    source = stage / name
    if not source.is_dir():
        raise TransferError(f"Transfer staging is missing {name}/.")
    target = final / name
    backup = final / f".cryopit-transfer-prev-{name}"
    if backup.exists():
        durable_rmtree(backup)
    if target.exists():
        durable_rename(target, backup)
    try:
        durable_rename(source, target)
    except Exception:
        if backup.exists() and not target.exists():
            durable_rename(backup, target)
        raise
    if backup.exists():
        durable_rmtree(backup)


def _unique_attachment_filename(final: Path, item, reserved):
    filename = item["filename"]
    candidate = filename
    stem, suffix = Path(filename).stem, Path(filename).suffix
    for index in range(0, 10000):
        rel = target_relpath(item["category"], item.get("top_cm"),
                             item.get("bottom_cm"), candidate)
        if rel not in reserved and not (final / rel).exists():
            reserved.add(rel)
            return candidate, rel
        candidate = f"{stem}-import-{index + 1}{suffix}"
    raise TransferError(f"Could not reserve a filename for attachment {filename!r}.")


def _prepare_attachment_plan(conn, record, root: Path, old_folder, desired):
    existing = {}
    for row in conn.execute(
        """SELECT attachment_id,category,sha256,top_cm,bottom_cm,filename
             FROM attachments WHERE site_id=?""", (record["site_id"],)
    ):
        existing[(row[1], row[2], row[3], row[4])] = (row[0], row[5])
    reserved = {
        target_relpath(r[0], r[2], r[3], r[1])
        for r in conn.execute(
            "SELECT category,filename,top_cm,bottom_cm FROM attachments WHERE site_id=?",
            (record["site_id"],),
        )
    }
    candidate_roots = []
    for folder in (old_folder, desired):
        if folder and root / folder not in candidate_roots:
            candidate_roots.append(root / folder)
    final = root / desired
    plan = []
    for item in record.get("attachments") or []:
        key = _attachment_key(item)
        if key in existing:
            attachment_id, filename = existing[key]
            rel = target_relpath(item["category"], item.get("top_cm"),
                                 item.get("bottom_cm"), filename)
            matches = False
            for base in candidate_roots:
                path = base / rel
                if not path.exists():
                    continue
                if not path.is_file() or _sha256_file(path) != item["sha256"]:
                    raise TransferError(
                        f"Destination attachment path contains different bytes: {rel}"
                    )
                matches = True
                break
            plan.append({"item": item, "existing_id": attachment_id,
                         "filename": filename, "relpath": rel,
                         "copy": not matches})
        else:
            filename, rel = _unique_attachment_filename(final, item, reserved)
            plan.append({"item": item, "existing_id": None,
                         "filename": filename, "relpath": rel, "copy": True})
    return plan


def _insert_revision_chain(conn, record, bundle_id):
    for revision in record["revisions"]:
        existing = conn.execute(
            "SELECT site_id,record_hash FROM site_revisions WHERE revision_id=?",
            (revision["revision_id"],),
        ).fetchone()
        if existing:
            if existing != (record["site_id"], revision["record_hash"]):
                raise TransferError(f"Revision collision: {revision['revision_id']}")
            continue
        conn.execute(
            """INSERT INTO site_revisions
               (revision_id,site_id,parent_revision_id,revision_number,
                payload_version,record_hash,raw_json,source_installation_id,
                source_owner,created_at,imported_at,import_bundle_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,datetime('now'),?)""",
            (revision["revision_id"], record["site_id"],
             revision.get("parent_revision_id"), revision["revision_number"],
             revision["payload_version"], revision["record_hash"],
             canonical_json(revision["payload"]),
             revision["source_installation_id"], revision["source_owner"],
             revision.get("created_at"), bundle_id),
        )


def _upsert_import_db(conn, record, owner, desired, bundle_id, attachment_plan):
    site_id = record["site_id"]
    payload = record["revisions"][-1]["payload"]
    raw_json = canonical_json(payload)
    m = payload.get("meta") or {}
    camp_name = (m.get("campaign") or "").strip() or "campaign"
    conn.execute("INSERT OR IGNORE INTO campaigns(name) VALUES(?)", (camp_name,))
    campaign_id = conn.execute("SELECT campaign_id FROM campaigns WHERE name=?",
                               (camp_name,)).fetchone()[0]
    values = _site_values(payload, owner, raw_json, campaign_id)
    row = conn.execute("SELECT export_folder FROM sites WHERE site_id=?", (site_id,)).fetchone()
    if row:
        assignments = ", ".join(f'"{c}"=?' for c in values)
        conn.execute(
            f"UPDATE sites SET {assignments}, current_revision_id=?, "
            "pending_export_folder=?, updated_at=datetime('now') WHERE site_id=?",
            [*values.values(), record["current_revision_id"], desired, site_id],
        )
        for table in ("site_observers", "site_instruments", "layers",
                      "ssa_calibration", "swe_samples"):
            conn.execute(f"DELETE FROM {table} WHERE site_id=?", (site_id,))
    else:
        cols = ["site_id", *values.keys(), "current_revision_id",
                "export_folder", "pending_export_folder"]
        conn.execute(
            f"INSERT INTO sites ({','.join(chr(34)+c+chr(34) for c in cols)}) "
            f"VALUES ({','.join('?' for _ in cols)})",
            [site_id, *values.values(), record["current_revision_id"], None, desired],
        )
    _insert_revision_chain(conn, record, bundle_id)
    conn.execute("UPDATE sites SET current_revision_id=? WHERE site_id=?",
                 (record["current_revision_id"], site_id))
    _write_children(conn, payload, site_id)

    key_to_id = {}
    for entry in attachment_plan:
        item = entry["item"]
        key = _attachment_key(item)
        if entry["existing_id"] is not None:
            key_to_id[key] = entry["existing_id"]
            continue
        cur = conn.execute(
            """INSERT INTO attachments
               (site_id,category,filename,sha256,top_cm,bottom_cm,uploaded_at,
                storage_status,storage_error,pending_delete,trash_relpath)
               VALUES (?,?,?,?,?,?,COALESCE(?,datetime('now')),'stored',NULL,0,NULL)""",
            (site_id, item["category"], entry["filename"], item["sha256"],
             item.get("top_cm"), item.get("bottom_cm"), item.get("uploaded_at")),
        )
        key_to_id[key] = cur.lastrowid
        entry["attachment_id"] = cur.lastrowid

    for upload in record.get("attachment_uploads") or []:
        attachment_id = None
        if upload.get("attachment_key"):
            attachment_id = key_to_id[tuple(upload["attachment_key"])]
        existing = conn.execute(
            "SELECT site_id,status,attachment_id FROM attachment_uploads WHERE queue_id=?",
            (upload["queue_id"],),
        ).fetchone()
        if existing:
            if existing[0] != site_id:
                raise TransferError(f"Photo queue collision: {upload['queue_id']}")
            if existing[1] == "stored":
                continue
            if upload["status"] == "stored":
                if existing[1] == "cancelled":
                    raise TransferError(
                        f"Cancelled central photo queue cannot be completed automatically: {upload['queue_id']}"
                    )
                conn.execute(
                    """UPDATE attachment_uploads SET status='stored',attachment_id=?,
                       last_error=NULL,updated_at=datetime('now') WHERE queue_id=?""",
                    (attachment_id, upload["queue_id"]),
                )
            elif upload["status"] == "cancelled" and existing[1] == "pending":
                conn.execute(
                    """UPDATE attachment_uploads SET status='cancelled',attachment_id=NULL,
                       last_error=?,updated_at=datetime('now') WHERE queue_id=?""",
                    (upload.get("last_error"), upload["queue_id"]),
                )
            continue
        conn.execute(
            """INSERT INTO attachment_uploads
               (queue_id,site_id,category,original_filename,mime_type,size_bytes,
                client_sha256,top_cm,bottom_cm,status,attachment_id,last_error,
                created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,COALESCE(?,datetime('now')),
                       COALESCE(?,datetime('now')))""",
            (upload["queue_id"], site_id, upload["category"],
             upload["original_filename"], upload.get("mime_type"),
             upload.get("size_bytes"), upload.get("client_sha256"),
             upload.get("top_cm"), upload.get("bottom_cm"), upload["status"],
             attachment_id, upload.get("last_error"), upload.get("created_at"),
             upload.get("updated_at")),
        )


def _publish_import(record, root: Path, stage: Path, old_folder, desired,
                    attachment_plan, action):
    final = root / desired
    if action == "new" or (old_folder is None and action == "resume"):
        if final.exists():
            marker = _read_marker(final)
            if not marker or marker.get("site_id") != record["site_id"]:
                raise TransferError(f"Destination folder {desired!r} belongs to another record.")
            # Publication already completed before SQLite finalization.
            if stage.exists():
                durable_rmtree(stage)
            return final
        ensure_directory(final.parent)
        sync_tree(stage)
        durable_rename(stage, final)
        return final

    old = root / old_folder
    if old_folder != desired:
        if old.exists() and not final.exists():
            durable_rename(old, final)
        elif not old.exists() and final.exists():
            marker = _read_marker(final)
            if marker and marker.get("site_id") != record["site_id"]:
                raise TransferError(f"Destination folder {desired!r} belongs to another record.")
        elif old.exists() and final.exists():
            raise TransferError(f"Both old and desired folders exist: {old_folder}, {desired}")
        else:
            raise TransferError(f"Neither old nor desired destination folder exists.")
    elif not final.is_dir():
        raise TransferError(f"Destination pit folder is missing: {final}")

    if action in {"fast_forward", "resume"}:
        _replace_generated(final, stage, "csv")
        _replace_generated(final, stage, "figures")
        marker_source = stage / ".cryopit-archive.json"
        if marker_source.is_file():
            _copy_file_durable(marker_source, final / ".cryopit-archive.json")
    for entry in attachment_plan:
        if not entry["copy"]:
            continue
        source = stage / entry["item"]["relpath"]
        target = final / entry["relpath"]
        if not source.is_file() or _sha256_file(source) != entry["item"]["sha256"]:
            raise TransferError(f"Staged attachment is missing or damaged: {entry['item']['relpath']}")
        _copy_file_durable(source, target)
    if stage.exists():
        durable_rmtree(stage)
    return final



@contextlib.contextmanager
def _maintenance_mode(root: Path, bundle_id: str, *, operation="transfer-import"):
    """Reject new HTTP writes and make /readyz fail during a transfer."""
    marker = root / ".cryopit-maintenance"
    try:
        fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise TransferError(
            f"CryoPit is already in maintenance mode: {marker}"
        ) from exc
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({
                "operation": operation, "bundle_id": bundle_id,
                "pid": os.getpid(),
                "started_at": datetime.now(timezone.utc).isoformat(),
            }, fh, sort_keys=True)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        yield
    finally:
        durable_unlink(marker, missing_ok=True)


def _write_json_durable(path: Path, data):
    ensure_directory(path.parent)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temp.write_text(json.dumps(data, indent=2, sort_keys=True,
                                   ensure_ascii=False) + "\n", "utf-8")
        fsync_file(temp)
        durable_replace(temp, path)
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


def _quarantine_item(root: Path, verified, extracted: Path, item):
    """Persist a small review record, never a second copy of the large photos."""
    bundle_id = verified["manifest"]["bundle_id"]
    site_id = item["site_id"]
    qdir = root / ".transfer-conflicts" / bundle_id / site_id
    ensure_directory(qdir)
    record_source = extracted / f"pits/{site_id}/record.json"
    if record_source.is_file():
        _copy_file_durable(record_source, qdir / "record.json")
    report = {
        "format": "cryopit-transfer-conflict-v1",
        "bundle_id": bundle_id,
        "bundle_sha256": verified["bundle_sha256"],
        "source_installation_id": verified["manifest"]["source_installation_id"],
        "source_owner": verified["manifest"].get("source_owner"),
        "site_id": site_id,
        "pit_id": item.get("pit_id"),
        "incoming_revision_id": item.get("revision_id"),
        "result": item.get("result"),
        "message": item.get("message"),
        "record_path": "record.json",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "note": "The original verified transfer ZIP remains the authoritative source of attachment bytes.",
    }
    _write_json_durable(qdir / "conflict.json", report)
    return (qdir / "conflict.json").relative_to(root).as_posix()


def _clear_quarantine(root: Path, bundle_id: str, site_id: str):
    qdir = root / ".transfer-conflicts" / bundle_id / site_id
    if qdir.exists():
        durable_rmtree(qdir)


def _apply_verified_import(bundle, verified, plan, destination_owner,
                           db_path, root: Path, report_path=None):
    bundle_id = verified["manifest"]["bundle_id"]
    bundle_sha = verified["bundle_sha256"]
    import_id = str(uuid.uuid4())

    with tempfile.TemporaryDirectory(prefix="cryopit-import-") as tmp:
        extracted = Path(tmp)
        with zipfile.ZipFile(bundle) as zf:
            for info in _zip_members(zf):
                if info.is_dir() or info.filename == MANIFEST:
                    continue
                target = extracted / info.filename
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, target.open("wb") as dst:
                    shutil.copyfileobj(src, dst, length=1024 * 1024)
        if _sha256_file(Path(bundle)) != bundle_sha:
            raise TransferError(
                "Transfer bundle changed while it was being imported; no data was applied."
            )

        conn = get_conn(db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            existing_import = conn.execute(
                "SELECT import_id FROM transfer_imports WHERE bundle_id=? AND destination_owner=?",
                (bundle_id, destination_owner),
            ).fetchone()
            if existing_import:
                import_id = existing_import[0]
                conn.execute("DELETE FROM transfer_import_items WHERE import_id=?", (import_id,))
                conn.execute(
                    """UPDATE transfer_imports SET status='running',summary_json=NULL,
                       started_at=datetime('now'),completed_at=NULL,bundle_sha256=?
                       WHERE import_id=?""", (bundle_sha, import_id)
                )
            else:
                conn.execute(
                    """INSERT INTO transfer_imports
                       (import_id,bundle_id,source_installation_id,destination_owner,
                        bundle_sha256,status)
                       VALUES (?,?,?,?,?,'running')""",
                    (import_id, bundle_id,
                     verified["manifest"]["source_installation_id"],
                     destination_owner, bundle_sha),
                )
            conn.execute("COMMIT")
        finally:
            conn.close()

        results = []
        with storage_lock(root):
            for item in plan["items"]:
                site_id = item["site_id"]
                record = verified["records"][site_id]
                action = item["result"]
                message = item["message"]
                if action == "conflict":
                    result_item = {**item}
                    result_item["quarantine"] = _quarantine_item(
                        root, verified, extracted, result_item)
                    results.append(result_item)
                    continue
                if action == "already":
                    _clear_quarantine(root, bundle_id, site_id)
                    results.append({**item})
                    continue

                stage = root / ".transfer-staging" / bundle_id / site_id
                if stage.exists():
                    durable_rmtree(stage)
                stage.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(extracted / record["folder_prefix"], stage)
                sync_tree(stage)

                conn = get_conn(db_path)
                try:
                    row = conn.execute(
                        "SELECT export_folder,pending_export_folder FROM sites WHERE site_id=?",
                        (site_id,),
                    ).fetchone()
                    old_folder = row[0] if row else None
                    desired = _desired_folder(record["revisions"][-1]["payload"])
                    attachment_plan = _prepare_attachment_plan(
                        conn, record, root, old_folder, desired)
                    conn.execute("BEGIN IMMEDIATE")
                    _upsert_import_db(conn, record, destination_owner, desired,
                                      bundle_id, attachment_plan)
                    conn.execute("COMMIT")
                    _publish_import(record, root, stage, old_folder, desired,
                                    attachment_plan, action)
                    conn.execute("BEGIN IMMEDIATE")
                    conn.execute(
                        """UPDATE sites SET export_folder=?,pending_export_folder=NULL,
                           updated_at=datetime('now') WHERE site_id=?""",
                        (desired, site_id),
                    )
                    conn.execute("COMMIT")
                    _clear_quarantine(root, bundle_id, site_id)
                    results.append({**item, "result": "imported", "message": message})
                except Exception as exc:
                    try:
                        conn.execute("ROLLBACK")
                    except Exception:
                        pass
                    result_item = {**item, "result": "error", "message": str(exc)}
                    result_item["quarantine"] = _quarantine_item(
                        root, verified, extracted, result_item)
                    results.append(result_item)
                finally:
                    conn.close()

        final_report = {**plan, "items": results}
        final_report["summary"] = dict(Counter(i["result"] for i in results))
        status = ("complete" if not any(
            i["result"] in {"conflict", "error"} for i in results
        ) else "partial")
        conn = get_conn(db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            for item in results:
                conn.execute(
                    """INSERT OR REPLACE INTO transfer_import_items
                       (import_id,site_id,incoming_revision_id,result,message)
                       VALUES (?,?,?,?,?)""",
                    (import_id, item["site_id"], item.get("revision_id"),
                     item["result"], item.get("message")),
                )
            conn.execute(
                """UPDATE transfer_imports SET status=?,summary_json=?,
                   completed_at=datetime('now') WHERE import_id=?""",
                (status, json.dumps(final_report["summary"], sort_keys=True),
                 import_id),
            )
            conn.execute("COMMIT")
        finally:
            conn.close()
        if report_path:
            _write_json_durable(Path(report_path), final_report)
        return final_report


def import_transfer(bundle, *, destination_owner, db_path=DB_PATH,
                    export_dir=EXPORT_DIR, dry_run=False, report_path=None):
    try:
        destination_owner = normalize_identity(destination_owner)
    except ValueError as exc:
        raise TransferError(
            "A valid trusted destination owner is required for import."
        ) from exc

    # Verify the untrusted ZIP before touching the destination database or
    # export tree. This keeps even a failed real import side-effect free.
    verified = verify_transfer(bundle)
    if dry_run:
        plan = inspect_transfer(
            bundle, destination_owner=destination_owner,
            db_path=db_path, export_dir=export_dir, _verified=verified)
        if report_path:
            Path(report_path).write_text(
                json.dumps(plan, indent=2, sort_keys=True) + "\n", "utf-8"
            )
        return plan

    root = Path(export_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    bundle_id = verified["manifest"]["bundle_id"]
    with _maintenance_mode(root, bundle_id, operation="transfer-import"):
        # Migration and final classification happen after the maintenance
        # marker is visible, eliminating the plan/apply race with live edits.
        init_db(db_path)
        plan = inspect_transfer(
            bundle, destination_owner=destination_owner,
            db_path=db_path, export_dir=root, _verified=verified)
        return _apply_verified_import(
            bundle, verified, plan, destination_owner,
            db_path, root, report_path=report_path)


def _print_report(report):
    print(f"Bundle: {report['bundle_id']}")
    print(f"Source installation: {report['source_installation_id']}")
    if report.get("destination_owner"):
        print(f"Destination owner: {report['destination_owner']}")
    for item in report["items"]:
        print(f"{item['result']:>12}  {item['pit_id']}  {item['site_id']}  {item['message']}")
    print("Summary: " + ", ".join(f"{k}={v}" for k, v in sorted(report["summary"].items())))


def main(argv=None):
    parser = argparse.ArgumentParser(description="CryoPit field transfer bundles")
    parser.add_argument("--db", default=DB_PATH, help="CryoPit SQLite database")
    parser.add_argument("--exports", default=EXPORT_DIR, help="CryoPit export directory")
    sub = parser.add_subparsers(dest="command", required=True)

    exp = sub.add_parser("export", help="Create a one-way field transfer bundle")
    exp.add_argument("--output", required=True)
    exp.add_argument("--owner", default=DEV_USER)
    exp.add_argument("--site-id", action="append", default=[])

    inspect = sub.add_parser("inspect", help="Verify and optionally classify a transfer bundle")
    inspect.add_argument("bundle")
    inspect.add_argument("--owner", help="Destination institutional owner for classification")
    inspect.add_argument("--json", dest="json_path")

    imp = sub.add_parser("import", help="Import safe new or fast-forward revisions")
    imp.add_argument("bundle")
    imp.add_argument("--owner", required=True, help="Trusted destination institutional owner")
    imp.add_argument("--dry-run", action="store_true")
    imp.add_argument("--report")

    args = parser.parse_args(argv)
    try:
        if args.command == "export":
            manifest = create_transfer(args.output, db_path=args.db, export_dir=args.exports,
                                       owner=args.owner, site_ids=args.site_id)
            print(f"Created {args.output}: {len(manifest['pits'])} pit(s), bundle {manifest['bundle_id']}")
            return 0
        if args.command == "inspect":
            report = inspect_transfer(args.bundle, destination_owner=args.owner,
                                      db_path=args.db, export_dir=args.exports)
            if args.json_path:
                Path(args.json_path).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", "utf-8")
            _print_report(report)
            return 1 if report["summary"].get("conflict") else 0
        report = import_transfer(args.bundle, destination_owner=args.owner,
                                 db_path=args.db, export_dir=args.exports,
                                 dry_run=args.dry_run, report_path=args.report)
        _print_report(report)
        return 1 if any(k in report["summary"] for k in ("conflict", "error")) else 0
    except TransferError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
