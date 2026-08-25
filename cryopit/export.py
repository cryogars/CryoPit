"""SnowEx-compatible CSV export.

One source of truth: `_build_csvs(payload)` turns a pit payload (the exact
shape the browser sends / sites.raw_json stores) into the seven CSV texts.
Both delivery paths — browser zip download and server-side archive folder —
run through it, so the files are byte-identical either way.

Files per pit, named by _fname() as
{CAMPAIGN}_{PitID}_{YYYYMMDD}_{parameter}_v01_0.csv:
    …_siteDetails_…            …_LWC_…
    …_temperature_…            …_stratigraphy_…
    …_density_…                …_SSA_…
    …_density_gap_filled_…     (derived; see docs/DENSITY.md)

Missing values are written as NO_DATA (-9999), the SnowEx convention.
"""
import csv
import io
import json
import os
import re
import zipfile

from .density import analyze, column_value_over
from .config import CAMPAIGN, EXPORT_DIR, NO_DATA
from .db import get_conn


def _c(v):
    """Cell value: NO_DATA for missing, verbatim otherwise (0 is a real value)."""
    if v is None or v == "":
        return NO_DATA
    return v


def _iso_time(t):
    """Field times are entered as military HHMM ('0830'). CSV headers and time
    columns emit ISO HH:MM ('08:30') so downstream datetime parsers accept the
    composed '{date}T{time}'. Anything not exactly HHMM passes through as-is."""
    t = (t or "").strip()
    if len(t) == 4 and t.isdigit():
        return t[:2] + ":" + t[2:]
    return t


def _dt(m):
    """'YYYY-MM-DDTHH:MM' when the pit-open time exists, else just the date —
    never a dangling 'YYYY-MM-DDT'."""
    d = (m.get("date") or "").strip()
    t = _iso_time(m.get("pit_open_time"))
    return f"{d}T{t}" if (d and t) else d


def _hdr(m):
    """The shared '# ' comment block that opens every data CSV."""
    return [
        ["# Location", _c(m.get("location"))],
        ["# Site", _c(m.get("site"))],
        ["# PitID", _c(m.get("pit_id"))],
        ["# Date/Local Standard Time", _c(_dt(m))],
        ["# UTM Zone", f"{m.get('utm_zone_number') or ''}{m.get('utm_zone_letter') or ''}" or NO_DATA],
        ["# Easting", _c(m.get("utm_easting"))],
        ["# Northing", _c(m.get("utm_northing"))],
        ["# Latitude", _c(m.get("latitude"))],
        ["# Longitude", _c(m.get("longitude"))],
        ["# Flags", _c(m.get("flags") or "None")],
    ]


