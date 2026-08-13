"""Persistence for immutable pit identities.

``site_id`` is the internal, immutable identity. ``pit_id`` remains the
human-facing identifier and may be corrected while editing a loaded record.
Re-archives update the site row in place and rebuild only form-derived child
rows; attachment rows are never deleted or reconstructed.
"""
from __future__ import annotations

import json
import logging
import re
import uuid

from .auth import current_user
from .config import CAMPAIGN
from .db import get_conn
from .revisions import record_revision

GROUND_PROBE_MIN_CM = -10.0
_ATTACH_CATEGORIES = {"sheet", "pitwall", "stratigraphy"}
_ATTACH_LIMITS = {"sheet": 3, "pitwall": 6}
_ATTACH_TOTAL = 150
_STRAT_PER_LAYER = 20
_ATTACH_MAX_BYTES = 10 * 1024 * 1024
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

_TASK_CHECKLIST_NAMES = {
    "HS Transects", "Snow Scope Transects",
    "Stratigraphy pictures", "Pit pictures",
}

_GROUND_CONDITION_CHOICES = {"Frozen", "Moist", "Saturated"}

_WEATHER_CHOICES = {
    "precip_rate": {
        "None", "Very light (0.5 cm/hr)", "Light (1 cm/hr)",
        "Moderate (5 cm/hr)", "Heavy (10 cm/hr)",
    },
    "precip_type": {"None", "Rain", "Snow", "Graupel", "Hail", "Rain/Snow mix"},
    "sky": {"Clear", "Few (<1/4)", "Scattered (1/4-1/2)", "Broken (>1/2)", "Overcast"},
    "wind": {"Calm (0 mph)", "Light (1-16 mph)", "Moderate (17-25 mph)",
             "Strong (26-38 mph)", "Extreme (>38 mph)"},
}


def _weather_values(value):
    """Return canonical weather selections while accepting pre-multiselect pits."""
    if value is None or value == "":
        return []
    if isinstance(value, str):
        values = [part.strip() for part in value.split(";") if part.strip()]
    elif isinstance(value, list):
        values = value
    else:
        raise ValueError("weather selections must be a list")
    out = []
    for item in values:
        if not isinstance(item, str) or not item.strip():
            raise ValueError("weather selections must contain non-empty text values")
        item = item.strip()
        if item not in out:
            out.append(item)
    return out


def _weather_text(value):
    return "; ".join(_weather_values(value)) or None


def _normalize_weather(payload):
    raw = payload.get("weather")
    if raw is None:
        raw = {}
        payload["weather"] = raw
    if not isinstance(raw, dict):
        raise ValueError("weather must be an object")
    for key, choices in _WEATHER_CHOICES.items():
        values = _weather_values(raw.get(key))
        invalid = [value for value in values if value not in choices]
        if invalid:
            raise ValueError(f"Weather {key.replace('_', ' ')} has an invalid value.")
        if "None" in values and len(values) > 1:
            raise ValueError(f"Weather {key.replace('_', ' ')} cannot combine None with another value.")
        raw[key] = values



def _normalize_ground(payload):
    raw = payload.get("ground")
    if raw is None:
        raw = {}
        payload["ground"] = raw
    if not isinstance(raw, dict):
        raise ValueError("ground must be an object")
    values = _weather_values(raw.get("condition"))
    invalid = [value for value in values if value not in _GROUND_CONDITION_CHOICES]
    if invalid:
        raise ValueError("Ground condition has an invalid value.")
    raw["condition"] = values


def _instrument_used(value):
    if value in ("Y", "N"):
        return value
    if value is None or value == "":
        return None
    raise ValueError(f"invalid instrument used state: {value!r}")


def normalize_queue_id(value):
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError(f"invalid photo queue_id: {value!r}") from exc


def _attachment_manifest(payload):
    """Validate and normalize the browser's durable photo-outbox manifest.

    The manifest contains metadata only; image bytes are uploaded separately.
    It is deliberately excluded from raw_json because it is workflow state, not
    a scientific form field.  Missing manifests remain valid for older clients.
    """
    raw = payload.get("attachment_manifest")
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("attachment_manifest must be a list")
    if len(raw) > 150:
        raise ValueError("attachment_manifest exceeds the 150-file pit limit")
    out, seen = [], set()
    for i, item in enumerate(raw, 1):
        if not isinstance(item, dict):
            raise ValueError(f"Photo manifest row {i} must be an object.")
        qid = normalize_queue_id(item.get("queue_id"))
        if qid in seen:
            raise ValueError(f"Photo manifest repeats queue_id {qid}.")
        seen.add(qid)
        category = (item.get("category") or "").strip()
        if category not in _ATTACH_CATEGORIES:
            raise ValueError(f"Photo manifest row {i} has an invalid category.")
        filename = (item.get("filename") or "").strip()
        if not filename or len(filename) > 255:
            raise ValueError(f"Photo manifest row {i} needs a valid filename.")
        size = item.get("size_bytes")
        if size is not None:
            if isinstance(size, bool) or not isinstance(size, int) or size < 0:
                raise ValueError(f"Photo manifest row {i} has an invalid size.")
            if size > _ATTACH_MAX_BYTES:
                raise ValueError(f"Photo manifest row {i} exceeds the 10 MB limit.")
        digest = (item.get("sha256") or "").lower() or None
        if digest and not _SHA256_RE.fullmatch(digest):
            raise ValueError(f"Photo manifest row {i} has an invalid SHA-256.")
        top, bottom = item.get("top_cm"), item.get("bottom_cm")
        for value, label in ((top, "top_cm"), (bottom, "bottom_cm")):
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))):
                raise ValueError(f"Photo manifest row {i} has an invalid {label}.")
        if category == "stratigraphy":
            if top is None or bottom is None or top <= bottom:
                raise ValueError(f"Photo manifest row {i} needs a valid layer interval.")
        else:
            top = bottom = None
        out.append({
            "queue_id": qid, "category": category, "filename": filename,
            "mime_type": (item.get("mime_type") or "")[:127] or None,
            "size_bytes": size, "client_sha256": digest,
            "top_cm": top, "bottom_cm": bottom,
        })
    return out


