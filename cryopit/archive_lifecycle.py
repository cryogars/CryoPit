"""Recoverable publication of pit folders.

SQLite and the filesystem cannot participate in one transaction.  CryoPit
therefore records intent in ``sites.pending_export_folder``, builds generated
output privately, publishes with same-filesystem renames, and only then clears
the pending value.  Every interruption is detectable and retryable.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import shutil
from pathlib import Path

from .config import CAMPAIGN, EXPORT_DIR
from .export import _fname, _safe_name, export_from_payload, save_csvs_at_folder
from .repository import attachment_upload_summary, finalize_export, get_site_state, save_pit
from .storage_lifecycle import (durable_rename, durable_replace, durable_rmtree,
                                fsync_file, storage_lock, sync_tree)
_MARKER = ".cryopit-archive.json"


class ArchiveConflict(RuntimeError):
    pass


class ArchiveIntegrityError(RuntimeError):
    pass


def derive_export_folder(payload):
    m = payload.get("meta") or {}
    dstr = (m.get("date") or "").replace("-", "")
    return _safe_name(
        f"{m.get('campaign') or CAMPAIGN}_{m.get('pit_id') or 'pit'}_{dstr}", "pit")


def payload_digest(payload):
    clean = {k: v for k, v in payload.items() if k not in {"site_id", "overwrite", "attachment_manifest"}}
    blob = json.dumps(clean, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _root():
    root = Path(EXPORT_DIR).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _stage(site_id):
    return _root() / ".staging" / site_id


def _path(folder):
    # Folder values are database-recorded safe single components.
    safe = _safe_name(folder, "pit")
    if safe != folder:
        raise ArchiveIntegrityError(f"Unsafe recorded export folder: {folder!r}")
    return _root() / safe


def _marker_data(site_id, folder, payload):
    return {
        "site_id": site_id,
        "export_folder": folder,
        "payload_sha256": payload_digest(payload),
        "format": 1,
    }


def _write_marker(folder, data):
    tmp = folder / (_MARKER + ".tmp")
    tmp.write_text(json.dumps(data, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    fsync_file(tmp)
    durable_replace(tmp, folder / _MARKER)


def _read_marker(folder):
    try:
        return json.loads((folder / _MARKER).read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError):
        return None


def _marker_matches(folder, site_id, desired, payload):
    m = _read_marker(folder)
    return bool(m and m.get("site_id") == site_id and
                m.get("export_folder") == desired and
                m.get("payload_sha256") == payload_digest(payload))


@contextlib.contextmanager
def archive_lock():
    """Serialize pit-folder and attachment storage lifecycle operations."""
    with storage_lock(_root()):
        yield


def _build_stage(site_id, desired, payload, render_figures, first_archive):
    stage = _stage(site_id)
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    csvs = export_from_payload(payload)
    csv_count = save_csvs_at_folder(csvs, str(stage))
    png, pdf = render_figures(payload)
    if not png:
        raise RuntimeError("Required profile PNG could not be generated.")
    figdir = stage / "figures"
    figdir.mkdir()
    stem = _fname(payload.get("meta") or {}, "profile").replace(".csv", "")
    (figdir / f"{stem}.png").write_bytes(png)
    fig_count = 1
    if pdf:
        (figdir / f"{stem}.pdf").write_bytes(pdf)
        fig_count += 1
    if first_archive:
        (stage / "uploads").mkdir()
    _write_marker(stage, _marker_data(site_id, desired, payload))
    # Close-to-open visibility is not enough for a field laptop that can lose
    # power. Flush the complete staged tree before publishing its directory
    # entry. Unsupported directory fsync is logged as a degraded guarantee.
    sync_tree(stage)
    return stage, {"csv_count": csv_count, "figure_count": fig_count,
                   "has_png": True, "has_pdf": bool(pdf)}


def _repair_swap(final, name):
    target = final / name
    backup = final / f".cryopit-prev-{name}"
    if backup.exists() and target.exists():
        durable_rmtree(backup)
    elif backup.exists() and not target.exists():
        durable_rename(backup, target)


def _replace_generated(final, stage, name):
    _repair_swap(final, name)
    target = final / name
    source = stage / name
    backup = final / f".cryopit-prev-{name}"
    if not source.exists():
        raise ArchiveIntegrityError(f"Staging output is missing {name}/")
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


def _resolve_rearchive_folder(site_id, old_name, desired):
    old = _path(old_name)
    new = _path(desired)
    if old_name == desired:
        if not old.is_dir():
            raise ArchiveIntegrityError(f"Recorded pit folder is missing: {old}")
        return old
    old_exists, new_exists = old.is_dir(), new.is_dir()
    if old_exists and not new_exists:
        durable_rename(old, new)
        return new
    if not old_exists and new_exists:
        marker = _read_marker(new)
        # It is valid for the marker still to name the old folder here: the
        # directory rename may have completed before generated output/marker.
        if marker and marker.get("site_id") not in (None, site_id):
            raise ArchiveConflict(f"Destination folder belongs to another pit: {new}")
        return new
    if old_exists and new_exists:
        raise ArchiveConflict(
            f"Both recorded and desired pit folders exist: {old.name}, {new.name}")
    raise ArchiveIntegrityError(
        f"Neither recorded nor desired pit folder exists: {old.name}, {new.name}")


def publish_pending(site_id, payload, render_figures):
    """Publish one already-pending DB state. Safe to call repeatedly."""
    state = get_site_state(site_id, include_raw=False)
    if not state:
        raise ArchiveIntegrityError("Pending pit no longer exists.")
    desired = state["pending_export_folder"]
    if not desired:
        return {"folder": str(_path(state["export_folder"])), "recovered": False}
    old_name = state["export_folder"]
    first = not old_name
    final = _path(desired)

    # A completed final marker means the filesystem side already finished; only
    # SQLite finalization remains.
    if final.is_dir() and _marker_matches(final, site_id, desired, payload):
        if not finalize_export(site_id, desired):
            raise ArchiveIntegrityError("Could not finalize the recovered archive in SQLite.")
        return {"folder": str(final), "recovered": True}

    stage, counts = _build_stage(site_id, desired, payload, render_figures, first)
    if first:
        if final.exists():
            marker = _read_marker(final)
            if not marker or marker.get("site_id") != site_id:
                raise ArchiveConflict(f"Export folder already exists: {final.name}")
            raise ArchiveConflict(
                f"Incomplete first archive already occupies {final.name}; manual review required.")
        final.parent.mkdir(parents=True, exist_ok=True)
        durable_rename(stage, final)
    else:
        final = _resolve_rearchive_folder(site_id, old_name, desired)
        _replace_generated(final, stage, "csv")
        _replace_generated(final, stage, "figures")
        # uploads/ is deliberately untouched.
        _write_marker(final, _marker_data(site_id, desired, payload))
        if stage.exists():
            durable_rmtree(stage)

    if not finalize_export(site_id, desired):
        raise ArchiveIntegrityError("Filesystem published, but SQLite finalization failed.")
    counts.update({"folder": str(final), "recovered": False})
    return counts


def recover_pending(site_id, render_figures):
    """Finish an interrupted first archive or re-archive from stored raw_json."""
    with archive_lock():
        state = get_site_state(site_id, include_raw=True)
        if not state:
            raise ArchiveIntegrityError("Pit not found.")
        if not state["pending_export_folder"]:
            return {"ok": True, "site_id": site_id, "already_complete": True,
                    "folder": str(_path(state["export_folder"]))}
        try:
            payload = json.loads(state["raw_json"] or "{}")
        except (ValueError, json.JSONDecodeError) as exc:
            raise ArchiveIntegrityError(f"Pending pit has invalid raw JSON: {exc}") from exc
        result = publish_pending(site_id, payload, render_figures)
        return {"ok": True, "site_id": site_id, **result,
                "photo_uploads": attachment_upload_summary(site_id)}


def archive_payload(payload, site_id, render_figures):
    """Create/update the DB pending state, publish it, and return API data."""
    with archive_lock():
        if site_id:
            prior = get_site_state(site_id, include_raw=True)
            if not prior:
                return {"ok": False, "msg": "Loaded pit was not found."}
            if prior["pending_export_folder"]:
                # Complete the previous intent before accepting newer edits.
                # A still-unresolved conflict is a normal recoverable response,
                # not an uncaught 500 from the archive endpoint.
                try:
                    recover_payload = json.loads(prior["raw_json"] or "{}")
                    publish_pending(site_id, recover_payload, render_figures)
                except (ArchiveConflict, ArchiveIntegrityError) as exc:
                    return {"ok": False, "pending": True, "site_id": site_id,
                            "pit_id": prior["pit_id"], "msg": str(exc)}
                except Exception:
                    logging.getLogger(__name__).exception("pending archive recovery failed")
                    return {"ok": False, "pending": True, "site_id": site_id,
                            "pit_id": prior["pit_id"],
                            "msg": "Archive recovery failed. Retry or review the server log."}

        desired = derive_export_folder(payload)
        status, info = save_pit(payload, site_id=site_id,
                                pending_export_folder=desired)
        if status == "exists":
            return {"ok": False, "exists": True, **info,
                    "msg": "Load the existing pit before editing it."}
        if status == "error":
            return {"ok": False, "msg": info}
        site_id = info["site_id"]
        try:
            result = publish_pending(site_id, payload, render_figures)
        except (ArchiveConflict, ArchiveIntegrityError) as exc:
            return {"ok": False, "pending": True, "site_id": site_id,
                    "pit_id": info["pit_id"], "msg": str(exc)}
        except Exception:
            logging.getLogger(__name__).exception("archive publication failed")
            return {"ok": False, "pending": True, "site_id": site_id,
                    "pit_id": info["pit_id"],
                    "msg": "Archive publication failed. Retry or review the server log."}
        return {"ok": True, **info, **result,
                "photo_uploads": attachment_upload_summary(site_id)}