def _csv(rows):
    """Rows -> CSV text with CRLF line endings (RFC 4180 / Excel-friendly).
    The UTF-8 BOM is added at encode time by the zip/folder writers."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    for r in rows:
        w.writerow(r)
    return buf.getvalue()


_SAFE = re.compile(r"[^A-Za-z0-9_\-]+")


def _safe_name(s, fallback="unnamed"):
    """Filename/foldername component: alphanumerics, _ and - only. Everything
    else (separators, dots, spaces) collapses to _, so no component can carry
    a path traversal ('..') or separator into a filesystem path."""
    s = _SAFE.sub("_", (s or "").strip()).strip("_")
    return s or fallback


def _fname(m, kind):
    """{CAMPAIGN}_{PitID}_{YYYYMMDD}_{parameter}_v01_0.csv — the SnowEx-style
    convention documented in docs/STRUCTURE.md (restored from the original)."""
    camp = _safe_name(m.get("campaign") or CAMPAIGN, "campaign")
    pid = _safe_name(m.get("pit_id"), "pit")
    dstr = _safe_name((m.get("date") or "").replace("-", ""), "nodate")
    return f"{camp}_{pid}_{dstr}_{kind}_v01_0.csv"


def _sortkey_interval(r):
    t = r.get("top"); return -(t if isinstance(t, (int, float)) else float("-inf"))


def _sortkey_height(r):
    h = r.get("height"); return -(h if isinstance(h, (int, float)) else float("-inf"))


def _weather_csv(value):
    """Render canonical weather arrays and legacy scalar values consistently."""
    if value is None or value == "":
        return NO_DATA
    if isinstance(value, list):
        return "; ".join(str(v) for v in value if str(v).strip()) or NO_DATA
    return str(value)


def _build_csvs(payload):
    """payload -> {filename: csv_text} for all seven files. All interval and
    point series are written surface -> ground regardless of entry order."""
    m = payload.get("meta") or {}
    wx = payload.get("weather") or {}
    g = payload.get("ground") or {}
    out = {}

    # -- siteDetails ---------------------------------------------------------
    rows = _hdr(m) + [
        ["# Campaign", _c(m.get("campaign") or CAMPAIGN)],
        ["# Total Depth (cm)", _c(m.get("total_depth"))],
        ["# Elevation (m)", _c(m.get("elevation"))],
        ["# Slope (deg)", _c(m.get("slope_angle"))],
        ["# Recorded By", _c(m.get("recorded_by"))],
        ["# Surveyors", _c(m.get("surveyors"))],
        ["# GPS", _c(m.get("gps_device"))],
        ["# GPS Uncertainty",
            f"{m.get('gps_uncertainty')} {m.get('gps_uncertainty_unit') or 'm'}"
            if m.get("gps_uncertainty") is not None else NO_DATA],
        ["# LWC Device", _c(m.get("lwc_device"))],
        ["# LWC Device SN", _c(m.get("lwc_device_sn"))],
        ["# Precipitation Rate", _weather_csv(wx.get("precip_rate"))],
        ["# Precipitation Type", _weather_csv(wx.get("precip_type"))],
        ["# Sky", _weather_csv(wx.get("sky"))],
        ["# Wind", _weather_csv(wx.get("wind"))],
        ["# Ground Condition", _weather_csv(g.get("condition"))],
        ["# Ground Roughness", _c(g.get("roughness"))],
        ["# Tree Canopy", _c(g.get("canopy"))],
        ["# Snow Cover Condition", _c(g.get("snow_cover"))],
        ["# Standing Water", _c(g.get("standing_water"))],
        ["# Vegetation", ",".join(g.get("vegetation") or []) or NO_DATA],
        ["# Vegetation Height (cm)", _c(g.get("veg_height"))],
    ]
    # Interval board SWE block (samples A/B/C + melt evidence), as on the sheet
    for sm in (g.get("swe_samples") or []):
        rows.append([f"# Interval Board {sm.get('sample')} Depth (cm)", _c(sm.get("depth"))])
        rows.append([f"# Interval Board {sm.get('sample')} SWE (mm)", _c(sm.get("swe"))])
        rows.append([f"# Interval Board {sm.get('sample')} Density (kg/m3)", _c(sm.get("density"))])
    rows += [
        ["# Evidence of Melt (SWE loss)", _c(g.get("melt_evidence"))],
        ["# Density Cutter", _c(m.get("density_cutter"))],
        # Derived values (computed, not measured — full detail and provenance
        # in the density_gap_filled CSV):
        ["# Derived Bulk Density (kg/m3)", "__DERIVED_BULK__"],
        ["# Derived SWE (mm)", "__DERIVED_SWE__"],
        ["# Density Measured Coverage A / B / Extra (%)", "__DERIVED_COV__"],
        ["# Pit Comments", _c(m.get("comments"))],
        ["# Weather Comments", _c(m.get("comment_weather"))],
        ["# Pit Notes", _c(m.get("comment_pit"))],
        ["# Hardness Notes", _c(m.get("comment_hardness"))],
        ["# Misc Notes", _c(m.get("comment_misc"))],
    ]
    # Instrument checklist as one summary row each. Missing/unanswered is
    # NO_DATA, never an invented N; Y and N remain explicit categorical values.
    for inst in (payload.get("instruments") or []):
        raw_used = inst.get("used")
        used = raw_used if raw_used in ("Y", "N") else NO_DATA
        sn = inst.get("sn") or ""
        val = str(used) + (f" (SN {sn})" if used == "Y" and sn else "")
        rows.append([f"# Instrument: {inst.get('name')}", val])
    out[_fname(m, "siteDetails")] = _csv(rows)

    # -- temperature ---------------------------------------------------------
    # Sorted surface-first. Time start attaches to the first row, end to the
    # last, NO_DATA in between. A single-measurement profile is both first and
    # last, so it carries "start/end" combined (or whichever one exists).
    t_start = _iso_time(m.get("temp_time_start"))
    t_end = _iso_time(m.get("temp_time_end"))
    trows = sorted(
        (payload.get("temperature") or []),
        key=lambda r: (r.get("height") if r.get("height") is not None else -1e9),
        reverse=True)
    n_t = len(trows)
    # A negative height is a reading taken BELOW the snow-ground interface —
    # that is a SOIL temperature, not a "ground" one. "Ground" is ambiguous in
    # a snow-pit file: it is also the name of the interface itself (height 0),
    # so "# Ground temperature: Yes" could be read as "there is a reading at the
    # base of the pack", which is the opposite of what it marks.
    # Marked in the header so the file says so itself — otherwise the only clue
    # is a negative number, which reads as a typo to anyone who did not take it.
    _ground = [r for r in trows
               if isinstance(r.get("height"), (int, float)) and r["height"] < 0]
    _gh = [["# Soil temperature", "Yes" if _ground else "No"]]
    if _ground:
        _gh.append(["# Soil temperature depths (cm below snow-ground interface)",
                    "; ".join(str(abs(r["height"])) for r in _ground)])
    rows = _hdr(m) + _gh + [["# Height (cm)", "Temperature (deg C)", "Time start/end"]]
    for idx, r in enumerate(trows):
        if n_t == 1:
            tv = f"{t_start}/{t_end}" if (t_start and t_end) else (t_start or t_end)
        elif idx == 0:
            tv = t_start
        elif idx == n_t - 1:
            tv = t_end
        else:
            tv = ""
        rows.append([_c(r.get("height")), _c(r.get("temp")), _c(tv)])
    out[_fname(m, "temperature")] = _csv(rows)

    # -- density ---------------------------------------------------------------
    def _avg(vals):
        # zero/negative densities are physically impossible and must never
        # pollute an average (they're also blocked by validation)
        vals = [v for v in vals if isinstance(v, (int, float)) and v > 0]
        return round(sum(vals) / len(vals), 1) if vals else None

    drows = sorted(payload.get("density") or [], key=_sortkey_interval)
    rows = _hdr(m) + [["# Top (cm)", "Bottom (cm)",
                       "Density Profile A (kg/m3)", "Density Profile B (kg/m3)", "Extra Density (kg/m3)",
                       "Avg Density (kg/m3)"]]
    dens_by_top = {}
    for r in drows:
        avg = _avg([r.get("a"), r.get("b"), r.get("c")])
        if r.get("top") is not None and avg is not None:
            dens_by_top[round(r["top"])] = avg
        rows.append([_c(r.get("top")), _c(r.get("bottom")),
                     _c(r.get("a")), _c(r.get("b")), _c(r.get("c")), _c(avg)])
    # Overall derived values live HERE per user decision (clearly marked as
    # derived; computation in docs/DENSITY.md). Per-profile derivations stay
    # in the gap-filled CSV.
    _dd = analyze(drows, m.get("total_depth"), payload.get("stratigraphy"))
    _cov = _dd["coverage"]
    rows[len(_hdr(m)):len(_hdr(m))] = [
        ["# Derived Bulk Density (kg/m3)",
         _c(round(_dd["bulk"], 1) if _dd["bulk"] is not None else None)],
        ["# Derived SWE (mm)",
         _c(round(_dd["swe"], 1) if _dd["swe"] is not None else None)],
        ["# Measured Coverage A / B / Extra (%)",
         f"{_cov.get('A', 0)} / {_cov.get('B', 0)} / {_cov.get('Extra', 0)}"],
        ["# Derivation", "see docs/DENSITY.md"],
        # Coverage is stated ONCE for the profiles present rather than repeated as a
# tag on every derived value. Extra is omitted entirely when no profile used
# it, so the header does not carry a column that never existed.

    ]
    out[_fname(m, "density")] = _csv(rows)

    # -- density_gap_filled (derived; see docs/DENSITY.md) -------------
    # The verbatim file above is the measurement record; this one is the
    # analysis-ready column: geometry cleaned (overlaps clipped, upper wins),
    # every vertical gap resolved, per-row provenance in Source. Cells of
    # profiles not measured on a row stay EMPTY here (never -9999, never
    # synthesized). Extra's column appears only if Extra was ever measured.
    dd = _dd   # computed above for the density CSV header
    # THE FULLY-FILLED MIRROR of the density CSV: same table, no holes.
    # Every profile cell is filled — measured values where measured, that
    # profile's own gap-filled column elsewhere; Source lists which profiles
    # were measured on each row (everything not listed is gap-filled).
    # Profiles with no measurements anywhere are omitted (nothing to fill
    # from). The measured-only view lives in the density CSV (-9999 holes).
    present = [(k, lbl) for k, lbl in (("a", "A"), ("b", "B"), ("c", "Extra"))
               if lbl in dd["profiles"]]
    hdr2 = _hdr(m) + [
        ["# HS used for gap filling (cm)", _c(dd.get("hs"))],
        # The Source column on every row already says how that row was
        # derived, so the header only needs to point at the rules rather than
        # restate them.
        ["# Derivation", "see docs/DENSITY.md"],
        ["# Measured coverage " + " / ".join(lbl for _k, lbl in present) + " (%)",
         *[_c(dd["profiles"][lbl]["coverage_pct"]) for _k, lbl in present]],
    ]
    if dd.get("layer_fallback"):
        hdr2.append(["# Density source",
                     "per-layer densities (stratigraphy); no interval densities were measured"])
    for label, p in dd["profiles"].items():
        note = ("" if p["coverage_pct"] >= 99.95
                else "")
        hdr2.append([f"# Derived Bulk Density {label} (kg/m3){note}", _c(round(p["bulk"], 1))])
        hdr2.append([f"# Derived SWE {label} (mm){note}", _c(round(p["swe"], 1))])
    cols = ["# Top (cm)", "Bottom (cm)"]
    cols += [f"Profile {lbl} (kg/m3)" if lbl != "Extra" else "Extra (kg/m3)"
             for _k, lbl in present]
    cols += ["Density (kg/m3)", "Source"]
    rows = hdr2 + [cols]
    for r in dd["column"]:
        line = [_c(r["top"]), _c(r["bottom"])]
        for k, lbl in present:
            v = r.get(k)
            if v is None or v <= 0:   # not measured here: this profile's own filled column
                v = column_value_over(dd["profiles"][lbl]["column"], r["top"], r["bottom"])
            line.append(_c(round(v, 1)) if v is not None else _c(None))
        src = r["source"]
        if r.get("profs") and src.startswith("measured") and r["profs"] != "layer":
            src = f"{src} [{r['profs']} measured]"
        line += [_c(round(r["value"], 1)), src]
        rows.append(line)
    out[_fname(m, "density_gap_filled")] = _csv(rows)

    _derived = dd   # reused for the siteDetails derived block below

    # -- LWC ---------------------------------------------------------------
    # Avg Density is joined from the density interval with the same (rounded)
    # top height, when one exists — the crews measure both on the same grid.
    rows = _hdr(m) + [["# Top (cm)", "Bottom (cm)", "Avg Density (kg/m3)",
                       "Permittivity A", "Permittivity B"]]
    for r in sorted(payload.get("lwc") or [], key=_sortkey_interval):
        top = r.get("top")
        avg = dens_by_top.get(round(top)) if top is not None else None
        rows.append([_c(top), _c(r.get("bottom")), _c(avg),
                     _c(r.get("a")), _c(r.get("b"))])
    out[_fname(m, "LWC")] = _csv(rows)

    # -- stratigraphy ---------------------------------------------------------
    # Density (kg/m3) is the optional per-layer density (§7 toggle). The
    # column is ALWAYS present (with -9999 when not measured) so downstream
    # parsers never see a schema that appears and disappears between pits.
    rows = _hdr(m) + [["# Top (cm)", "Bottom (cm)",
                       "Grain Size min (mm)", "Grain Size max (mm)", "Grain Size avg (mm)",
                       "Grain Type", "Hand Hardness", "Manual Wetness",
                       "Density (kg/m3)", "Comments"]]
    for r in sorted(payload.get("stratigraphy") or [], key=_sortkey_interval):
        rows.append([_c(r.get("top")), _c(r.get("bottom")),
                     _c(r.get("gmin")), _c(r.get("gmax")), _c(r.get("gavg")),
                     _c(r.get("gtype")), _c(r.get("hardness")), _c(r.get("wetness")),
                     _c(r.get("layer_density")), _c(r.get("comments"))])
    out[_fname(m, "stratigraphy")] = _csv(rows)

    # -- SSA ---------------------------------------------------------------
    sc = payload.get("ssa_calibration") or {}
    rows = _hdr(m) + [
        ["# SSA Instrument", _c(sc.get("instrument"))],
        ["# SSA Operator", _c(sc.get("operator"))],
        ["# Calibration Time", _c(_iso_time(sc.get("measured_at")))],
        ["# Spectralon Levels", ",".join(str(v) for v in (sc.get("spectralon") or [])) or NO_DATA],
        ["# Calibration Values (V)", ",".join(str(v) for v in (sc.get("calib_values") or [])) or NO_DATA],
        ["# Calibration Notes", _c(sc.get("notes"))],
        ["# Height (cm)", "Signal (V)", "Reflectance (%)", "SSA (m2/kg)",
         "Grain Type", "Comments"],
    ]
    for r in sorted(payload.get("ssa") or [], key=_sortkey_height):
        rows.append([_c(r.get("height")), _c(r.get("signal")),
                     _c(r.get("reflectance")), _c(r.get("ssa")),
                     _c(r.get("grain_type")), _c(r.get("comments"))])
    out[_fname(m, "SSA")] = _csv(rows)

    # inject derived values into siteDetails (built before the density block)
    sd = _fname(m, "siteDetails")
    cov = _derived["coverage"]
    out[sd] = (out[sd]
        .replace("__DERIVED_BULK__", str(_c(round(_derived["bulk"], 1) if _derived["bulk"] is not None else None)))
        .replace("__DERIVED_SWE__", str(_c(round(_derived["swe"], 1) if _derived["swe"] is not None else None)))
        .replace("__DERIVED_COV__", f"{cov.get('A', 0)} / {cov.get('B', 0)} / {cov.get('Extra', 0)}"))
    return out


def export_from_payload(payload):
    """Browser payload -> {filename: csv_text}."""
    return _build_csvs(payload)


def export_all(pit_id, owner):
    """Re-export an archived pit from its stored verbatim payload, so the files
    match a fresh download of the same pit exactly. Returns ({name: text}, err)."""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT raw_json FROM sites WHERE pit_id = ? AND owner = ?",
            (pit_id, owner)).fetchone()
        if not row or not row[0]:
            return None, "Pit not found."
        return _build_csvs(json.loads(row[0])), None
    except Exception as e:
        return None, str(e)
    finally:
        conn.close()


def write_zip_to_path(csvs, m, output_path, extras=None, uploads=None):
    """Write one browser-download ZIP directly to ``output_path``.

    The member layout intentionally mirrors the archive folder: CSVs under
    ``csv/``, binary extras (profile PNG/PDF) under their supplied names, and
    already-uploaded field documents under ``uploads/``. CSVs are UTF-8 with
    BOM so Excel opens degree signs correctly.

    Writing to a file path rather than ``io.BytesIO`` keeps ZIP payload size
    from becoming Python-process memory usage. Returns ``(zipname, byte_size)``.
    """
    camp = _safe_name((m or {}).get("campaign") or CAMPAIGN, "campaign")
    pid = _safe_name((m or {}).get("pit_id"), "pit")
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in csvs.items():
            z.writestr(f"csv/{name}", text.encode("utf-8-sig"))
        for name, blob in (extras or {}).items():
            z.writestr(name, blob)
        for arcname, path in (uploads or {}).items():
            z.write(path, arcname)
    return f"{camp}_{pid}.zip", os.path.getsize(output_path)


def save_csvs_at_folder(csvs, folder):
    """Write the seven CSVs into ``folder/csv`` and return their count.

    Unlike :func:`save_csvs_to_folder`, ``folder`` is an already-resolved
    trusted server path. Archive staging uses this so its private directory can
    live beneath ``EXPORT_DIR/.staging`` without sanitizing the separators away.
    """
    csvdir = os.path.join(folder, "csv")
    os.makedirs(csvdir, exist_ok=True)
    n = 0
    for name, text in csvs.items():
        safe = _safe_name(os.path.splitext(name)[0], "file") + ".csv"
        with open(os.path.join(csvdir, safe), "w", encoding="utf-8-sig", newline="") as f:
            f.write(text)
        n += 1
    return n


def save_csvs_to_folder(csvs, subfolder):
    """Write the CSVs under EXPORT_DIR/<subfolder>/ (one folder per pit).
    The subfolder is sanitized to a single safe path component, so payload
    data can never traverse outside EXPORT_DIR. Returns (folder, count)."""
    folder = os.path.join(EXPORT_DIR, _safe_name(subfolder, "pit"))
    n = save_csvs_at_folder(csvs, folder)
    return os.path.abspath(folder), n