def _is_manifest_pdf(item):
    return item.get("mime_type") == "application/pdf" or item["filename"].lower().endswith(".pdf")


def _assert_manifest_capacity(conn, site_id, item):
    stored_total = conn.execute(
        "SELECT COUNT(*) FROM attachments WHERE site_id=?", (site_id,)).fetchone()[0]
    pending_total = conn.execute(
        "SELECT COUNT(*) FROM attachment_uploads WHERE site_id=? AND status='pending'",
        (site_id,)).fetchone()[0]
    if stored_total + pending_total >= _ATTACH_TOTAL:
        raise ValueError("Expected photographs exceed the 150-file pit limit.")

    category = item["category"]
    if category == "stratigraphy":
        stored = conn.execute(
            """SELECT COUNT(*) FROM attachments WHERE site_id=? AND category='stratigraphy'
                 AND top_cm IS ? AND bottom_cm IS ?""",
            (site_id, item["top_cm"], item["bottom_cm"])).fetchone()[0]
        pending = conn.execute(
            """SELECT COUNT(*) FROM attachment_uploads
               WHERE site_id=? AND status='pending' AND category='stratigraphy'
                 AND top_cm IS ? AND bottom_cm IS ?""",
            (site_id, item["top_cm"], item["bottom_cm"])).fetchone()[0]
        if stored + pending >= _STRAT_PER_LAYER:
            raise ValueError(f"Expected photographs exceed the {_STRAT_PER_LAYER}-photo limit for this layer.")
        return

    stored = conn.execute(
        "SELECT COUNT(*) FROM attachments WHERE site_id=? AND category=?",
        (site_id, category)).fetchone()[0]
    pending = conn.execute(
        "SELECT COUNT(*) FROM attachment_uploads WHERE site_id=? AND category=? AND status='pending'",
        (site_id, category)).fetchone()[0]
    if stored + pending >= _ATTACH_LIMITS[category]:
        raise ValueError(f"Expected photographs exceed the {_ATTACH_LIMITS[category]}-file {category} limit.")

    if category == "sheet":
        stored_pdf = conn.execute(
            "SELECT 1 FROM attachments WHERE site_id=? AND category='sheet' AND filename LIKE '%.pdf' LIMIT 1",
            (site_id,)).fetchone()
        pending_rows = conn.execute(
            """SELECT original_filename,mime_type FROM attachment_uploads
               WHERE site_id=? AND category='sheet' AND status='pending'""",
            (site_id,)).fetchall()
        pending_pdf = any((mime == "application/pdf" or name.lower().endswith(".pdf"))
                          for name, mime in pending_rows)
        if (_is_manifest_pdf(item) and (stored + pending > 0)) or \
                (not _is_manifest_pdf(item) and (stored_pdf or pending_pdf)):
            raise ValueError("The pit sheet is one PDF or up to three images, never a mix.")


def _sync_attachment_manifest(conn, site_id, manifest):
    """Upsert expected uploads without deleting expectations from elsewhere.

    Absence from one browser's manifest is not cancellation: another device may
    hold that queued photo.  Cancellation is an explicit API operation.
    """
    for item in manifest:
        existing = conn.execute(
            """SELECT site_id,status,category,top_cm,bottom_cm,original_filename,
                      size_bytes,client_sha256
               FROM attachment_uploads WHERE queue_id=?""",
            (item["queue_id"],)).fetchone()
        if existing and existing[0] != site_id:
            raise ValueError(f"Photo queue_id {item['queue_id']} already belongs to another pit.")
        if existing and existing[1] == "cancelled":
            raise ValueError(f"Photo queue_id {item['queue_id']} was cancelled and cannot be reused.")
        if existing and (existing[2], existing[3], existing[4]) != (
                item["category"], item["top_cm"], item["bottom_cm"]):
            raise ValueError(f"Photo queue_id {item['queue_id']} changed category or layer interval.")
        if existing and existing[5] != item["filename"]:
            raise ValueError(f"Photo queue_id {item['queue_id']} changed filename.")
        if existing and existing[6] is not None and item["size_bytes"] is not None \
                and existing[6] != item["size_bytes"]:
            raise ValueError(f"Photo queue_id {item['queue_id']} changed file size.")
        if existing and existing[7] and item["client_sha256"] \
                and existing[7] != item["client_sha256"]:
            raise ValueError(f"Photo queue_id {item['queue_id']} changed checksum.")
        if existing:
            # A retry may fill metadata that was unavailable to an older browser,
            # but never changes the identity or reverts stored to pending.
            conn.execute(
                """UPDATE attachment_uploads SET original_filename=?, mime_type=?,
                   size_bytes=COALESCE(?,size_bytes),
                   client_sha256=COALESCE(?,client_sha256), updated_at=datetime('now')
                   WHERE queue_id=?""",
                (item["filename"], item["mime_type"], item["size_bytes"],
                 item["client_sha256"], item["queue_id"]))
        else:
            _assert_manifest_capacity(conn, site_id, item)
            conn.execute(
                """INSERT INTO attachment_uploads
                   (queue_id,site_id,category,original_filename,mime_type,size_bytes,
                    client_sha256,top_cm,bottom_cm,status)
                   VALUES (?,?,?,?,?,?,?,?,?,'pending')""",
                (item["queue_id"], site_id, item["category"], item["filename"],
                 item["mime_type"], item["size_bytes"], item["client_sha256"],
                 item["top_cm"], item["bottom_cm"]))


def _validate_payload(payload):
    try:
        _attachment_manifest(payload)
        _normalize_weather(payload)
        _normalize_ground(payload)
    except ValueError as exc:
        return str(exc)
    m = payload.get("meta") or {}
    pid = (m.get("pit_id") or "").strip()
    if not pid or pid == "—":
        return "Missing pit_id"

    for section, label in (("density", "Density"), ("lwc", "LWC"),
                           ("stratigraphy", "Stratigraphy")):
        for i, r in enumerate(payload.get(section) or []):
            t, b = r.get("top"), r.get("bottom")
            if t is not None and b is not None and t <= b:
                return (f"{label} interval {i + 1}: top ({t}) must be greater "
                        f"than bottom ({b}).")

    hs = m.get("total_depth")
    hs = hs if isinstance(hs, (int, float)) and hs > 0 else None
    for i, r in enumerate(payload.get("density") or []):
        for key, lbl in (("a", "A"), ("b", "B"), ("c", "Extra")):
            v = r.get(key)
            if v is not None and isinstance(v, (int, float)) and not (0 < v <= 917):
                return (f"Density interval {i + 1} profile {lbl}: {v} kg/m3 "
                        "is outside 1-917 (ice).")
    for i, r in enumerate(payload.get("stratigraphy") or []):
        v = r.get("layer_density")
        if v is not None and isinstance(v, (int, float)) and not (0 < v <= 917):
            return f"Layer {i + 1} density: {v} kg/m3 is outside 1-917 (ice)."
    for section, lbl in (("density", "Density"), ("lwc", "LWC"),
                         ("stratigraphy", "Stratigraphy")):
        for i, r in enumerate(payload.get(section) or []):
            for bound in ("top", "bottom"):
                v = r.get(bound)
                if isinstance(v, (int, float)) and v < 0:
                    return f"{lbl} interval {i + 1}: {bound} cannot be negative."
            top = r.get("top")
            if hs and isinstance(top, (int, float)) and top > hs + 0.51:
                return (f"{lbl} interval {i + 1}: top ({top}) exceeds total "
                        f"depth ({hs}).")
    for i, r in enumerate(payload.get("temperature") or []):
        h = r.get("height")
        if isinstance(h, (int, float)) and (h < GROUND_PROBE_MIN_CM or
                                            (hs and h > hs + 0.51)):
            # The parenthetical is the half of this message that explains WHY a
            # negative height is allowed at all. export.py calls these readings
            # "Soil temperature" in the CSV header; this said only "outside
            # -10.0-100 cm", so the rule and its explanation had drifted apart.
            return (f"Temperature row {i + 1}: height ({h}) outside "
                    f"{GROUND_PROBE_MIN_CM}-{hs or '?'} cm "
                    f"(negative heights are soil readings, "
                    f"below the snow-ground interface).")
    for i, r in enumerate(payload.get("lwc") or []):
        for key, lbl in (("a", "A"), ("b", "B")):
            v = r.get(key)
            if v is not None and isinstance(v, (int, float)) and not (1 < v <= 12):
                return (f"LWC interval {i + 1} profile {lbl}: permittivity {v} "
                        "is outside (1, 12].")

    for i, inst in enumerate(payload.get("instruments") or []):
        try:
            used = _instrument_used(inst.get("used"))
        except ValueError:
            return f"Instrument row {i + 1}: used must be Y, N, or unanswered."
        serial = (inst.get("sn") or "").strip()
        if used != "Y" and serial:
            return f"Instrument row {i + 1}: a serial number requires Used=Y."
        name = (inst.get("name") or "").strip()
        is_task = name in _TASK_CHECKLIST_NAMES
        if m.get("no_instruments") and not is_task and used != "N":
            return f"Instrument row {i + 1} conflicts with 'No instruments used'."
        if m.get("no_tasks") and is_task and used != "N":
            return f"Survey/documentation row {i + 1} conflicts with 'No tasks done'."

    sc = payload.get("ssa_calibration") or {}
    ssa_name = (sc.get("instrument") or "").strip()
    if (sc.get("spectralon") or sc.get("calib_values")) and not ssa_name:
        return "Select the SSA instrument (§8) before archiving calibration data."
    return None


def _site_values(payload, owner, raw_json, campaign_id):
    m = payload.get("meta") or {}
    g = payload.get("ground") or {}
    wx = payload.get("weather") or {}
    unit = (m.get("gps_uncertainty_unit") or "m").lower()
    unc = m.get("gps_uncertainty")
    if unc is not None:
        if unit == "cm":
            unc /= 100.0
        elif unit == "ft":
            unc *= 0.3048
    return {
        "pit_id": (m.get("pit_id") or "").strip(), "owner": owner,
        "raw_json": raw_json, "campaign_id": campaign_id,
        "location": m.get("location"), "site": m.get("site"),
        "date": m.get("date"), "pit_open_time": m.get("pit_open_time"),
        "temp_time_start": m.get("temp_time_start"),
        "temp_time_end": m.get("temp_time_end"),
        "total_depth_cm": m.get("total_depth"),
        "utm_easting": m.get("utm_easting"), "utm_northing": m.get("utm_northing"),
        "utm_zone_number": m.get("utm_zone_number"),
        "utm_zone_letter": m.get("utm_zone_letter"),
        "latitude": m.get("latitude"), "longitude": m.get("longitude"),
        "coord_source": m.get("coord_source"), "elevation_m": m.get("elevation"),
        "slope_angle_deg": m.get("slope_angle"), "recorded_by": m.get("recorded_by"),
        "surveyors": m.get("surveyors"), "gps_device": m.get("gps_device"),
        "gps_uncertainty_m": unc, "precip_rate": _weather_text(wx.get("precip_rate")),
        "precip_type": _weather_text(wx.get("precip_type")),
        "sky_condition": _weather_text(wx.get("sky")),
        "wind": _weather_text(wx.get("wind")), "ground_condition": _weather_text(g.get("condition")),
        "ground_roughness": g.get("roughness"), "tree_canopy": g.get("canopy"),
        "snow_cover_condition": g.get("snow_cover"),
        "standing_water": g.get("standing_water"),
        "vegetation": ",".join(g.get("vegetation") or []),
        "veg_height_cm": g.get("veg_height"),
        "swe_melt_evidence": g.get("melt_evidence"),
        "density_cutter": m.get("density_cutter"), "flags": m.get("flags"),
        "comments": m.get("comments"), "comment_weather": m.get("comment_weather"),
        "comment_pit": m.get("comment_pit"),
        "comment_hardness": m.get("comment_hardness"),
        "comment_misc": m.get("comment_misc"),
    }


def _write_children(conn, payload, site_id):
    m = payload.get("meta") or {}
    g = payload.get("ground") or {}
    sc = payload.get("ssa_calibration") or {}
    ssa_name = (sc.get("instrument") or "").strip()
    # -- observers ---------------------------------------------------
    def get_or_create_observer(name):
        name = (name or "").strip()
        if not name:
            return None
        row = conn.execute(
            "SELECT observer_id FROM observers WHERE name = ?", (name,)).fetchone()
        if row:
            return row[0]
        return conn.execute(
            "INSERT INTO observers (name) VALUES (?)", (name,)).lastrowid

    rb = get_or_create_observer(m.get("recorded_by"))
    if rb:
        conn.execute("""INSERT OR IGNORE INTO site_observers
            (site_id, observer_id, role) VALUES (?,?,'recorder')""", (site_id, rb))
    for name in (m.get("surveyors") or "").split(","):
        oid = get_or_create_observer(name)
        if oid:
            conn.execute("""INSERT OR IGNORE INTO site_observers
                (site_id, observer_id, role) VALUES (?,?,'surveyor')""", (site_id, oid))

    # -- instruments ---------------------------------------------------
    def get_or_create_instrument(name):
        name = (name or "").strip()
        if not name:
            return None
        row = conn.execute(
            "SELECT instrument_id FROM instruments WHERE name = ?", (name,)).fetchone()
        if row:
            return row[0]
        return conn.execute(
            "INSERT INTO instruments (name) VALUES (?)", (name,)).lastrowid

    for inst in (payload.get("instruments") or []):
        iid = get_or_create_instrument(inst.get("name"))
        if iid is None:
            continue
        used = _instrument_used(inst.get("used"))
        serial = (inst.get("sn") or "").strip() or None
        if used != "Y":
            serial = None
        conn.execute("""INSERT OR REPLACE INTO site_instruments
            (site_id, instrument_id, serial_number, used) VALUES (?,?,?,?)""",
            (site_id, iid, serial, used))

    # -- temperature layers -------------------------------------------
    # Profile start/end times attach to the top and bottom of the
    # sorted profile. A SINGLE measurement is both the first and last
    # row, so it carries "start/end" combined (or whichever exists) —
    # previously the end time silently won and the start was lost.
    t_start = m.get("temp_time_start") or ""
    t_end = m.get("temp_time_end") or ""
    trows = sorted(
        (payload.get("temperature") or []),
        key=lambda r: (r.get("height") if r.get("height") is not None else -1e9),
        reverse=True)
    n_t = len(trows)
    for idx, r in enumerate(trows):
        if n_t == 1:
            tr_time = f"{t_start}/{t_end}" if (t_start and t_end) else (t_start or t_end)
        elif idx == 0:
            tr_time = t_start
        elif idx == n_t - 1:
            tr_time = t_end
        else:
            tr_time = ""
        conn.execute("""INSERT INTO layers (site_id, kind, height_cm, value_a, time_recorded)
            VALUES (?,?,?,?,?)""",
            (site_id, "temperature", r.get("height"), r.get("temp"), tr_time))

    # -- density layers ------------------------------------------------
    for r in (payload.get("density") or []):
        conn.execute("""INSERT INTO layers
            (site_id, kind, top_cm, bottom_cm, value_a, value_b, value_c)
            VALUES (?,?,?,?,?,?,?)""",
            (site_id, "density", r.get("top"), r.get("bottom"),
             r.get("a"), r.get("b"), r.get("c")))

    # -- Interval board SWE samples (A/B/C) ------------------------------
    for sm in (g.get("swe_samples") or []):
        if sm.get("sample") not in ("A", "B", "C"):
            continue
        conn.execute("""INSERT OR REPLACE INTO swe_samples
            (site_id, sample, depth_cm, swe_mm, density_kgm3)
            VALUES (?,?,?,?,?)""",
            (site_id, sm["sample"], sm.get("depth"), sm.get("swe"), sm.get("density")))

    # -- LWC layers ----------------------------------------------------
    # Linked to the device the crew actually recorded in the form's
    # "LWC device" field (free text, get-or-created), mirroring the
    # sheet's "LWC Device & SN" header. Blank -> NULL, never a
    # fabricated attribution. The device+serial also lands in
    # site_instruments so the per-pit SN is preserved.
    lwc_name = (m.get("lwc_device") or "").strip()
    lwc_inst = get_or_create_instrument(lwc_name) if lwc_name else None
    if lwc_inst is not None:
        conn.execute("""INSERT OR REPLACE INTO site_instruments
            (site_id, instrument_id, serial_number, used)
            VALUES (?,?,?,?)""",
            (site_id, lwc_inst, (m.get("lwc_device_sn") or "").strip(), "Y"))
    for r in (payload.get("lwc") or []):
        conn.execute("""INSERT INTO layers
            (site_id, kind, top_cm, bottom_cm, value_a, value_b, instrument_id)
            VALUES (?,?,?,?,?,?,?)""",
            (site_id, "lwc", r.get("top"), r.get("bottom"),
             r.get("a"), r.get("b"), lwc_inst))

    # -- stratigraphy layers --------------------------------------------
    # layer_density_kgm3 is the optional per-layer density (§7 toggle);
    # measured-only, like everything else in the DB.
    #
    # value_a / value_b carry the TWO readings the mean came from.
    # Only the mean used to be stored, so a query against the database
    # could see 285 but never the 280 and 290 behind it — while §5,
    # writing to these very columns on its own rows, kept both. The
    # asymmetry was invisible because the round trip still worked: the
    # form reloads from raw_json, which holds everything. Anyone
    # reaching the data by SQL — which is the point of having tables —
    # got the derived number and no way back to the measurements.
    for r in (payload.get("stratigraphy") or []):
        conn.execute("""INSERT INTO layers
            (site_id, kind, top_cm, bottom_cm,
             value_a, value_b,
             grain_size_min_mm, grain_size_max_mm, grain_size_avg_mm,
             grain_type, hand_hardness, manual_wetness,
             layer_density_kgm3, comments)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (site_id, "stratigraphy", r.get("top"), r.get("bottom"),
             r.get("layer_density_a"), r.get("layer_density_b"),
             r.get("gmin"), r.get("gmax"), r.get("gavg"),
             r.get("gtype"), r.get("hardness"), r.get("wetness"),
             r.get("layer_density"), r.get("comments")))

    # -- SSA -----------------------------------------------------------
    # The SSA device (IceCube/IRIS/IRIS2, seeded — get_or_create is the
    # backstop for a new device name). When NO device was selected the
    # rows store NULL rather than a fabricated attribution; calibration
    # data without a device was already rejected before the transaction.
    ssa_inst_id = get_or_create_instrument(ssa_name) if ssa_name else None
    for r in (payload.get("ssa") or []):
        conn.execute("""INSERT INTO layers
            (site_id, kind, height_cm, signal_v, reflectance_pct, ssa_m2kg,
             grain_type, comments, instrument_id)
            VALUES (?,?,?,?,?,?,?,?,?)""",
            (site_id, "ssa", r.get("height"), r.get("signal"),
             r.get("reflectance"), r.get("ssa"),
             r.get("grain_type"), r.get("comments"), ssa_inst_id))

    spec = sc.get("spectralon") or []
    calv = sc.get("calib_values") or []
    op = (sc.get("operator") or "").strip() or None
    for i in range(max(len(spec), len(calv))):
        conn.execute("""INSERT INTO ssa_calibration
            (site_id, instrument_id, operator,
             spectralon_level, calib_value_v, measured_at, notes)
            VALUES (?,?,?,?,?,?,?)""",
            (site_id, ssa_inst_id, op,
             spec[i] if i < len(spec) else None,
             calv[i] if i < len(calv) else None,
             sc.get("measured_at"), sc.get("notes") if i == 0 else ""))

def save_pit(payload, site_id=None, pending_export_folder=None):
    """Create a pending first archive or update a loaded pit in place.

    Returns ``("ok", info)`` where info contains site_id/pit_id/updated, or
    ``("exists", info)`` for a new form whose human pit_id is already owned by
    the current user.  A caller must finalize ``pending_export_folder`` only
    after filesystem publication succeeds.
    """
    err = _validate_payload(payload)
    if err:
        return "error", err
    owner = current_user()
    m = payload.get("meta") or {}
    pid = (m.get("pit_id") or "").strip()
    raw = {k: v for k, v in payload.items()
           if k not in {"overwrite", "site_id", "attachment_manifest"}}
    raw_json = json.dumps(raw, separators=(",", ":"), ensure_ascii=False)
    manifest = _attachment_manifest(payload)
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        existing = None
        if site_id:
            existing = conn.execute(
                "SELECT site_id, owner, export_folder FROM sites WHERE site_id=?",
                (site_id,)).fetchone()
            if not existing or existing[1] != owner:
                conn.execute("ROLLBACK")
                return "error", "Loaded pit was not found or is not owned by this account."
            duplicate = conn.execute(
                "SELECT site_id FROM sites WHERE owner=? AND pit_id=? AND site_id<>?",
                (owner, pid, site_id)).fetchone()
            if duplicate:
                conn.execute("ROLLBACK")
                return "error", f"Pit ID '{pid}' is already used by another saved pit."
        else:
            duplicate = conn.execute(
                "SELECT site_id FROM sites WHERE owner=? AND pit_id=?",
                (owner, pid)).fetchone()
            if duplicate:
                conn.execute("ROLLBACK")
                return "exists", {"site_id": duplicate[0], "pit_id": pid}
            site_id = str(uuid.uuid4())

        camp_name = (m.get("campaign") or "").strip() or CAMPAIGN
        conn.execute("INSERT OR IGNORE INTO campaigns (name) VALUES (?)", (camp_name,))
        campaign_id = conn.execute(
            "SELECT campaign_id FROM campaigns WHERE name=?", (camp_name,)).fetchone()[0]
        values = _site_values(payload, owner, raw_json, campaign_id)

        if existing:
            assignments = ", ".join(f'"{c}"=?' for c in values)
            conn.execute(
                f"UPDATE sites SET {assignments}, pending_export_folder=?, "
                "updated_at=datetime('now') WHERE site_id=?",
                [*values.values(), pending_export_folder, site_id])
            for table in ("site_observers", "site_instruments", "layers",
                          "ssa_calibration", "swe_samples"):
                conn.execute(f"DELETE FROM {table} WHERE site_id=?", (site_id,))
        else:
            cols = ["site_id", *values.keys(), "export_folder", "pending_export_folder"]
            qcols = ", ".join(f'"{c}"' for c in cols)
            conn.execute(
                f"INSERT INTO sites ({qcols}) VALUES ({','.join('?' for _ in cols)})",
                [site_id, *values.values(), None, pending_export_folder])

        _write_children(conn, payload, site_id)
        _sync_attachment_manifest(conn, site_id, manifest)
        revision = record_revision(conn, site_id, owner, raw)
        conn.execute("COMMIT")
        return "ok", {"site_id": site_id, "pit_id": pid, "updated": bool(existing),
                      "export_folder": existing[2] if existing else None,
                      "pending_export_folder": pending_export_folder,
                      "revision_id": revision["revision_id"],
                      "revision_number": revision["revision_number"],
                      "revision_created": revision["created"]}
    except ValueError as exc:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        return "error", str(exc)
    except Exception:
        logging.getLogger(__name__).exception("pit persistence failed")
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        return "error", "The pit could not be saved. Use the request ID when contacting support."
    finally:
        conn.close()


def finalize_export(site_id, export_folder):
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT owner FROM sites WHERE site_id=?", (site_id,)).fetchone()
        if not row or row[0] != current_user():
            conn.execute("ROLLBACK")
            return False
        conn.execute(
            "UPDATE sites SET export_folder=?, pending_export_folder=NULL, "
            "updated_at=datetime('now') WHERE site_id=?",
            (export_folder, site_id))
        conn.execute("COMMIT")
        return True
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        conn.close()


def get_site_state(site_id, include_raw=False):
    conn = get_conn()
    try:
        cols = "site_id, pit_id, owner, export_folder, pending_export_folder"
        if include_raw:
            cols += ", raw_json"
        row = conn.execute(
            f"SELECT {cols} FROM sites WHERE site_id=? AND owner=?",
            (site_id, current_user())).fetchone()
        if not row:
            return None
        keys = ["site_id", "pit_id", "owner", "export_folder", "pending_export_folder"]
        if include_raw:
            keys.append("raw_json")
        return dict(zip(keys, row))
    finally:
        conn.close()


def attachment_upload_summary(site_id):
    conn = get_conn()
    try:
        rows = conn.execute(
            """SELECT status,COUNT(*) FROM attachment_uploads u
               JOIN sites s ON s.site_id=u.site_id
               WHERE u.site_id=? AND s.owner=? GROUP BY status""",
            (site_id, current_user())).fetchall()
        out = {"pending": 0, "stored": 0, "cancelled": 0}
        out.update({status: count for status, count in rows})
        out["total"] = sum(out.values())
        return out
    finally:
        conn.close()


def list_attachment_uploads(site_id, include_cancelled=False):
    conn = get_conn()
    try:
        where = "" if include_cancelled else " AND u.status<>'cancelled'"
        rows = conn.execute(
            """SELECT u.queue_id,u.category,u.original_filename,u.mime_type,
                      u.size_bytes,u.client_sha256,u.top_cm,u.bottom_cm,u.status,
                      u.attachment_id,u.last_error,u.publication_state,
                      u.created_at,u.updated_at
               FROM attachment_uploads u JOIN sites s ON s.site_id=u.site_id
               WHERE u.site_id=? AND s.owner=?""" + where +
            " ORDER BY u.created_at,u.queue_id",
            (site_id, current_user())).fetchall()
        keys = ["queue_id","category","filename","mime_type","size_bytes",
                "sha256","top_cm","bottom_cm","status","attachment_id",
                "last_error","publication_state","created_at","updated_at"]
        return [dict(zip(keys, row)) for row in rows]
    finally:
        conn.close()


def cancel_attachment_upload(site_id, queue_id):
    qid = normalize_queue_id(queue_id)
    # Resolve a lost-response publication before deciding whether this item is
    # still cancellable. A stored photograph must go through safe deletion.
    from .attachment_storage import recover_upload
    try:
        recover_upload(qid, current_user())
    except Exception:
        pass
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """SELECT u.status,u.publication_state FROM attachment_uploads u
               JOIN sites s ON s.site_id=u.site_id
               WHERE u.queue_id=? AND u.site_id=? AND s.owner=?""",
            (qid, site_id, current_user())).fetchone()
        if not row:
            conn.execute("ROLLBACK")
            return {"ok": True, "absent": True, "queue_id": qid}
        if row[0] == "stored":
            conn.execute("ROLLBACK")
            return {"ok": False, "stored": True, "queue_id": qid,
                    "msg": "This photograph is already stored; use attachment deletion instead."}
        if row[1] is not None:
            conn.execute("ROLLBACK")
            return {"ok": False, "recoverable": True, "queue_id": qid,
                    "msg": "This photograph has an unfinished storage operation. Retry recovery first."}
        conn.execute(
            """UPDATE attachment_uploads SET status='cancelled',last_error=NULL,
               publication_state=NULL,staged_relpath=NULL,target_relpath=NULL,
               server_sha256=NULL,updated_at=datetime('now') WHERE queue_id=?""",
            (qid,))
        conn.execute("COMMIT")
        return {"ok": True, "cancelled": True, "queue_id": qid}
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        conn.close()


def load_pit(site_id):
    state = get_site_state(site_id, include_raw=True)
    if not state:
        return None, "Pit not found."
    if state["pending_export_folder"] is not None:
        return None, "This pit has an archive operation that needs recovery."
    if not state["raw_json"]:
        return None, "Pit has no stored form payload."
    try:
        return json.loads(state["raw_json"]), None
    except Exception:
        logging.getLogger(__name__).exception("stored pit payload is invalid JSON")
        return None, "The stored pit payload is unreadable; recovery is required."


_PIT_SORTS = {
    "updated": "COALESCE(s.updated_at, s.created_at) DESC, s.date DESC, s.pit_id COLLATE NOCASE ASC",
    "date": "s.date DESC, s.pit_id COLLATE NOCASE ASC",
    "pit_id": "s.pit_id COLLATE NOCASE ASC, s.date DESC",
}


def _like_pattern(value):
    """Literal substring search pattern for SQLite LIKE.

    Percent and underscore are useful characters in field identifiers, so a
    user searching for them should not accidentally turn them into SQL
    wildcards. Exclamation mark is the explicit ESCAPE character below.
    """
    escaped = str(value).replace("!", "!!").replace("%", "!%").replace("_", "!_")
    return "%" + escaped + "%"


def search_pits(*, limit=10, offset=0, query="", campaign="", date_from="",
                date_to="", sort="date"):
    """Search the current owner's archived pits with stable pagination.

    Recovery-required rows are deliberately excluded; callers render those in
    a separate workflow via :func:`list_pending_pits`. The owner predicate is
    always present and cannot be supplied by the client.
    """
    limit = max(1, min(int(limit), 50))
    offset = max(0, int(offset))
    query = (query or "").strip()[:200]
    campaign = (campaign or "").strip()[:200]
    sort_sql = _PIT_SORTS.get(sort, _PIT_SORTS["date"])

    where = ["s.owner=?", "s.pending_export_folder IS NULL"]
    params = [current_user()]
    if campaign:
        where.append("c.name=?")
        params.append(campaign)
    if date_from:
        where.append("s.date>=?")
        params.append(date_from)
    if date_to:
        where.append("s.date<=?")
        params.append(date_to)
    if query:
        pattern = _like_pattern(query.lower())
        where.append("""(
            lower(s.pit_id) LIKE ? ESCAPE '!'
            OR lower(COALESCE(s.site,'')) LIKE ? ESCAPE '!'
            OR lower(COALESCE(s.location,'')) LIKE ? ESCAPE '!'
            OR lower(COALESCE(c.name,'')) LIKE ? ESCAPE '!'
            OR lower(COALESCE(s.date,'')) LIKE ? ESCAPE '!'
            OR lower(COALESCE(s.recorded_by,'')) LIKE ? ESCAPE '!'
            OR lower(COALESCE(s.surveyors,'')) LIKE ? ESCAPE '!'
            OR EXISTS (
                SELECT 1 FROM site_observers so
                JOIN observers o ON o.observer_id=so.observer_id
                WHERE so.site_id=s.site_id
                  AND lower(o.name) LIKE ? ESCAPE '!'
            )
        )""")
        params.extend([pattern] * 8)

    where_sql = " AND ".join(where)
    conn = get_conn()
    try:
        total = conn.execute(
            f"""SELECT COUNT(*)
                  FROM sites s LEFT JOIN campaigns c ON c.campaign_id=s.campaign_id
                 WHERE {where_sql}""", params).fetchone()[0]
        rows = conn.execute(
            f"""SELECT s.site_id, s.pit_id, s.location, s.site, s.date,
                       COALESCE(s.updated_at, s.created_at), c.name,
                       s.recorded_by, s.surveyors,
                       (SELECT COUNT(*) FROM attachment_uploads u
                         WHERE u.site_id=s.site_id AND u.status='pending'),
                       (SELECT COUNT(*) FROM attachments a
                         WHERE a.site_id=s.site_id AND a.pending_delete=0),
                       (SELECT COUNT(*) FROM attachments a
                         WHERE a.site_id=s.site_id AND a.pending_delete=0
                           AND COALESCE(a.storage_status,'stored')='missing')
                  FROM sites s LEFT JOIN campaigns c ON c.campaign_id=s.campaign_id
                 WHERE {where_sql}
                 ORDER BY {sort_sql}
                 LIMIT ? OFFSET ?""", [*params, limit, offset]).fetchall()
        pits = [{
            "site_id": r[0], "pit_id": r[1], "location": r[2], "site": r[3],
            "date": r[4], "updated_at": r[5], "campaign": r[6],
            "recorded_by": r[7], "surveyors": r[8], "pending_photos": r[9],
            "attachment_count": r[10], "missing_attachments": r[11],
        } for r in rows]
        return {
            "pits": pits, "total": total, "limit": limit, "offset": offset,
            "has_more": offset + len(pits) < total,
        }
    finally:
        conn.close()


def workspace_summary(recent_limit=3):
    """Compact owner-scoped data for the Stage 11 landing workspace.

    The authenticated owner predicate is applied inside the repository. The
    browser cannot request another owner's summary. Browser-local IndexedDB
    photo counts are intentionally not included here; the client adds those
    separately because the server cannot see files that have not been
    registered or uploaded yet.
    """
    recent_limit = max(1, min(int(recent_limit), 10))
    recent = search_pits(limit=recent_limit, offset=0, sort="updated")
    recovery = list_pending_pits(limit=25)
    conn = get_conn()
    try:
        owner = current_user()
        expected_photos = conn.execute(
            """SELECT COUNT(*) FROM attachment_uploads u
               JOIN sites s ON s.site_id=u.site_id
               WHERE s.owner=? AND u.status='pending'""",
            (owner,)).fetchone()[0]
        missing_attachments = conn.execute(
            """SELECT COUNT(*) FROM attachments a
               JOIN sites s ON s.site_id=a.site_id
               WHERE s.owner=? AND a.pending_delete=0
                 AND COALESCE(a.storage_status,'stored')='missing'""",
            (owner,)).fetchone()[0]
        return {
            "recent": recent["pits"],
            "total_pits": recent["total"],
            "recovery": recovery,
            "recovery_count": len(recovery),
            "expected_photos": expected_photos,
            "missing_attachments": missing_attachments,
        }
    finally:
        conn.close()


def list_pits(limit):
    """Backward-compatible recent-pits helper used by lifecycle tests."""
    return search_pits(limit=limit, sort="updated")["pits"]


def list_owner_campaigns():
    """Campaign filters available to the current owner, with pit counts."""
    conn = get_conn()
    try:
        rows = conn.execute(
            """SELECT c.name, COUNT(*)
                 FROM sites s JOIN campaigns c ON c.campaign_id=s.campaign_id
                WHERE s.owner=? AND s.pending_export_folder IS NULL
                GROUP BY c.campaign_id, c.name
                ORDER BY c.name COLLATE NOCASE""",
            (current_user(),)).fetchall()
        return [{"name": name, "count": count} for name, count in rows]
    finally:
        conn.close()


def list_pending_pits(limit=25):
    conn = get_conn()
    try:
        rows = conn.execute(
            """SELECT site_id, pit_id, export_folder, pending_export_folder, updated_at
               FROM sites WHERE owner=? AND pending_export_folder IS NOT NULL
               ORDER BY COALESCE(updated_at, created_at) DESC LIMIT ?""",
            (current_user(), limit)).fetchall()
        return [{"site_id": r[0], "pit_id": r[1], "export_folder": r[2],
                 "pending_export_folder": r[3], "updated_at": r[4]} for r in rows]
    finally:
        conn.close()
