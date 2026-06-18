"""
CryoPit Snow Pit Logger
Design: Clinical white · Braun/laboratory aesthetic
Flask single-origin app · SnowEx-compatible CSV export · UTM <-> lat/lon · SQLite

Run:    pip install flask
        python CryoPit_V1.py
        open http://127.0.0.1:8502
"""

from flask import Flask, request, jsonify, abort
import sqlite3
import json, io, csv as csvlib, math, os, zipfile, base64

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# -----------------------------------------------------------------------------
# CONFIG
# -----------------------------------------------------------------------------
DB_PATH     = os.getenv("CRYOPIT_DB_PATH",   "cryopit.db")
INSTITUTION = os.getenv("CRYOPIT_INSTITUTION","CryoGARS · Boise State University")
CAMPAIGN    = os.getenv("CRYOPIT_CAMPAIGN",   "SNEX25")
APP_TITLE   = os.getenv("CRYOPIT_APP_TITLE",  "CryoPit")
PORT        = int(os.getenv("CRYOPIT_PORT",   os.getenv("CRYOPIT_API_PORT", "8502")))
# Default destination for server-side CSV writes. Resolved by the Python
# process — locally that's your laptop; deployed it's the server. Point it at
# a mounted Drive, an S3-backed mount, or a synced repo directory.
EXPORT_DIR  = os.getenv("CRYOPIT_EXPORT_DIR", "exports")
NO_DATA     = -9999

# -----------------------------------------------------------------------------
# DATABASE
# -----------------------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS campaigns(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE, description TEXT,
    start_date TEXT, end_date TEXT, location TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);

CREATE TABLE IF NOT EXISTS observers(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE, email TEXT, institution TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);

CREATE TABLE IF NOT EXISTS instruments(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL, model TEXT, serial_number TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);

CREATE TABLE IF NOT EXISTS measurement_types(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE, units TEXT, derived INTEGER DEFAULT 0);

CREATE TABLE IF NOT EXISTS sites(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id INTEGER REFERENCES campaigns(id),
    pit_id TEXT NOT NULL UNIQUE, name TEXT, location TEXT,
    date TEXT NOT NULL, pit_open_time TEXT,
    temp_time_start TEXT, temp_time_end TEXT,
    utm_easting REAL, utm_northing REAL,
    utm_zone_number INTEGER, utm_zone_letter TEXT,
    latitude REAL, longitude REAL, coord_source TEXT,
    elevation REAL, total_depth INTEGER, slope_angle INTEGER,
    precip_rate TEXT, precip_type TEXT, sky_condition TEXT, wind TEXT,
    ground_condition TEXT, ground_roughness TEXT,
    vegetation TEXT, vegetation_height INTEGER, tree_canopy TEXT,
    snow_cover_condition TEXT, standing_water TEXT,
    wise_serial TEXT, gps_device TEXT, gps_uncertainty REAL, gps_uncertainty_unit TEXT, density_cutter TEXT,
    new_snow_depth INTEGER, new_snow_swe INTEGER, new_snow_density REAL,
    recorded_by INTEGER REFERENCES observers(id),
    comments TEXT, flags TEXT,
    raw_json TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);

CREATE TABLE IF NOT EXISTS site_observers(
    site_id INTEGER NOT NULL REFERENCES sites(id),
    observer_id INTEGER NOT NULL REFERENCES observers(id),
    PRIMARY KEY(site_id, observer_id));

CREATE TABLE IF NOT EXISTS layers(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id INTEGER NOT NULL REFERENCES sites(id),
    measurement_type_id INTEGER NOT NULL REFERENCES measurement_types(id),
    instrument_id INTEGER REFERENCES instruments(id),
    top_cm REAL, bottom_cm REAL, depth_from_surface REAL,
    value REAL, value_b REAL, value_c REAL, value_avg REAL,
    grain_size_min REAL, grain_size_max REAL, grain_size_avg REAL,
    grain_type TEXT, hand_hardness TEXT, snow_wetness TEXT,
    time_recorded TEXT, comments TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);

CREATE TABLE IF NOT EXISTS ssa_calibration(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id INTEGER NOT NULL REFERENCES sites(id),
    instrument_id INTEGER NOT NULL REFERENCES instruments(id),
    spectralon TEXT, calib_values TEXT,
    measured_at TEXT, operator TEXT, notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);

CREATE TABLE IF NOT EXISTS site_instruments(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id INTEGER NOT NULL REFERENCES sites(id),
    instrument_id INTEGER NOT NULL REFERENCES instruments(id),
    used INTEGER DEFAULT 1, notes TEXT);

INSERT OR IGNORE INTO measurement_types(name,units,derived) VALUES
    ('temperature','degC',0),('density','kg/m3',0),
    ('permittivity','unitless',0),('lwc','%',1),
    ('grain_size','mm',0),('hand_hardness','unitless',0),
    ('wetness','unitless',0),('swe','mm',0),('depth','cm',0),
    ('ssa','m2/kg',0);

INSERT OR IGNORE INTO instruments(name,model) VALUES
    ('IceCube','A2 Photonic IceCube'),
    ('IRIS2','IRIS2'),('IRIS','IRIS'),
    ('Snow Fork','Toikka Snow Fork'),
    ('SMP','SnowMicroPen'),
    ('Denoth','Denoth LWC Meter'),
    ('Federal Sampler','Standard Federal Sampler'),
    ('Lyte Probe','Lyte Probe'),
    ('Standard Ram','Standard Rammsonde');
"""

def init_db():
    conn = sqlite3.connect(DB_PATH)
    _guard_foreign_db(conn)
    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.commit()
    conn.close()

def _guard_foreign_db(conn):
    """Refuse to inject CryoPit tables into an unrelated existing database.

    empty/new file -> fine; existing CryoPit DB ('sites' present) -> fine;
    existing OTHER DB -> stop with a clear message.
    """
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    tables.discard("sqlite_sequence")
    if tables and "sites" not in tables:
        raise SystemExit(
            f"The database at '{DB_PATH}' already exists but does not look like a "
            f"CryoPit database (no 'sites' table found). To avoid modifying an "
            f"unrelated file, CryoPit will not initialize here. Point "
            f"CRYOPIT_DB_PATH at a new path or an existing CryoPit database."
        )

def _migrate(conn):
    """Add columns introduced after a DB was first created.

    CREATE TABLE IF NOT EXISTS won't alter an existing table, so new columns
    must be added explicitly. Each ADD COLUMN is wrapped — if the column
    already exists SQLite raises OperationalError, which we ignore. Idempotent.
    Existing rows get NULL for the new columns (so v2.0 pits have raw_json=NULL
    and can't be loaded for editing, only re-entered).
    """
    adds = [
        ("sites", "location TEXT"),
        ("sites", "gps_uncertainty REAL"),
        ("sites", "gps_uncertainty_unit TEXT"),
        ("sites", "raw_json TEXT"),
        ("ssa_calibration", "operator TEXT"),
    ]
    for table, coldef in adds:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {coldef}")
        except sqlite3.OperationalError:
            pass  # column already exists

def get_conn():
    """One connection per request.

    WAL mode: readers and the (single) writer no longer block each other —
    most "database is locked" errors in default journal mode come from that
    interaction. WAL does NOT parallelize writes; SQLite always serializes
    writers. busy_timeout is what handles two writers colliding: the second
    one waits up to 5 s for the lock instead of raising immediately. Our
    writes are millisecond-scale, so collisions resolve invisibly.
    journal_mode persists in the DB file but is cheap to (re)issue per
    connection; busy_timeout is per-connection and must be set every time.
    """
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

def save_pit(payload):
    """Persist one pit. Returns (status, info):
        ("ok", pit_id)      saved
        ("exists", pit_id)  pit_id already in DB and payload lacks overwrite:true
        ("error", message)  anything else
    Replacement is DELETE-then-INSERT inside one transaction, gated by the
    overwrite flag so the form can ask the user first.
    """
    conn = get_conn()
    try:
        m   = payload.get("meta") or {}
        pid = (m.get("pit_id") or "").strip()
        if not pid or pid == "—":
            return "error", "Missing pit_id"

        exists = conn.execute(
            "SELECT 1 FROM sites WHERE pit_id=?", (pid,)).fetchone()
        if exists and not payload.get("overwrite"):
            return "exists", pid

        # Round-trip payload: exactly what the form sent, minus transport-only
        # keys. This is what /api/load returns, so loading is lossless.
        raw = {k: v for k, v in payload.items()
               if k not in ("overwrite", "dest", "folder")}

        with conn:
            wx  = payload.get("weather", {}) or {}
            gnd = payload.get("ground", {}) or {}

            def get_or_create_observer(name):
                name = (name or "").strip()
                if not name: return None
                row = conn.execute("SELECT id FROM observers WHERE name=?", (name,)).fetchone()
                if row: return row[0]
                conn.execute("INSERT INTO observers(name) VALUES(?)", (name,))
                return conn.execute("SELECT last_insert_rowid()").fetchone()[0]

            recorded_by_id = get_or_create_observer(m.get("recorded_by",""))
            surveyor_ids   = [get_or_create_observer(s.strip())
                              for s in (m.get("surveyors","") or "").split(",") if s.strip()]

            camp_name = m.get("campaign") or CAMPAIGN
            camp_row  = conn.execute("SELECT id FROM campaigns WHERE name=?", (camp_name,)).fetchone()
            if camp_row:
                camp_id = camp_row[0]
            else:
                conn.execute("INSERT INTO campaigns(name) VALUES(?)", (camp_name,))
                camp_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

            conn.execute("DELETE FROM layers WHERE site_id=(SELECT id FROM sites WHERE pit_id=?)", (pid,))
            conn.execute("DELETE FROM site_observers WHERE site_id=(SELECT id FROM sites WHERE pit_id=?)", (pid,))
            conn.execute("DELETE FROM site_instruments WHERE site_id=(SELECT id FROM sites WHERE pit_id=?)", (pid,))
            conn.execute("DELETE FROM ssa_calibration WHERE site_id=(SELECT id FROM sites WHERE pit_id=?)", (pid,))
            conn.execute("DELETE FROM sites WHERE pit_id=?", (pid,))

            # total_depth may legitimately be None (not yet measured). Every
            # depth_from_surface derives through dfs() so missing inputs stay
            # NULL instead of becoming fake numbers.
            total_depth = m.get("total_depth")
            def dfs(h):
                if total_depth is None or h is None: return None
                return total_depth - h

            conn.execute("""INSERT INTO sites(
                campaign_id,pit_id,name,location,date,pit_open_time,
                temp_time_start,temp_time_end,
                utm_easting,utm_northing,utm_zone_number,utm_zone_letter,
                latitude,longitude,coord_source,elevation,
                total_depth,slope_angle,
                precip_rate,precip_type,sky_condition,wind,
                ground_condition,ground_roughness,vegetation,vegetation_height,
                tree_canopy,snow_cover_condition,standing_water,
                wise_serial,gps_device,gps_uncertainty,gps_uncertainty_unit,density_cutter,
                new_snow_depth,new_snow_swe,new_snow_density,
                recorded_by,comments,flags,raw_json)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (camp_id, pid, m.get("site",""), m.get("location",""), m.get("date",""),
                 m.get("pit_open_time",""), m.get("temp_time_start",""), m.get("temp_time_end",""),
                 m.get("utm_easting"), m.get("utm_northing"),
                 m.get("utm_zone_number"), m.get("utm_zone_letter",""),
                 m.get("latitude"), m.get("longitude"), m.get("coord_source","utm"),
                 m.get("elevation"), total_depth, m.get("slope_angle"),
                 wx.get("precip_rate",""), wx.get("precip_type",""),
                 wx.get("sky",""), wx.get("wind",""),
                 gnd.get("condition",""), gnd.get("roughness",""),
                 json.dumps(gnd.get("vegetation",[])), gnd.get("veg_height"),
                 gnd.get("canopy",""), gnd.get("snow_cover",""), gnd.get("standing_water",""),
                 m.get("wise_serial",""), m.get("gps_device",""),
                 m.get("gps_uncertainty"), m.get("gps_uncertainty_unit",""),
                 m.get("density_cutter",""),
                 gnd.get("new_depth"), gnd.get("new_swe"), gnd.get("new_density"),
                 recorded_by_id, m.get("comments",""), m.get("flags") or "None",
                 json.dumps(raw)))

            site_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

            for oid in surveyor_ids:
                if oid:
                    conn.execute("INSERT OR IGNORE INTO site_observers(site_id,observer_id) VALUES(?,?)",
                                 (site_id, oid))

            def mt_id(name):
                return conn.execute("SELECT id FROM measurement_types WHERE name=?", (name,)).fetchone()[0]

            def inst_id(name):
                row = conn.execute("SELECT id FROM instruments WHERE name=?", (name,)).fetchone()
                return row[0] if row else None

            # Temperature — store the profile start time on the first (surface)
            # row and end time on the last (ground) row; the section-level
            # start/end fields are the single source for these.
            tid = mt_id("temperature")
            trows = payload.get("temperature", [])
            t_start = m.get("temp_time_start","")
            t_end   = m.get("temp_time_end","")
            trows_sorted = sorted(
                trows,
                key=lambda r: -(r["height"] if r.get("height") is not None else -1e9))
            n_t = len(trows_sorted)
            for idx, r in enumerate(trows_sorted):
                tr_time = ""
                if idx == 0:        tr_time = t_start
                elif idx == n_t-1:  tr_time = t_end
                conn.execute("""INSERT INTO layers(site_id,measurement_type_id,
                    top_cm,depth_from_surface,value,time_recorded)
                    VALUES(?,?,?,?,?,?)""",
                    (site_id, tid, r.get("height"), dfs(r.get("height")),
                     r.get("temp"), tr_time))

            # Density — average over whichever of A/B/C are present (0 counts).
            did = mt_id("density")
            for r in payload.get("density", []):
                vals = [v for v in [r.get("a"), r.get("b"), r.get("c")] if v is not None]
                avg  = round(sum(vals)/len(vals)) if vals else None
                conn.execute("""INSERT INTO layers(site_id,measurement_type_id,
                    top_cm,bottom_cm,depth_from_surface,value,value_b,value_c,value_avg)
                    VALUES(?,?,?,?,?,?,?,?,?)""",
                    (site_id, did, r.get("top"), r.get("bottom"),
                     dfs(r.get("top")),
                     r.get("a"), r.get("b"), r.get("c"), avg))

            # LWC
            lid  = mt_id("permittivity")
            sfid = inst_id("Snow Fork")
            for r in payload.get("lwc", []):
                conn.execute("""INSERT INTO layers(site_id,measurement_type_id,instrument_id,
                    top_cm,bottom_cm,depth_from_surface,value,value_b)
                    VALUES(?,?,?,?,?,?,?,?)""",
                    (site_id, lid, sfid, r.get("top"), r.get("bottom"),
                     dfs(r.get("top")),
                     r.get("a"), r.get("b")))

            # Stratigraphy
            gid = mt_id("grain_size")
            for r in payload.get("stratigraphy", []):
                conn.execute("""INSERT INTO layers(site_id,measurement_type_id,
                    top_cm,bottom_cm,depth_from_surface,
                    grain_size_min,grain_size_max,grain_size_avg,
                    grain_type,hand_hardness,snow_wetness,comments)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (site_id, gid, r.get("top"), r.get("bottom"),
                     dfs(r.get("top")),
                     r.get("gmin"), r.get("gmax"), r.get("gavg"),
                     r.get("gtype",""), r.get("hardness",""),
                     r.get("wetness",""), r.get("comments","")))

            # Site instruments
            for inst_rec in payload.get("instruments", []):
                inst_r = conn.execute("SELECT id FROM instruments WHERE name=?",
                                      (inst_rec["name"],)).fetchone()
                if inst_r:
                    conn.execute("""INSERT INTO site_instruments(site_id,instrument_id,used,notes)
                        VALUES(?,?,?,?)""",
                        (site_id, inst_r[0],
                         1 if inst_rec.get("used")=="Y" else 0,
                         inst_rec.get("sn","—")))

            # SSA
            ssaid = mt_id("ssa")
            ssa_inst_name = (payload.get("ssa_calibration",{}) or {}).get("instrument") or "IceCube"
            iceid = inst_id(ssa_inst_name) or inst_id("IceCube")
            for r in payload.get("ssa", []):
                conn.execute("""INSERT INTO layers(site_id,measurement_type_id,instrument_id,
                    top_cm,depth_from_surface,value,value_b,value_c,grain_type,comments)
                    VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (site_id, ssaid, iceid,
                     r.get("height"), dfs(r.get("height")),
                     r.get("ssa"), r.get("reflectance"), r.get("signal"),
                     r.get("grain_type",""), r.get("comments","")))

            ssa_cal = payload.get("ssa_calibration", {}) or {}
            if ssa_cal.get("spectralon") or ssa_cal.get("calib_values") or ssa_cal.get("operator"):
                conn.execute("""INSERT INTO ssa_calibration(
                    site_id,instrument_id,spectralon,calib_values,measured_at,operator,notes)
                    VALUES(?,?,?,?,?,?,?)""",
                    (site_id, iceid,
                     json.dumps(ssa_cal.get("spectralon",[])),
                     json.dumps(ssa_cal.get("calib_values",[])),
                     ssa_cal.get("measured_at",""),
                     ssa_cal.get("operator",""), ssa_cal.get("notes","")))

        return "ok", pid
    except Exception as e:
        return "error", str(e)
    finally:
        conn.close()

def load_pit(pit_id):
    """Return (payload, None) for a pit, or (None, reason)."""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT raw_json FROM sites WHERE pit_id=?", (pit_id,)).fetchone()
        if not row:
            return None, "Pit not found"
        if not row[0]:
            return None, ("This pit was saved by an older CryoPit version and "
                          "has no stored payload — it can't be loaded for editing.")
        return json.loads(row[0]), None
    except Exception as e:
        return None, str(e)
    finally:
        conn.close()

def list_pits(limit=50):
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT pit_id, date FROM sites ORDER BY created_at DESC LIMIT ?",
            (limit,)).fetchall()
        return [{"pit_id": r[0], "date": r[1]} for r in rows]
    except Exception:
        return []
    finally:
        conn.close()

# -----------------------------------------------------------------------------
# CSV EXPORT — SnowEx format
# -----------------------------------------------------------------------------
def _c(v):
    if v is None: return NO_DATA
    if isinstance(v, float) and math.isnan(v): return NO_DATA
    if isinstance(v, str) and v.strip() == "": return NO_DATA
    # Render whole-number floats without a trailing ".0" so that a value read
    # back from the DB as REAL (e.g. 759039.0) matches the same value taken
    # straight from the form payload as int (759039). Keeps Download (payload
    # export) and Archive (DB export) byte-identical.
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v

def _hdr(p, extra=None):
    rows = [
        ["# Location",                  _c(p.get("location"))],
        ["# Site",                      _c(p.get("name"))],
        ["# PitID",                     _c(p.get("pit_id"))],
        ["# Date/Local Standard Time",  str(p.get("date",""))+"T"+str(p.get("pit_open_time",""))],
        ["# UTM Zone",                  str(p.get("utm_zone_number") or "")+str(p.get("utm_zone_letter") or "")],
        ["# Easting",                   _c(p.get("utm_easting"))],
        ["# Northing",                  _c(p.get("utm_northing"))],
        ["# Latitude",                  _c(p.get("latitude"))],
        ["# Longitude",                 _c(p.get("longitude"))],
        ["# Coordinate Datum",          "WGS84"],
        ["# Flags",                     _c(p.get("flags","None"))],
        ["# Pit Comments",              _c(p.get("comments"))],
    ]
    if extra:
        rows.extend(extra)
    return rows

def _csv(rows):
    buf = io.StringIO()
    csvlib.writer(buf).writerows(rows)
    return buf.getvalue()

def _fname(pit_id, date_str, param, campaign=None):
    return f"{campaign or CAMPAIGN}_{pit_id}_{date_str}_{param}_v01_0.csv"

def _build_csvs(p, layers, obs_str, ssa_cal, inst_list, campaign):
    """Build the six CSV strings from already-normalized plain data.

    Single source of truth for CSV formatting. Both paths feed it:
      export_all(pit_id)        — reads the DB, then calls this
      export_from_payload(...)  — transforms a collect() payload, then calls this

    Args:
      p         : dict of site fields (top_cm/value naming already applied to layers)
      layers    : dict {measurement_type_name: [layer_dict, ...]} where each layer
                  has keys top_cm, bottom_cm, value, value_b, value_c, value_avg,
                  grain_size_min/max, grain_type, hand_hardness, snow_wetness,
                  time_recorded, comments (missing keys default to None)
      obs_str   : observers as a display string
      ssa_cal   : dict with instrument, operator, spectralon[], calib_values[], measured_at
      inst_list : [(name, serial, used_int), ...]
      campaign  : campaign name for filenames
    """
    pit_id   = p.get("pit_id")
    date_str = (p.get("date") or "00000000").replace("-","")
    def L(name): return layers.get(name, [])
    def g(d, k): return d.get(k)

    # -- siteDetails -----------------------------------------------
    veg = p.get("vegetation") or []
    if isinstance(veg, str):
        veg = json.loads(veg or "[]")
    sd = _hdr(p) + [
        ["# HS (cm)",                   _c(p.get("total_depth"))],
        ["# Observers",                 obs_str or NO_DATA],
        ["# WISe Serial No",            _c(p.get("wise_serial"))],
        ["# GPS",                       _c(p.get("gps_device"))],
        ["# GPS Uncertainty",           (str(_c(p.get("gps_uncertainty")))+" "+(p.get("gps_uncertainty_unit") or "")).strip() if p.get("gps_uncertainty") is not None else NO_DATA],
        ["# Density Cutter/Instrument", _c(p.get("density_cutter"))],
        ["# Snow Cover Condition",      _c(p.get("snow_cover_condition"))],
        ["# Standing Water Present",    _c(p.get("standing_water"))],
        ["# Precip Type",               _c(p.get("precip_type"))],
        ["# Precip Rate",               _c(p.get("precip_rate"))],
        ["# Sky",                       _c(p.get("sky_condition"))],
        ["# Wind",                      _c(p.get("wind"))],
        ["# Ground Condition",          _c(p.get("ground_condition"))],
        ["# Ground Roughness",          _c(p.get("ground_roughness"))],
        ["# Ground Vegetation/Cover",   ", ".join(veg) if veg else NO_DATA],
        ["# Vegetation Height (cm)",    _c(p.get("vegetation_height"))],
        ["# Tree Canopy",               _c(p.get("tree_canopy"))],
    ]

    # -- density ---------------------------------------------------
    dens_data = L("density")
    dens = _hdr(p) + [["# Top (cm)","Bottom (cm)","Density A (kg/m3)","Density B (kg/m3)","Density C (kg/m3)"]]
    for d in dens_data:
        dens.append([_c(g(d,"top_cm")),_c(g(d,"bottom_cm")),
                     _c(g(d,"value")),_c(g(d,"value_b")),_c(g(d,"value_c"))])

    # -- temperature -----------------------------------------------
    temp_data = sorted(
        L("temperature"),
        key=lambda d: -(d["top_cm"] if d.get("top_cm") is not None else -1e9))
    temp = _hdr(p) + [["# Depth (cm)","Temperature (deg C)","Time start/end"]]
    for i,d in enumerate(temp_data):
        t_val = _c(g(d,"time_recorded"))
        if 0 < i < len(temp_data)-1: t_val = NO_DATA
        temp.append([_c(g(d,"top_cm")),_c(g(d,"value")),t_val])

    # -- LWC -------------------------------------------------------
    lwc_data = L("permittivity")
    def _k(v): return None if v is None else round(float(v), 1)
    dens_map = {_k(g(d,"top_cm")): g(d,"value_avg") for d in dens_data}
    lwc = _hdr(p) + [["# Top (cm)","Bottom (cm)","Avg Density (kg/m3)","Permittivity A","Permittivity B","LWC-vol A (%)","LWC-vol B (%)"]]
    for d in lwc_data:
        lwc.append([_c(g(d,"top_cm")),_c(g(d,"bottom_cm")),
                    _c(dens_map.get(_k(g(d,"top_cm")))),
                    _c(g(d,"value")),_c(g(d,"value_b")),NO_DATA,NO_DATA])

    # -- stratigraphy ----------------------------------------------
    strat_data = L("grain_size")
    grain_codes = ("Grain Type (IACS): PP=Precipitation Particles, "
                   "PPsd=Stellars/Dendrites, PPgp=Graupel, PPrm=Rimed Particles, "
                   "MM=Machine Made, DF=Decomposing/Fragmented, "
                   "RG=Rounded Grains, RGwp=Wind Packed, RGxf=Faceted Rounded, RGlr=Large Rounded, "
                   "FC=Faceted Crystals, FCsf=Near-surface Facets, FCxr=Rounding Facets, FCso=Solid Facets, "
                   "DH=Depth Hoar, DHcp=Hollow Cups, DHpr=Hollow Prisms, DHla=Large Striated, DHxr=Rounding Depth Hoar, "
                   "SH=Surface Hoar, SHxr=Rounding Surface Hoar, "
                   "MF=Melt Forms, MFcl=Clustered Rounded, MFsl=Slush, MFcr=Melt-Freeze Crust, "
                   "IF=Ice Formations, IFsc=Sun Crust, IFrc=Rain Crust, IFbi=Basal Ice; "
                   "Hand Hardness: F=Fist, 4F=4-finger, 1F=1-finger, P=Pencil, K=Knife, I=Ice; "
                   "Manual Wetness: D=Dry, M=Moist, W=Wet, V=Very Wet, S=Soaked")
    strat = _hdr(p,[["# Parameter Codes",grain_codes]]) + \
        [["# Top (cm)","Bottom (cm)","Grain Size (mm)","Grain Type","Hand Hardness","Manual Wetness","Comments"]]
    for d in strat_data:
        mn,mx = _c(g(d,"grain_size_min")),_c(g(d,"grain_size_max"))
        gs = f"{mn}-{mx} mm" if mn!=NO_DATA and mx!=NO_DATA else NO_DATA
        strat.append([_c(g(d,"top_cm")),_c(g(d,"bottom_cm")),
                      gs,_c(g(d,"grain_type")),_c(g(d,"hand_hardness")),
                      _c(g(d,"snow_wetness")),_c(g(d,"comments"))])

    # -- SSA -------------------------------------------------------
    ssa_data = L("ssa")
    ssa_cal = ssa_cal or {}
    ssa_extra = [["# Instrument", ssa_cal.get("instrument") or "IceCube"]]
    if ssa_cal.get("operator"):     ssa_extra.append(["# SSA Operator", ssa_cal["operator"]])
    if ssa_cal.get("spectralon"):   ssa_extra.append(["# Spectralon"] + list(ssa_cal["spectralon"]))
    if ssa_cal.get("calib_values"): ssa_extra.append(["# Calibration Values (V)"] + list(ssa_cal["calib_values"]))
    if ssa_cal.get("measured_at"):  ssa_extra.append(["# Timing", ssa_cal["measured_at"]])
    ssa = _hdr(p, ssa_extra) + \
        [["# Sample_signal(V)","Reflectance(%)","SSA(m2 kg-1)","Sample_top_height(cm)","Grain type","Comments"]]
    for d in ssa_data:
        ssa.append([_c(g(d,"value_c")),_c(g(d,"value_b")),
                    _c(g(d,"value")),_c(g(d,"top_cm")),
                    _c(g(d,"grain_type")),_c(g(d,"comments"))])

    # Add instruments to siteDetails
    if inst_list:
        sd.append([])
        sd.append(["# INSTRUMENTS"])
        sd.append(["# Instrument","Serial No.","Used"])
        for name, serial, used in inst_list:
            sd.append([name, serial or "—", "Y" if used==1 else "N"])

    return {
        _fname(pit_id,date_str,"siteDetails",campaign):  _csv(sd),
        _fname(pit_id,date_str,"density",campaign):      _csv(dens),
        _fname(pit_id,date_str,"temperature",campaign):  _csv(temp),
        _fname(pit_id,date_str,"LWC",campaign):          _csv(lwc),
        _fname(pit_id,date_str,"stratigraphy",campaign): _csv(strat),
        _fname(pit_id,date_str,"SSA",campaign):          _csv(ssa),
    }

def export_all(pit_id):
    """Build CSVs by reading a saved pit from the database (used by Archive)."""
    conn = get_conn()
    p_row = conn.execute("SELECT * FROM sites WHERE pit_id=?", (pit_id,)).fetchone()
    if not p_row:
        conn.close()
        return {}
    cols = [d[0] for d in conn.execute("SELECT * FROM sites WHERE pit_id=?", (pit_id,)).description]
    p    = dict(zip(cols, p_row))
    crow = conn.execute("SELECT name FROM campaigns WHERE id=?", (p.get("campaign_id"),)).fetchone()
    campaign = (crow[0] if crow and crow[0] else CAMPAIGN)

    def mt(name):
        r = conn.execute("SELECT id FROM measurement_types WHERE name=?", (name,)).fetchone()
        return r[0] if r else None

    def lrows(mt_id):
        rows = conn.execute(
            "SELECT * FROM layers WHERE site_id=? AND measurement_type_id=? ORDER BY depth_from_surface",
            (p["id"], mt_id)).fetchall()
        lc = [d[0] for d in conn.execute("SELECT * FROM layers LIMIT 0").description]
        return [dict(zip(lc,r)) for r in rows]

    layers = {
        "density":      lrows(mt("density")),
        "temperature":  lrows(mt("temperature")),
        "permittivity": lrows(mt("permittivity")),
        "grain_size":   lrows(mt("grain_size")),
        "ssa":          lrows(mt("ssa")),
    }

    obs = conn.execute("""SELECT o.name FROM observers o
        JOIN site_observers so ON so.observer_id=o.id WHERE so.site_id=?""",
        (p["id"],)).fetchall()
    obs_str = ", ".join([r[0] for r in obs])

    cal_row = conn.execute(
        "SELECT spectralon,calib_values,measured_at,operator FROM ssa_calibration WHERE site_id=? LIMIT 1",
        (p["id"],)).fetchone()
    inst_row = conn.execute(
        "SELECT i.name FROM instruments i JOIN ssa_calibration sc ON sc.instrument_id=i.id WHERE sc.site_id=? LIMIT 1",
        (p["id"],)).fetchone()
    ssa_cal = {"instrument": inst_row[0] if inst_row else None}
    if cal_row:
        ssa_cal["spectralon"]   = json.loads(cal_row[0] or "[]")
        ssa_cal["calib_values"] = json.loads(cal_row[1] or "[]")
        ssa_cal["measured_at"]  = cal_row[2]
        ssa_cal["operator"]     = cal_row[3]

    inst_rows = conn.execute("""
        SELECT i.name, si.notes, si.used
        FROM site_instruments si
        JOIN instruments i ON i.id=si.instrument_id
        WHERE si.site_id=?""", (p["id"],)).fetchall()
    inst_list = [(r[0], r[1], r[2]) for r in inst_rows]

    conn.close()
    return _build_csvs(p, layers, obs_str, ssa_cal, inst_list, campaign)

def export_from_payload(payload):
    """Build CSVs directly from a collect() payload — NO database read or write.

    Used by Download, so a user can get CSVs without persisting the pit. Mirrors
    the same normalization save_pit() applies (height->top_cm, A/B/C->value/
    value_b/value_c, temperature start/end on first/last rows) so the output is
    identical to exporting the same pit after archiving it.
    """
    m   = payload.get("meta", {}) or {}
    wx  = payload.get("weather", {}) or {}
    gnd = payload.get("ground", {}) or {}
    campaign = m.get("campaign") or CAMPAIGN

    # site-field dict in the same shape _build_csvs expects (DB column names)
    p = {
        "pit_id": m.get("pit_id"), "name": m.get("site"), "location": m.get("location"),
        "date": m.get("date"), "pit_open_time": m.get("pit_open_time"),
        "utm_zone_number": m.get("utm_zone_number"), "utm_zone_letter": m.get("utm_zone_letter"),
        "utm_easting": m.get("utm_easting"), "utm_northing": m.get("utm_northing"),
        "latitude": m.get("latitude"), "longitude": m.get("longitude"),
        "total_depth": m.get("total_depth"), "wise_serial": m.get("wise_serial"),
        "gps_device": m.get("gps_device"), "gps_uncertainty": m.get("gps_uncertainty"),
        "gps_uncertainty_unit": m.get("gps_uncertainty_unit"),
        "density_cutter": m.get("density_cutter"),
        "snow_cover_condition": gnd.get("snow_cover"), "standing_water": gnd.get("standing_water"),
        "precip_type": wx.get("precip_type"), "precip_rate": wx.get("precip_rate"),
        "sky_condition": wx.get("sky"), "wind": wx.get("wind"),
        "ground_condition": gnd.get("condition"), "ground_roughness": gnd.get("roughness"),
        "vegetation": gnd.get("vegetation", []), "vegetation_height": gnd.get("veg_height"),
        "tree_canopy": gnd.get("canopy"),
        "flags": m.get("flags", "None"), "comments": m.get("comments"),
    }

    # temperature: same surface-first + start/end-on-first/last as save_pit
    trows = payload.get("temperature", []) or []
    t_start, t_end = m.get("temp_time_start",""), m.get("temp_time_end","")
    trows_sorted = sorted(trows, key=lambda r: -(r["height"] if r.get("height") is not None else -1e9))
    n_t = len(trows_sorted)
    temp_layers = []
    for idx, r in enumerate(trows_sorted):
        tr_time = t_start if idx==0 else (t_end if idx==n_t-1 else "")
        temp_layers.append({"top_cm": r.get("height"), "value": r.get("temp"), "time_recorded": tr_time})

    # density: A/B/C -> value/value_b/value_c, with value_avg for LWC lookup
    dens_layers = []
    for r in payload.get("density", []) or []:
        vals = [v for v in [r.get("a"), r.get("b"), r.get("c")] if v is not None]
        avg  = round(sum(vals)/len(vals)) if vals else None
        dens_layers.append({"top_cm": r.get("top"), "bottom_cm": r.get("bottom"),
                            "value": r.get("a"), "value_b": r.get("b"), "value_c": r.get("c"),
                            "value_avg": avg})

    lwc_layers = [{"top_cm": r.get("top"), "bottom_cm": r.get("bottom"),
                   "value": r.get("a"), "value_b": r.get("b")}
                  for r in (payload.get("lwc", []) or [])]

    strat_layers = [{"top_cm": r.get("top"), "bottom_cm": r.get("bottom"),
                     "grain_size_min": r.get("gmin"), "grain_size_max": r.get("gmax"),
                     "grain_type": r.get("gtype"), "hand_hardness": r.get("hardness"),
                     "snow_wetness": r.get("wetness"), "comments": r.get("comments")}
                    for r in (payload.get("stratigraphy", []) or [])]

    ssa_layers = [{"top_cm": r.get("height"), "value": r.get("ssa"),
                   "value_b": r.get("reflectance"), "value_c": r.get("signal"),
                   "grain_type": r.get("grain_type"), "comments": r.get("comments")}
                  for r in (payload.get("ssa", []) or [])]

    layers = {"density": dens_layers, "temperature": temp_layers,
              "permittivity": lwc_layers, "grain_size": strat_layers, "ssa": ssa_layers}

    obs_str = m.get("surveyors") or m.get("recorded_by") or ""
    ssa_cal = payload.get("ssa_calibration", {}) or {}
    inst_list = [(it.get("name"), it.get("sn"), 1 if it.get("used")=="Y" else 0)
                 for it in (payload.get("instruments", []) or [])]

    return _build_csvs(p, layers, obs_str, ssa_cal, inst_list, campaign)

# -----------------------------------------------------------------------------
# EXPORT DESTINATIONS
#
# export_all() produces {filename: csv_string}. Everything below is a "sink":
# a function that takes that dict and puts it somewhere. Adding Drive / S3 / a
# repo means writing another sink, never touching the export logic above.
# Browser-download is the third sink and lives in the form's JS.
# -----------------------------------------------------------------------------
def zip_csvs(files, pit_id, campaign=None):
    """Bundle {filename: content} into a single ZIP, returned as (zipname, b64).

    Built entirely in memory so it can be delivered without writing to disk —
    the browser decodes the base64 and saves one file instead of six.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for fname, content in files.items():
            if content:
                zf.writestr(fname, content)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    safe = (pit_id or "pit").replace("/", "_").replace("\\", "_")
    return f"{campaign or CAMPAIGN}_{safe}.zip", b64

def save_csvs_to_folder(files, folder):
    """Write {filename: content} into `folder` on the machine running Python.

    NOTE: `folder` is resolved by THIS process. Locally that's the user's own
    machine; deployed, it's the server. Returns (ok, info) where info is
    {"folder": abs_path, "count": n} on success or an error message on failure.
    (Restored in v3.0 — the v2.0 file was missing this function's `def` line,
    so the Folder export crashed with NameError.)
    """
    if not folder or not str(folder).strip():
        folder = EXPORT_DIR
    folder = os.path.abspath(os.path.expanduser(str(folder).strip()))
    try:
        os.makedirs(folder, exist_ok=True)
        if not os.path.isdir(folder):
            return False, f"Not a directory: {folder}"
        if not os.access(folder, os.W_OK):
            return False, f"No write permission: {folder}"
        written = []
        for fname, content in files.items():
            if not content:
                continue
            fpath = os.path.join(folder, fname)
            with open(fpath, "w", newline="") as fh:
                fh.write(content)
            written.append(fname)
        return True, {"folder": folder, "count": len(written)}
    except PermissionError as e:
        return False, f"Permission denied: {e}"
    except OSError as e:
        return False, f"Could not write to {folder}: {e}"

# -----------------------------------------------------------------------------
# HTML FORM
# -----------------------------------------------------------------------------
FORM = r"""<!DOCTYPE html>
<html lang="en" data-theme="light">
<head>
<meta charset="UTF-8">
<title>__PAGE_TITLE__</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@300;400;500;700&family=IBM+Plex+Mono:wght@300;400;500&display=swap" rel="stylesheet">
<style>
/* ============================================================
   CryoPit v3 — "glaciology instrument"
   Polar-night bar + aurora hairline · frost-paper surfaces ·
   Space Grotesk UI / IBM Plex Mono data · live core rail
   ============================================================ */
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --w:   #fbfdfe; --bg:  #edf2f7; --ink: #0d1b2a;
  --ink2:#46586c; --ink3:#8fa1b3; --acc: #155efc;
  --acc2:#22b8cf;
  --red: #d8323c; --grn: #15835a;
  --rule:#dce5ee; --rule2:#c3d0dd;
  --sans:'Space Grotesk',sans-serif;
  --mono:'IBM Plex Mono',monospace;
  --nav: 54px; --r:4px;
}
html[data-theme="dark"]{
  --w:   #141e2b; --bg:  #0d1520; --ink: #e8eef6;
  --ink2:#9fb0c2; --ink3:#5b6b7d; --acc: #5b95ff;
  --acc2:#39c4dd;
  --red: #ff7077; --grn: #3ad29a;
  --rule:#243245; --rule2:#33445a;
}
html,body{height:100%;background:var(--w);font-family:var(--sans);color:var(--ink);font-size:14px;overflow:hidden}
::selection{background:var(--acc);color:#fff}

/* TOP BAR — polar night, aurora hairline */
.topbar{
  position:fixed;top:0;left:0;right:0;z-index:200;height:var(--nav);
  background:#0b1626;display:flex;align-items:center;
  padding:0 28px;
}
.topbar::after{content:'';position:absolute;left:0;right:0;bottom:0;height:2px;
  background:linear-gradient(90deg,var(--acc) 0%,var(--acc2) 45%,rgba(34,184,207,0) 85%)}
.tb-brand{display:flex;align-items:center;gap:10px;margin-right:18px}
.tb-wordmark{font-size:16px;font-weight:300;color:#fff;letter-spacing:-.01em}
.tb-wordmark strong{font-weight:700;color:#fff}
.tb-divider{color:rgba(255,255,255,.18);font-size:16px;font-weight:200;margin:0 2px}
.tb-inst{font-family:var(--mono);font-size:9px;letter-spacing:.18em;text-transform:uppercase;color:rgba(255,255,255,.38)}
.tb-pitid{font-family:var(--mono);font-size:12px;color:#dce9ff;margin-left:16px;letter-spacing:.05em;padding:4px 12px;background:rgba(91,149,255,.12);border:1px solid rgba(91,149,255,.28);border-radius:999px;white-space:nowrap;max-width:240px;overflow:hidden;text-overflow:ellipsis}
.tb-right{display:flex;align-items:center;gap:8px;margin-left:auto}
.tb-pct{font-family:var(--mono);font-size:11px;color:#fff;opacity:.45;margin-right:2px}
.tb-prog{width:64px;height:2px;background:rgba(255,255,255,.14);border-radius:1px;overflow:hidden}
.tb-fill{height:100%;background:linear-gradient(90deg,var(--acc),var(--acc2));border-radius:1px;transition:width .3s ease}
.tb-save{padding:7px 18px;background:var(--acc);color:#fff;border:none;border-radius:999px;font-size:12px;font-weight:700;letter-spacing:.02em;cursor:pointer;font-family:var(--sans);transition:filter .12s;margin-left:8px}
.tb-save:hover{filter:brightness(1.15)}
.tb-export{position:relative;display:flex;align-items:center;gap:6px}
.tb-csv{padding:6px 14px;background:transparent;color:#cfe0f5;border:1px solid rgba(255,255,255,.22);border-radius:999px;font-size:12px;cursor:pointer;font-family:var(--sans);transition:all .12s}
.tb-csv:hover{border-color:var(--acc2);color:#fff}
.tb-dest{padding:5px 10px;background:rgba(255,255,255,.06);color:#cfe0f5;border:1px solid rgba(255,255,255,.22);border-radius:999px;font-size:12px;cursor:pointer;font-family:var(--sans);outline:none}
.tb-dest option{background:#0b1626;color:#fff}
.tb-folder-pop{position:absolute;top:calc(100% + 10px);right:0;z-index:210;display:flex;flex-direction:column;gap:5px;padding:11px 13px;background:#0b1626;border:1px solid rgba(91,149,255,.3);border-radius:8px;box-shadow:0 10px 32px rgba(4,10,22,.5);min-width:290px}
.tb-folder-lbl{font-family:var(--mono);font-size:9px;letter-spacing:.1em;text-transform:uppercase;color:rgba(255,255,255,.45)}
.tb-folder{padding:6px 10px;background:rgba(255,255,255,.06);color:#fff;border:1px solid rgba(255,255,255,.22);border-radius:6px;font-size:12px;font-family:var(--mono);outline:none;width:100%;letter-spacing:.02em}
.tb-folder::placeholder{color:rgba(255,255,255,.32)}
.tb-folder:focus{border-color:var(--acc2)}
.tb-status{font-family:var(--mono);font-size:10px;color:#fff;opacity:.35;margin-left:4px;min-width:84px;letter-spacing:.02em}
.tb-status.ok{color:#3ad29a;opacity:.95}
.tb-status.ok-dl{color:#5b95ff;opacity:.95}
.tb-status.unsaved{color:#f0b35a;opacity:.85}
.tb-status.err{color:#ff7077;opacity:.95}
.tb-theme{background:transparent;border:1px solid rgba(255,255,255,.18);border-radius:999px;color:#fff;opacity:.55;cursor:pointer;font-size:13px;padding:4px 9px;transition:all .12s;margin-left:4px}
.tb-theme:hover{opacity:.95;border-color:var(--acc2)}

/* SHELL */
.shell{display:flex;height:calc(100vh - var(--nav));margin-top:var(--nav);overflow:hidden}

/* INDEX */
.index{width:192px;min-width:192px;background:var(--bg);border-right:1px solid var(--rule);height:100%;overflow-y:auto;flex-shrink:0;display:flex;flex-direction:column}
.idx-item{display:flex;align-items:center;padding:11px 18px;cursor:pointer;border-bottom:1px solid var(--rule);gap:10px;transition:background .12s;user-select:none;position:relative}
.idx-item:hover{background:var(--rule)}
.idx-item.active{background:var(--w)}
.idx-item.active::before{content:'';position:absolute;left:0;top:0;bottom:0;width:2px;background:linear-gradient(var(--acc),var(--acc2))}
.idx-num{font-family:var(--mono);font-size:10px;color:var(--ink3);min-width:18px}
.idx-lbl{font-size:12px;color:var(--ink2);font-weight:500;letter-spacing:.01em}
.idx-item.active .idx-lbl{color:var(--ink)}
.idx-pip{width:6px;height:6px;border-radius:50%;border:1.5px solid var(--rule2);flex-shrink:0;margin-left:auto;transition:all .2s}
.idx-pip.done{background:var(--grn);border-color:var(--grn)}
.nav-foot{border-top:1px solid var(--rule);padding:10px 0;margin-top:auto}
.nav-foot-label{font-family:var(--mono);font-size:9px;letter-spacing:.14em;text-transform:uppercase;color:var(--ink3);padding:8px 18px 5px}
.nav-foot-empty{font-family:var(--mono);font-size:11px;color:var(--ink3);padding:4px 18px;display:block}
.pit-entry{display:block;padding:6px 18px;font-family:var(--mono);font-size:11px;color:var(--ink2);border-bottom:1px solid var(--rule);cursor:pointer;transition:all .12s;text-decoration:none;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;border-left:2px solid transparent}
.pit-entry:hover{background:var(--rule);color:var(--ink);border-left-color:var(--acc2)}
.pit-entry .pit-date{font-size:10px;color:var(--ink3);display:block;margin-top:1px}

/* MAIN */
.main{flex:1;min-width:0;height:100%;overflow-y:auto;scroll-behavior:smooth}

/* SECTION — sticky headers with ghost numerals */
.sec{border-bottom:1px solid var(--rule)}
.sec-hd{display:flex;align-items:baseline;gap:14px;padding:15px 36px 12px;border-bottom:1px solid var(--rule);background:var(--bg);position:sticky;top:0;z-index:20}
.sec-num{font-family:var(--mono);font-size:21px;font-weight:300;color:var(--ink3);opacity:.5;letter-spacing:.02em;min-width:34px;line-height:1}
.sec-title{font-size:13px;font-weight:700;letter-spacing:.09em;text-transform:uppercase}
.sec-meta{font-family:var(--mono);font-size:11px;color:var(--ink3);margin-left:auto}
.sec-body{padding:24px 36px}

/* FIELD ROWS */
.row{display:flex;border:1px solid var(--rule);border-radius:var(--r);overflow:hidden;margin-bottom:14px;background:var(--w)}
.ri{flex:1;border-right:1px solid var(--rule);display:flex;flex-direction:column}
.ri:last-child{border-right:none}
.rl{font-family:var(--mono);font-size:9px;letter-spacing:.12em;text-transform:uppercase;color:var(--ink3);background:var(--bg);padding:5px 12px;border-bottom:1px solid var(--rule);display:flex;align-items:center;gap:4px}
.req{color:var(--red);font-size:10px}
.ri input,.ri select,.ri textarea{font-family:var(--sans);font-size:13px;color:var(--ink);border:none;background:var(--w);padding:8px 12px;outline:none;width:100%}
.ri input:focus,.ri select:focus,.ri textarea:focus{background:var(--bg);box-shadow:inset 2px 0 0 var(--acc)}
.ri input::placeholder,.ri textarea::placeholder{color:var(--ink3)}
.ri textarea{resize:vertical;line-height:1.6;min-height:64px}
.ri select{cursor:pointer;appearance:none;background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='10' viewBox='0 0 10 10'%3E%3Cpath d='M1 3l4 4 4-4' stroke='%238fa1b3' stroke-width='1.5' fill='none'/%3E%3C/svg%3E");background-repeat:no-repeat;background-position:right 10px center;padding-right:26px}
.pitid{font-family:var(--mono);font-size:13px;color:var(--ink);padding:8px 12px;cursor:text;outline:none;background:var(--w);letter-spacing:.05em}
.pitid:focus{background:var(--bg);box-shadow:inset 2px 0 0 var(--acc)}
.hint{font-family:var(--mono);font-size:9px;color:var(--ink3);padding:3px 12px 5px;letter-spacing:.04em}
.coord-note{font-family:var(--mono);font-size:9px;color:var(--grn);padding:2px 12px 5px}
.coord-or{font-family:var(--mono);font-size:10px;color:var(--ink3);text-align:center;padding:5px 0;letter-spacing:.12em;width:50%}

/* TOGGLES — pills */
.toggles{display:flex;flex-wrap:wrap;gap:5px;padding:9px 12px}
.tog{display:inline-flex;align-items:center;padding:4px 12px;border:1px solid var(--rule2);border-radius:999px;font-size:12px;color:var(--ink2);cursor:pointer;transition:all .1s;user-select:none;font-family:var(--sans)}
.tog:hover{border-color:var(--acc);color:var(--acc)}
.tog.on{background:var(--acc);color:#fff;border-color:var(--acc);font-weight:700}
.tog input{display:none}

/* TABLES */
.pw{border:1px solid var(--rule);border-radius:var(--r);overflow:hidden;margin-bottom:8px;background:var(--w)}
.pt{width:100%;border-collapse:collapse}
.pt thead tr{background:var(--bg)}
.pt th{padding:7px 12px;text-align:left;font-family:var(--mono);font-size:9px;color:var(--ink3);letter-spacing:.1em;text-transform:uppercase;font-weight:400;border-bottom:1px solid var(--rule);white-space:nowrap}
.pt td{border-bottom:1px solid var(--rule)}
.pt tr:last-child td{border-bottom:none}
.pt tbody tr:hover td{background:color-mix(in srgb,var(--bg) 55%,var(--w))}
.pt td input,.pt td select{border:none;background:transparent;padding:7px 12px;font-size:13px;font-family:var(--mono);color:var(--ink);width:100%;outline:none}
.pt td input:focus,.pt td select:focus{background:var(--bg);box-shadow:inset 2px 0 0 var(--acc)}
.avg input{color:var(--ink2);font-style:italic}
.del{width:26px;height:26px;border:none;background:transparent;color:var(--ink3);cursor:pointer;font-size:14px;display:flex;align-items:center;justify-content:center;margin:4px;border-radius:999px;transition:all .12s}
.del:hover{background:rgba(216,50,60,.12);color:var(--red)}
.add{width:100%;border:none;background:var(--bg);padding:7px 16px;font-size:12px;font-family:var(--mono);color:var(--ink3);cursor:pointer;text-align:left;border-top:1px solid var(--rule);letter-spacing:.04em;transition:all .12s}
.add:hover{background:var(--rule);color:var(--acc)}

/* INSTRUMENTS */
.ig-lbl{font-family:var(--mono);font-size:9px;letter-spacing:.14em;text-transform:uppercase;color:var(--ink3);padding:14px 0 6px}
.it{width:100%;border-collapse:collapse;border:1px solid var(--rule);border-radius:var(--r);overflow:hidden;margin-bottom:8px;background:var(--w)}
.it th{padding:7px 12px;text-align:left;font-family:var(--mono);font-size:9px;color:var(--ink3);letter-spacing:.1em;text-transform:uppercase;font-weight:400;border-bottom:1px solid var(--rule);background:var(--bg)}
.it td{padding:8px 12px;border-bottom:1px solid var(--rule);font-size:12px;vertical-align:middle}
.it tr:last-child td{border-bottom:none}
.sn{font-family:var(--mono);font-size:12px;border:1px solid var(--rule);border-radius:6px;padding:3px 9px;background:var(--bg);color:var(--ink);width:110px;outline:none}
.sn:focus{border-color:var(--acc)}
.yn{display:flex;border:1px solid var(--rule);border-radius:999px;overflow:hidden;width:max-content}
.yn button{padding:3px 11px;font-size:11px;font-family:var(--mono);background:transparent;border:none;cursor:pointer;color:var(--ink3);transition:all .12s}
.yn button.y.on{background:var(--grn);color:#fff;font-weight:600}
.yn button.n.on{background:var(--bg);color:var(--ink);font-weight:600}

/* CHECKLIST */
.cl-sum{display:flex;align-items:center;gap:16px;padding:14px 18px;background:var(--bg);border:1px solid var(--rule);border-radius:var(--r);margin-bottom:18px}
.cl-pct{font-family:var(--mono);font-size:32px;font-weight:300;color:var(--ink);min-width:62px}
.cl-bw{flex:1}
.cl-bt{height:2px;background:var(--rule);border-radius:1px;margin-bottom:5px;overflow:hidden}
.cl-bf{height:100%;background:linear-gradient(90deg,var(--grn),var(--acc2));border-radius:1px;transition:width .35s}
.cl-bl{font-family:var(--mono);font-size:11px;color:var(--ink3)}
.ci{display:flex;align-items:center;gap:10px;padding:9px 0;border-bottom:1px solid var(--rule);font-size:12px}
.ci:last-child{border-bottom:none}
.cd{width:7px;height:7px;border-radius:50%;flex-shrink:0;border:1.5px solid var(--rule2);transition:all .2s}
.cd.done{background:var(--grn);border-color:var(--grn)}
.ct{color:var(--ink2);font-family:var(--mono);font-size:12px}
.ct.done{color:var(--ink)}

/* LIVE CORE RAIL — the signature: your snowpack, always in view */
.rail{width:176px;min-width:176px;border-left:1px solid var(--rule);background:var(--bg);height:100%;overflow-y:auto;padding:16px 14px;display:flex;flex-direction:column;gap:10px;cursor:pointer}
.rail:hover .rail-lbl{color:var(--acc)}
.rail-lbl{font-family:var(--mono);font-size:9px;letter-spacing:.16em;text-transform:uppercase;color:var(--ink3);transition:color .12s}
#mini-core{display:flex;justify-content:center}
.rail-stat{display:flex;justify-content:space-between;align-items:baseline;font-family:var(--mono);font-size:10px;color:var(--ink2);border-bottom:1px dashed var(--rule2);padding:5px 1px}
.rail-stat b{color:var(--ink);font-weight:500;font-size:11px}
.rail-hint{font-family:var(--mono);font-size:9px;color:var(--ink3);letter-spacing:.04em;margin-top:auto;opacity:.8}
@media(max-width:1140px){.rail{display:none}}

@media(prefers-reduced-motion:reduce){
  *,*::before,*::after{transition:none!important;animation:none!important}
  .main{scroll-behavior:auto}
}
</style>
</head>
<body>

<div class="topbar">
  <div class="tb-brand">
    <svg width="22" height="22" viewBox="0 0 22 22" fill="none" xmlns="http://www.w3.org/2000/svg">
      <line x1="11" y1="1" x2="11" y2="21" stroke="white" stroke-width="1.5" stroke-opacity="0.9"/>
      <line x1="1" y1="11" x2="21" y2="11" stroke="white" stroke-width="1.5" stroke-opacity="0.9"/>
      <line x1="3.05" y1="3.05" x2="18.95" y2="18.95" stroke="white" stroke-width="1.5" stroke-opacity="0.9"/>
      <line x1="18.95" y1="3.05" x2="3.05" y2="18.95" stroke="white" stroke-width="1.5" stroke-opacity="0.9"/>
      <circle cx="11" cy="1" r="1.8" fill="white" fill-opacity="0.9"/>
      <circle cx="11" cy="21" r="1.8" fill="white" fill-opacity="0.9"/>
      <circle cx="1" cy="11" r="1.8" fill="white" fill-opacity="0.9"/>
      <circle cx="21" cy="11" r="1.8" fill="white" fill-opacity="0.9"/>
      <circle cx="3.05" cy="3.05" r="1.8" fill="white" fill-opacity="0.9"/>
      <circle cx="18.95" cy="18.95" r="1.8" fill="white" fill-opacity="0.9"/>
      <circle cx="18.95" cy="3.05" r="1.8" fill="white" fill-opacity="0.9"/>
      <circle cx="3.05" cy="18.95" r="1.8" fill="white" fill-opacity="0.9"/>
      <circle cx="11" cy="11" r="2.5" fill="white" fill-opacity="0.95"/>
    </svg>
    <span class="tb-wordmark">Cryo<strong>Pit</strong></span>
    <span class="tb-divider">|</span>
    <span class="tb-inst" id="tb-inst">CryoGARS</span>
  </div>
  <span class="tb-pitid" id="tb-pid">—</span>
  <div class="tb-right">
    <span class="tb-pct" id="tb-pct">0%</span>
    <div class="tb-prog" id="tb-prog" title="Completion"><div class="tb-fill" id="tb-fill"></div></div>
    <button class="tb-csv" onclick="newPit()" title="Clear the form to start a new pit">New</button>
    <button class="tb-csv" onclick="doDownload()" title="Download the CSVs to your computer (does not save to the database)">↓ Download</button>
    <button class="tb-save" onclick="doArchive()" title="Save the pit to the database and write the CSVs to the server's export folder">Archive</button>
    <span class="tb-status unsaved" id="tb-st">● not archived</span>
    <button class="tb-theme" onclick="toggleTheme()" id="theme-btn" title="Toggle theme">◑</button>
  </div>
</div>

<div class="shell">

<nav class="index">
  <div class="idx-item active" data-t="s1" onclick="nav(this)"><span class="idx-num">01</span><span class="idx-lbl">Identity</span><span class="idx-pip" id="p1"></span></div>
  <div class="idx-item" data-t="s2" onclick="nav(this)"><span class="idx-num">02</span><span class="idx-lbl">Weather</span><span class="idx-pip" id="p2"></span></div>
  <div class="idx-item" data-t="s3" onclick="nav(this)"><span class="idx-num">03</span><span class="idx-lbl">Ground</span><span class="idx-pip" id="p3"></span></div>
  <div class="idx-item" data-t="s4" onclick="nav(this)"><span class="idx-num">04</span><span class="idx-lbl">Temperature</span><span class="idx-pip" id="p4"></span></div>
  <div class="idx-item" data-t="s5" onclick="nav(this)"><span class="idx-num">05</span><span class="idx-lbl">Density</span><span class="idx-pip" id="p5"></span></div>
  <div class="idx-item" data-t="s6" onclick="nav(this)"><span class="idx-num">06</span><span class="idx-lbl">LWC</span><span class="idx-pip" id="p6"></span></div>
  <div class="idx-item" data-t="s7" onclick="nav(this)"><span class="idx-num">07</span><span class="idx-lbl">Stratigraphy</span><span class="idx-pip" id="p7"></span></div>
  <div class="idx-item" data-t="s8" onclick="nav(this)"><span class="idx-num">08</span><span class="idx-lbl">SSA</span><span class="idx-pip" id="p8"></span></div>
  <div class="idx-item" data-t="s9" onclick="nav(this)"><span class="idx-num">09</span><span class="idx-lbl">Instruments</span><span class="idx-pip" id="p9"></span></div>
  <div class="idx-item" data-t="s10" onclick="nav(this)"><span class="idx-num">10</span><span class="idx-lbl">Checklist</span><span class="idx-pip" id="p10"></span></div>
  <div class="idx-item" data-t="s11" onclick="nav(this);drawProfile()"><span class="idx-num">11</span><span class="idx-lbl">Profile</span><span class="idx-pip" id="p11"></span></div>
  <div class="nav-foot">
    <div class="nav-foot-label">Saved pits · click to load</div>
    <div id="saved-pits-list"><span class="nav-foot-empty">none yet</span></div>
  </div>
</nav>

<main class="main">

<!-- 01 IDENTITY -->
<section class="sec" id="s1">
  <div class="sec-hd"><span class="sec-num">01</span><span class="sec-title">Identity</span></div>
  <div class="sec-body">
    <div class="row">
      <div class="ri" style="flex:1.4">
        <div class="rl">Location <span class="req">*</span></div>
        <select id="loc" onchange="onLoc()">
          <option value="">Select…</option>
          <option>Grand Mesa, CO</option>
          <option>Senator Beck Basin, CO</option>
          <option>Mores Creek, ID</option>
          <option>Banner Summit, ID</option>
          <option value="__c">Other…</option>
        </select>
        <input id="loc-c" placeholder="Type location" style="display:none;border-top:1px solid var(--rule)" oninput="updateId();tick()">
      </div>
      <div class="ri"><div class="rl">Site / transect</div><input id="site" placeholder="LSOS, Transect A…" oninput="updateId()"></div>
      <div class="ri"><div class="rl">Date <span class="req">*</span></div><input type="date" id="date" oninput="updateId();tick()"></div>
      <div class="ri"><div class="rl">Campaign</div><input id="campaign" placeholder="SNEX25" value="SNEX25"></div>
    </div>
    <div class="row">
      <div class="ri" style="flex:1.3">
        <div class="rl">Pit ID <span class="req">*</span></div>
        <div contenteditable="true" class="pitid" id="pitid" spellcheck="false" oninput="onPitEdit()">—</div>
        <div class="hint" id="pidhint">auto · site + date · tap to edit</div>
      </div>
      <div class="ri"><div class="rl">Total depth (cm)</div><input type="number" id="depth" min="0" placeholder="120" oninput="tick()"></div>
      <div class="ri"><div class="rl">Pit open</div><input id="po" maxlength="4" placeholder="0830" oninput="milCheck(this)" style="font-family:var(--mono);letter-spacing:.05em"><div class="hint">HHMM · 24-hr</div></div>
      <div class="ri"><div class="rl">Slope (°)</div><input type="number" id="slope" min="0" max="90" placeholder="0"></div>
    </div>
    <div class="row">
      <div class="ri"><div class="rl">Recorded by <span class="req">*</span></div><input id="recby" placeholder="Your name" oninput="tick()"></div>
      <div class="ri"><div class="rl">Field observers <span class="req">*</span></div><input id="surv" placeholder="A. Jones, B. Lee" oninput="tick()"></div>
      <div class="ri"><div class="rl">GPS device</div><input id="gps" placeholder="GAIA GPSMAP 66"></div>
      <div class="ri"><div class="rl">GPS uncertainty</div><input type="number" step="0.1" min="0" id="gps-unc" placeholder="3" style="font-family:var(--mono)"></div>
      <div class="ri"><div class="rl">Unit</div>
        <select id="gps-unc-unit">
          <option value="m">m</option>
          <option value="cm">cm</option>
          <option value="ft">ft</option>
        </select>
      </div>
      <div class="ri"><div class="rl">WISe serial no.</div><input id="wise" placeholder="—"></div>
    </div>
    <div class="row">
      <div class="ri"><div class="rl">UTM Easting</div><input id="utme" placeholder="476455" oninput="onUTM();tick()" style="font-family:var(--mono)"><div class="coord-note" id="utme-note"></div></div>
      <div class="ri"><div class="rl">UTM Northing</div><input id="utmn" placeholder="7226118" oninput="onUTM();tick()" style="font-family:var(--mono)"><div class="coord-note" id="utmn-note"></div></div>
      <div class="ri"><div class="rl">UTM Zone</div><input id="utmz" placeholder="11N" oninput="onUTM()" style="font-family:var(--mono)"></div>
      <div class="ri"><div class="rl">Elevation (m)</div><input type="number" id="elev" placeholder="—"></div>
    </div>
    <div class="coord-or">— or enter lat / lon · all coordinates are WGS84 —</div>
    <div class="row">
      <div class="ri"><div class="rl">Latitude (°N)</div><input id="lat" placeholder="65.157650" oninput="onLatLon();tick()" style="font-family:var(--mono)"><div class="coord-note" id="lat-note"></div></div>
      <div class="ri"><div class="rl">Longitude (°E)</div><input id="lon" placeholder="-147.502260" oninput="onLatLon();tick()" style="font-family:var(--mono)"><div class="coord-note" id="lon-note"></div></div>
      <div class="ri"><div class="rl">Density cutter (cc)</div>
        <div class="toggles">
          <label class="tog"><input type="checkbox" id="dc100"><span>100</span></label>
          <label class="tog"><input type="checkbox" id="dc250"><span>250</span></label>
          <label class="tog"><input type="checkbox" id="dc1000"><span>1000</span></label>
        </div>
      </div>
      <div class="ri"><div class="rl">Flags</div><input id="flags" placeholder="None"></div>
    </div>
    <div class="row">
      <div class="ri"><div class="rl">Comments / notes</div><textarea id="comments" placeholder="Site conditions, access, anomalies…"></textarea></div>
    </div>
  </div>
</section>

<!-- 02 WEATHER -->
<section class="sec" id="s2">
  <div class="sec-hd"><span class="sec-num">02</span><span class="sec-title">Weather</span></div>
  <div class="sec-body">
    <div class="row">
      <div class="ri"><div class="rl">Precipitation rate</div>
        <div class="toggles">
          <label class="tog"><input type="radio" name="pr" value="None"><span>None</span></label>
          <label class="tog"><input type="radio" name="pr" value="Very light (0.5 cm/hr)"><span>Very light</span></label>
          <label class="tog"><input type="radio" name="pr" value="Light (1 cm/hr)"><span>Light</span></label>
          <label class="tog"><input type="radio" name="pr" value="Moderate (5 cm/hr)"><span>Moderate</span></label>
          <label class="tog"><input type="radio" name="pr" value="Heavy (10 cm/hr)"><span>Heavy</span></label>
        </div>
      </div>
      <div class="ri"><div class="rl">Precipitation type</div>
        <div class="toggles">
          <label class="tog"><input type="radio" name="pt" value="None"><span>None</span></label>
          <label class="tog"><input type="radio" name="pt" value="Rain"><span>Rain</span></label>
          <label class="tog"><input type="radio" name="pt" value="Snow"><span>Snow</span></label>
          <label class="tog"><input type="radio" name="pt" value="Graupel"><span>Graupel</span></label>
          <label class="tog"><input type="radio" name="pt" value="Hail"><span>Hail</span></label>
          <label class="tog"><input type="radio" name="pt" value="Rain/Snow mix"><span>Rain/Snow</span></label>
        </div>
      </div>
    </div>
    <div class="row">
      <div class="ri"><div class="rl">Sky condition</div>
        <div class="toggles">
          <label class="tog"><input type="radio" name="sky" value="Clear"><span>Clear</span></label>
          <label class="tog"><input type="radio" name="sky" value="Few (&lt;1/4)"><span>Few &lt;¼</span></label>
          <label class="tog"><input type="radio" name="sky" value="Scattered (1/4-1/2)"><span>Scattered ¼–½</span></label>
          <label class="tog"><input type="radio" name="sky" value="Broken (&gt;1/2)"><span>Broken &gt;½</span></label>
          <label class="tog"><input type="radio" name="sky" value="Overcast"><span>Overcast</span></label>
        </div>
      </div>
      <div class="ri"><div class="rl">Wind</div>
        <div class="toggles">
          <label class="tog"><input type="radio" name="wind" value="Calm (0 mph)"><span>Calm</span></label>
          <label class="tog"><input type="radio" name="wind" value="Light (1-16 mph)"><span>Light</span></label>
          <label class="tog"><input type="radio" name="wind" value="Moderate (17-25 mph)"><span>Moderate</span></label>
          <label class="tog"><input type="radio" name="wind" value="Strong (26-38 mph)"><span>Strong</span></label>
          <label class="tog"><input type="radio" name="wind" value="Extreme (&gt;38 mph)"><span>Extreme</span></label>
        </div>
      </div>
    </div>
  </div>
</section>

<!-- 03 GROUND -->
<section class="sec" id="s3">
  <div class="sec-hd"><span class="sec-num">03</span><span class="sec-title">Ground &amp; vegetation</span></div>
  <div class="sec-body">
    <div class="row">
      <div class="ri"><div class="rl">Ground condition</div>
        <div class="toggles">
          <label class="tog"><input type="radio" name="gc" value="Frozen"><span>Frozen</span></label>
          <label class="tog"><input type="radio" name="gc" value="Moist"><span>Moist</span></label>
          <label class="tog"><input type="radio" name="gc" value="Saturated"><span>Saturated</span></label>
        </div>
      </div>
      <div class="ri"><div class="rl">Ground roughness</div>
        <div class="toggles">
          <label class="tog"><input type="radio" name="gr" value="Smooth (&lt;5 cm)"><span>Smooth &lt;5 cm</span></label>
          <label class="tog"><input type="radio" name="gr" value="Rough (5-20 cm)"><span>Rough 5–20</span></label>
          <label class="tog"><input type="radio" name="gr" value="Rugged (&gt;20 cm)"><span>Rugged &gt;20</span></label>
        </div>
      </div>
      <div class="ri"><div class="rl">Snow cover condition</div>
        <div class="toggles">
          <label class="tog"><input type="radio" name="scc" value="Continuous"><span>Continuous</span></label>
          <label class="tog"><input type="radio" name="scc" value="Discontinuous"><span>Discontinuous</span></label>
          <label class="tog"><input type="radio" name="scc" value="Patchy"><span>Patchy</span></label>
        </div>
      </div>
    </div>
    <div class="row">
      <div class="ri"><div class="rl">Tree canopy</div>
        <div class="toggles">
          <label class="tog"><input type="radio" name="tc" value="No trees"><span>No trees</span></label>
          <label class="tog"><input type="radio" name="tc" value="Sparse (5-20%)"><span>Sparse 5–20%</span></label>
          <label class="tog"><input type="radio" name="tc" value="Open (20-70%)"><span>Open 20–70%</span></label>
          <label class="tog"><input type="radio" name="tc" value="Closed (&gt;70%)"><span>Closed &gt;70%</span></label>
        </div>
      </div>
      <div class="ri"><div class="rl">Standing water</div>
        <div class="toggles">
          <label class="tog"><input type="radio" name="sw" value="N/A"><span>N/A</span></label>
          <label class="tog"><input type="radio" name="sw" value="Yes"><span>Yes</span></label>
          <label class="tog"><input type="radio" name="sw" value="No"><span>No</span></label>
        </div>
      </div>
    </div>
    <div class="row">
      <div class="ri"><div class="rl">Vegetation</div>
        <div class="toggles">
          <label class="tog"><input type="checkbox" id="vb"><span>Bare</span></label>
          <label class="tog"><input type="checkbox" id="vg"><span>Grass</span></label>
          <label class="tog"><input type="checkbox" id="vs"><span>Shrub</span></label>
          <label class="tog"><input type="checkbox" id="vd"><span>Deadfall</span></label>
        </div>
      </div>
      <div class="ri"><div class="rl">Veg. height (cm)</div><input type="number" id="vh" min="0" placeholder="0"></div>
      <div class="ri"><div class="rl">New snow depth (cm)</div><input type="number" id="nd" min="0" placeholder="0"></div>
      <div class="ri"><div class="rl">New snow SWE (mm)</div><input type="number" id="ns" min="0" placeholder="0"></div>
    </div>
  </div>
</section>

<!-- 04 TEMPERATURE -->
<section class="sec" id="s4">
  <div class="sec-hd"><span class="sec-num">04</span><span class="sec-title">Temperature profile</span><span class="sec-meta" id="tc-cnt">0 measurements</span></div>
  <div class="sec-body">
    <div class="row" style="margin-bottom:16px">
      <div class="ri"><div class="rl">Profile start</div><input id="ts" maxlength="4" placeholder="0808" oninput="milCheck(this)" style="font-family:var(--mono);letter-spacing:.05em"><div class="hint">HHMM</div></div>
      <div class="ri"><div class="rl">Profile end</div><input id="te" maxlength="4" placeholder="0828" oninput="milCheck(this)" style="font-family:var(--mono);letter-spacing:.05em"><div class="hint">HHMM</div></div>
      <div class="ri"><div class="rl">Auto-fill depths</div>
        <div style="display:flex;gap:6px;align-items:center;padding:6px 12px">
          <select id="t-interval" style="flex:0 0 auto;width:auto;padding:4px 8px;border:1px solid var(--rule);border-radius:6px">
            <option value="10">every 10 cm</option>
            <option value="5">every 5 cm</option>
          </select>
          <button class="add" style="width:auto;border:1px solid var(--rule);border-radius:999px;padding:4px 12px" onclick="autofillTemp()">↧ generate from total depth</button>
        </div>
        <div class="hint">starts at snow height, snaps to nearest interval, steps to 0</div>
      </div>
    </div>
    <div class="pw"><table class="pt">
      <thead><tr><th>Height above ground (cm)</th><th>Temperature (°C)</th><th style="width:36px"></th></tr></thead>
      <tbody id="tb"></tbody>
    </table><button class="add" onclick="addRow('t')">+ add measurement</button></div>
  </div>
</section>

<!-- 05 DENSITY -->
<section class="sec" id="s5">
  <div class="sec-hd"><span class="sec-num">05</span><span class="sec-title">Density</span><span class="sec-meta" id="dc-cnt">0 intervals</span></div>
  <div class="sec-body">
    <p style="font-family:var(--mono);font-size:11px;color:var(--ink3);margin-bottom:14px;letter-spacing:.02em">Height above ground · A B C = three cutter samples · average auto-computed</p>
    <div style="display:flex;gap:6px;align-items:center;margin-bottom:10px">
      <select id="d-interval" style="width:auto;padding:4px 8px;border:1px solid var(--rule);border-radius:6px">
        <option value="10">10 cm intervals</option>
        <option value="5">5 cm intervals</option>
      </select>
      <button class="add" style="width:auto;border:1px solid var(--rule);border-radius:999px;padding:4px 12px" onclick="autofillDensity()">↧ generate intervals from total depth</button>
    </div>
    <div class="pw"><table class="pt">
      <thead><tr><th>Top (cm)</th><th>Bottom (cm)</th><th>A (kg/m³)</th><th>B (kg/m³)</th><th>C (kg/m³)</th><th>Avg</th><th style="width:36px"></th></tr></thead>
      <tbody id="db"></tbody>
    </table><button class="add" onclick="addRow('d')">+ add interval</button></div>
  </div>
</section>

<!-- 06 LWC -->
<section class="sec" id="s6">
  <div class="sec-hd"><span class="sec-num">06</span><span class="sec-title">LWC / permittivity</span><span class="sec-meta" id="lc-cnt">0 intervals</span></div>
  <div class="sec-body">
    <p style="font-family:var(--mono);font-size:11px;color:var(--ink3);margin-bottom:14px;letter-spacing:.02em">Permittivity profiles A and B (unitless) · height above ground</p>
    <div style="margin-bottom:10px">
      <button class="add" style="width:auto;border:1px solid var(--rule);border-radius:999px;padding:4px 12px" onclick="copyDensityIntervals()">⎘ copy intervals from density</button>
      <span class="hint" style="display:inline-block;margin-left:8px">pulls the same top/bottom pairs; you enter permittivity</span>
    </div>
    <div class="pw"><table class="pt">
      <thead><tr><th>Top (cm)</th><th>Bottom (cm)</th><th>Permittivity A</th><th>Permittivity B</th><th style="width:36px"></th></tr></thead>
      <tbody id="lb"></tbody>
    </table><button class="add" onclick="addRow('l')">+ add interval</button></div>
  </div>
</section>

<!-- 07 STRATIGRAPHY -->
<section class="sec" id="s7">
  <div class="sec-hd"><span class="sec-num">07</span><span class="sec-title">Stratigraphy</span><span class="sec-meta" id="sc-cnt">0 layers</span></div>
  <div class="sec-body">
    <div class="pw"><table class="pt">
      <thead><tr><th>Top</th><th>Bot</th><th>Gmin</th><th>Gmax</th><th>Gavg</th><th>Type</th><th>Hardness</th><th>Wet</th><th>Comments</th><th style="width:36px"></th></tr></thead>
      <tbody id="sb"></tbody>
    </table><button class="add" onclick="addRow('s')">+ add layer</button></div>
  </div>
</section>

<!-- 08 SSA -->
<section class="sec" id="s8">
  <div class="sec-hd"><span class="sec-num">08</span><span class="sec-title">SSA</span><span class="sec-meta" id="sa-cnt">0 measurements</span></div>
  <div class="sec-body">
    <p style="font-family:var(--mono);font-size:11px;color:var(--ink3);margin-bottom:14px;letter-spacing:.02em">Specific surface area · optional · height above ground</p>
    <div class="row">
      <div class="ri"><div class="rl">Instrument</div>
        <select id="ssa-inst">
          <option value="">Select instrument…</option>
          <option value="IceCube">IceCube</option>
          <option value="IRIS2">IRIS2</option>
          <option value="IRIS">IRIS</option>
        </select>
      </div>
      <div class="ri"><div class="rl">Calibration time (HHMM)</div><input id="ssa-cal-time" maxlength="4" placeholder="0800" oninput="milCheck(this)" style="font-family:var(--mono);letter-spacing:.05em"></div>
      <div class="ri"><div class="rl">SSA operator</div><input id="ssa-operator" placeholder="if different from recorder"></div>
      <div class="ri" style="flex:2"><div class="rl">Spectralon levels (comma-separated)</div><input id="ssa-spec" placeholder="99,60,40,20,5,0" style="font-family:var(--mono)"></div>
    </div>
    <div class="row">
      <div class="ri"><div class="rl">Calibration values V (comma-separated)</div><input id="ssa-calv" placeholder="2.024,1.686,1.226,0.705,0.328,0.062" style="font-family:var(--mono)"></div>
      <div class="ri"><div class="rl">Calibration notes</div><input id="ssa-notes" placeholder="—"></div>
    </div>
    <div class="pw"><table class="pt">
      <thead><tr><th>Height (cm)</th><th>Signal (V)</th><th>Reflectance (%)</th><th>SSA (m²/kg)</th><th>Grain type</th><th>Comments</th><th style="width:36px"></th></tr></thead>
      <tbody id="ssab"></tbody>
    </table><button class="add" onclick="addRow('sa')">+ add measurement</button></div>
  </div>
</section>

<!-- 09 INSTRUMENTS -->
<section class="sec" id="s9">
  <div class="sec-hd"><span class="sec-num">09</span><span class="sec-title">Instruments &amp; tasks</span></div>
  <div class="sec-body" id="ig"></div>
</section>

<!-- 10 CHECKLIST -->
<section class="sec" id="s10">
  <div class="sec-hd"><span class="sec-num">10</span><span class="sec-title">Checklist</span></div>
  <div class="sec-body">
    <div class="cl-sum">
      <div class="cl-pct" id="cl-pct">0%</div>
      <div class="cl-bw">
        <div class="cl-bt"><div class="cl-bf" id="cl-fill"></div></div>
        <div class="cl-bl" id="cl-lbl">0 of 10 sections complete</div>
      </div>
    </div>
    <div id="cl-items"></div>
  </div>
</section>

<!-- 11 PROFILE -->
<section class="sec" id="s11">
  <div class="sec-hd"><span class="sec-num">11</span><span class="sec-title">Profile</span>
    <span class="sec-meta">height above ground · 0 at bottom</span></div>
  <div class="sec-body">
    <p style="font-family:var(--mono);font-size:11px;color:var(--ink3);margin-bottom:14px;letter-spacing:.02em">
      Live plot from stratigraphy + density + temperature. Needs Total depth (§1) and stratigraphy layers (§7).
      <button class="add" style="display:inline-block;width:auto;border:1px solid var(--rule);border-radius:999px;margin-left:8px;padding:4px 12px" onclick="drawProfile()">↻ redraw</button>
    </p>
    <div id="profile-wrap" style="overflow-x:auto;border:1px solid var(--rule);border-radius:var(--r);background:var(--w);padding:8px"></div>
  </div>
</section>

</main>

<aside class="rail" title="Live core — click for the full profile" onclick="document.querySelector('[data-t=s11]').click();drawProfile()">
  <div class="rail-lbl">Live core</div>
  <div id="mini-core"></div>
  <div class="rail-stat"><span>HS</span><b id="mc-hs">—</b></div>
  <div class="rail-stat"><span>layers</span><b id="mc-lay">0</b></div>
  <div class="rail-stat"><span>ρ bulk</span><b id="mc-den">—</b></div>
  <div class="rail-stat"><span>SWE</span><b id="mc-swe">—</b></div>
  <div class="rail-stat" style="border-bottom:none"><span id="mc-cov-lbl" style="font-size:8px;color:var(--ink3)"></span><b id="mc-cov" style="font-size:9px;color:var(--ink3);font-weight:400"></b></div>
  <div class="rail-stat"><span>T min</span><b id="mc-tmin">—</b></div>
  <div class="rail-hint">click for full profile →</div>
</aside>
</div>

<script>
/* Same-origin API: the page and the JSON routes come from one Flask process,
   so paths are relative — no host, no port, no CORS. */
const API = '';
const G=['PP','RG','FC','SH','MM','DF','DH','MF','IF',
  'PPsd','PPgp','PPrm','RGwp','RGxf','RGlr',
  'FCsf','FCxr','FCso',
  'DHcp','DHpr','DHla','DHxr','SHxr',
  'MFcl','MFsl','MFcr','IFsc','IFrc','IFbi'];
const H=['F','4F','1F','P','K','I'];
const W=['D','M','W','V','S'];
const INST=[
  {g:'Measurement'},
  {n:'Digital LWC (Snow Fork / Denoth)',sn:1},{n:'Standard ram',sn:1},
  {n:'Powder ram',sn:1},{n:'Force ram',sn:1},{n:'Snow Micro Pen (SMP)',sn:1},
  {n:'Slush ram',sn:1},{n:'Lyte Probe',sn:1},
  {n:'IceCube / IRIS (SSA)',sn:1},{n:'NIR / SSA Box',sn:1},
  {g:'Spatial surveys'},
  {n:'HS depth transects'},{n:'Snow Scope transects'},{n:'Surface roughness'},
  {g:'Documentation'},
  {n:'Pit wall photos — VIS'},{n:'Pit wall photos — NIR'},
  {n:'Grain photos (all layers)'},{n:'Site overview photo'},
  {g:'Closeout'},
  {n:'Pit backfilled'},{n:'Red pole / flag left'},{n:'Data backed up'},
];

/* num(): the one number parser. Returns null for blank/garbage and PRESERVES
   legitimate zeros. Replaces both broken v2.0 patterns:
     parseFloat(x)||0    -> a blank became a real 0 (fabricated measurement)
     parseFloat(x)||null -> a typed 0 became null (lost measurement)        */
function num(v){
  if(v===undefined||v===null)return null;
  const n=parseFloat(v);
  return Number.isFinite(n)?n:null;
}

let _loaded_pid=null;   /* pit_id this form was loaded from (overwrite implied) */
let _restoring=false;   /* true while populate() runs; suppresses draft churn   */

function buildInst(){
  let h='',ii=0,open=false;
  INST.forEach(it=>{
    if(it.g){
      if(open)h+='</tbody></table>';
      h+=`<div class="ig-lbl">${it.g}</div><table class="it"><thead><tr><th style="width:46%">Instrument / task</th><th>Serial no.</th><th>Used</th></tr></thead><tbody>`;
      open=true;
    } else {
      const i=ii++;
      h+=`<tr><td>${it.n}</td><td>${it.sn?`<input class="sn" id="sn${i}" placeholder="—">`:'—'}</td>
          <td><div class="yn"><button class="y" id="yy${i}" onclick="setyn(${i},'Y')">Y</button>
          <button class="n on" id="yn${i}" onclick="setyn(${i},'N')">N</button></div></td></tr>`;
    }
  });
  if(open)h+='</tbody></table>';
  document.getElementById('ig').innerHTML=h;
  window._ic=ii;
}
function setyn(i,v){
  document.getElementById('yy'+i).classList.toggle('on',v==='Y');
  document.getElementById('yn'+i).classList.toggle('on',v==='N');
  tick();
}
function so(a){return a.map(v=>`<option value="${v}">${v}</option>`).join('')}

function addRow(t,focus){
  const map={t:'tb',d:'db',l:'lb',s:'sb',sa:'ssab'};
  const tr=document.createElement('tr');
  if(t==='t'){
    tr.innerHTML=`<td><input type="number" placeholder="100"></td>
      <td><input type="number" step="0.1" placeholder="-2.0"></td>
      <td><button class="del" onclick="this.closest('tr').remove();cnt('t')">×</button></td>`;
  } else if(t==='d'){
    tr.innerHTML=`<td><input type="number" placeholder="120"></td>
      <td><input type="number" placeholder="110"></td>
      <td><input type="number" oninput="calcAvg(this.closest('tr'))"></td>
      <td><input type="number" oninput="calcAvg(this.closest('tr'))"></td>
      <td><input type="number" oninput="calcAvg(this.closest('tr'))"></td>
      <td class="avg"><input readonly placeholder="—" tabindex="-1"></td>
      <td><button class="del" onclick="this.closest('tr').remove();cnt('d')">×</button></td>`;
  } else if(t==='l'){
    tr.innerHTML=`<td><input type="number" placeholder="120"></td>
      <td><input type="number" placeholder="110"></td>
      <td><input type="number" step="0.001" placeholder="1.173"></td>
      <td><input type="number" step="0.001" placeholder="—"></td>
      <td><button class="del" onclick="this.closest('tr').remove();cnt('l')">×</button></td>`;
  } else if(t==='s'){
    tr.innerHTML=`<td><input type="number" placeholder="120" style="min-width:62px"></td>
      <td><input type="number" placeholder="110" style="min-width:62px"></td>
      <td><input type="number" step="0.1" placeholder="0.5" style="min-width:68px"></td>
      <td><input type="number" step="0.1" placeholder="1.0" style="min-width:68px"></td>
      <td><input type="number" step="0.1" placeholder="0.7" style="min-width:68px"></td>
      <td><select style="min-width:60px">${so(G)}</select></td>
      <td><select style="min-width:52px">${so(H)}</select></td>
      <td><select style="min-width:50px">${so(W)}</select></td>
      <td><input type="text" placeholder="notes…" style="min-width:100px"></td>
      <td><button class="del" onclick="this.closest('tr').remove();cnt('s')">×</button></td>`;
  } else if(t==='sa'){
    tr.innerHTML=`<td><input type="number" placeholder="35"></td>
      <td><input type="number" step="0.001" placeholder="1.147"></td>
      <td><input type="number" step="0.01" placeholder="36.22"></td>
      <td><input type="number" step="0.01" placeholder="23.76"></td>
      <td><select style="min-width:60px">${so(G)}</select></td>
      <td><input type="text" placeholder="notes…" style="min-width:80px"></td>
      <td><button class="del" onclick="this.closest('tr').remove();cnt('sa')">×</button></td>`;
  }
  document.getElementById(map[t]).appendChild(tr);
  cnt(t); tick();
  if(focus!==false)tr.querySelector('input,select').focus();
  return tr;
}

// Auto-fill helpers ------------------------------------------------
function _hs(){ return num(gv('depth'))||0; }   // total snow height (HS)

// Temperature: start at HS, snap to nearest interval boundary below, step to 0.
// e.g. HS=83, step=10 -> 83,80,70,...,0 ; step=5 -> 83,80,75,...,0
function autofillTemp(){
  const hs=_hs(), step=parseInt(gv('t-interval'))||10;
  if(!hs){setst('set Total depth (§1) first','err');return;}
  const depths=[hs];
  let d=Math.floor(hs/step)*step;          // snap down to interval boundary
  if(d===hs) d-=step;                       // if HS already on boundary, next one down
  for(; d>0; d-=step) depths.push(d);
  depths.push(0);
  document.getElementById('tb').innerHTML='';
  depths.forEach(h=>{
    const tr=document.createElement('tr');
    tr.innerHTML=`<td><input type="number" value="${h}"></td>
      <td><input type="number" step="0.1" placeholder="-2.0"></td>
      <td><button class="del" onclick="this.closest('tr').remove();cnt('t')">×</button></td>`;
    document.getElementById('tb').appendChild(tr);
  });
  cnt('t'); tick();
}

// Density: fixed intervals from HS downward. e.g. HS=87,step=10 -> 87-77,77-67,...
function autofillDensity(){
  const hs=_hs(), step=parseInt(gv('d-interval'))||10;
  if(!hs){setst('set Total depth (§1) first','err');return;}
  const rows=[];
  for(let top=hs; top>0; top-=step) rows.push([top, Math.max(top-step,0)]);
  document.getElementById('db').innerHTML='';
  rows.forEach(([top,bot])=>{
    const tr=document.createElement('tr');
    tr.innerHTML=`<td><input type="number" value="${top}"></td>
      <td><input type="number" value="${bot}"></td>
      <td><input type="number" oninput="calcAvg(this.closest('tr'))"></td>
      <td><input type="number" oninput="calcAvg(this.closest('tr'))"></td>
      <td><input type="number" oninput="calcAvg(this.closest('tr'))"></td>
      <td class="avg"><input readonly placeholder="—" tabindex="-1"></td>
      <td><button class="del" onclick="this.closest('tr').remove();cnt('d')">×</button></td>`;
    document.getElementById('db').appendChild(tr);
  });
  cnt('d'); tick();
}

// LWC: copy the top/bottom interval pairs from density, leaving permittivity blank.
function copyDensityIntervals(){
  const drows=document.querySelectorAll('#db tr');
  if(!drows.length){setst('add density intervals first','err');return;}
  document.getElementById('lb').innerHTML='';
  drows.forEach(dtr=>{
    const di=dtr.querySelectorAll('input');
    const top=di[0].value, bot=di[1].value;
    const tr=document.createElement('tr');
    tr.innerHTML=`<td><input type="number" value="${top}"></td>
      <td><input type="number" value="${bot}"></td>
      <td><input type="number" step="0.001" placeholder="1.173"></td>
      <td><input type="number" step="0.001" placeholder="—"></td>
      <td><button class="del" onclick="this.closest('tr').remove();cnt('l')">×</button></td>`;
    document.getElementById('lb').appendChild(tr);
  });
  cnt('l'); tick();
}

function calcAvg(tr){
  // num() so a legitimate 0 counts and blanks don't (the old ||null dropped 0s)
  const ins=tr.querySelectorAll('input[type=number]');
  const v=[ins[2],ins[3],ins[4]].map(i=>num(i.value)).filter(x=>x!==null);
  tr.querySelector('.avg input').value=v.length?Math.round(v.reduce((a,b)=>a+b)/v.length):'';
}

function cnt(t){
  const map={t:['tb','tc-cnt','measurements'],d:['db','dc-cnt','intervals'],
             l:['lb','lc-cnt','intervals'],s:['sb','sc-cnt','layers'],sa:['ssab','sa-cnt','measurements']};
  const [bid,cid,lbl]=map[t];
  const n=document.getElementById(bid).children.length;
  document.getElementById(cid).textContent=`${n} ${lbl}`;
  tick();
}

function milCheck(inp){
  const v=inp.value;
  if(v.length===4){
    inp.style.color=goodTime(v)?'var(--ink)':'var(--red)';
  } else inp.style.color='var(--ink)';
}
function goodTime(v){
  return /^\d{4}$/.test(v)&&parseInt(v.slice(0,2))<=23&&parseInt(v.slice(2))<=59;
}

// Pure JS UTM <-> WGS84 --------------------------------------------
function latLonToUtm(lat,lon){
  const a=6378137,f=1/298.257223563,b=a*(1-f),e2=1-(b*b)/(a*a);
  const zn=Math.floor((lon+180)/6)+1,lcm=(zn-1)*6-180+3;
  const k0=0.9996,lr=lat*Math.PI/180,lc=lcm*Math.PI/180,dl=lon*Math.PI/180-lc;
  const n=(a-b)/(a+b);
  const A_=a*(1-n+(5/4)*(n*n-n**3)+(81/64)*(n**4-n**5));
  const B_=(3*a/2)*(n-n**2+(7/8)*(n**3-n**4)+(55/64)*n**5);
  const C_=(15*a/8)*(n*n-n**3+(3/4)*(n**4-n**5));
  const D_=(35*a/24)*(n**3-n**4+(11/16)*n**5);
  const E_=(315*a/80)*n**4;
  const M=A_*lr-B_*Math.sin(2*lr)+C_*Math.sin(4*lr)-D_*Math.sin(6*lr)+E_*Math.sin(8*lr);
  const sL=Math.sin(lr),cL=Math.cos(lr),tL=Math.tan(lr);
  const nu=a/Math.sqrt(1-e2*sL*sL),p=dl;
  const E1=nu*cL*p,E2=nu*cL**3*(1-tL*tL+e2*cL*cL/(1-e2))*p**3/6,E3=nu*cL**5*(5-18*tL*tL+tL**4)*p**5/120;
  const N1=M,N2=nu*sL*cL*p*p/2,N3=nu*sL*cL**3*(5-tL*tL+9*e2*cL*cL/(1-e2))*p**4/24;
  const east=k0*(E1+E2+E3)+500000,north=k0*(N1+N2+N3)+(lat>=0?0:10000000);
  return{e:Math.round(east*10)/10,n:Math.round(north*10)/10,zn,zl:lat>=0?'N':'S'};
}
function utmToLatLon(e,n,zn,zl){
  const a=6378137,f=1/298.257223563,b=a*(1-f),e2=1-(b*b)/(a*a),ep2=e2/(1-e2);
  const N0=zl.toUpperCase()<'N'?10000000:0,k0=0.9996;
  const x=e-500000,y=n-N0,lcm=((zn-1)*6-180+3)*Math.PI/180;
  const M=y/k0,mu=M/(a*(1-e2/4-3*e2*e2/64-5*e2**3/256));
  const e1=(1-Math.sqrt(1-e2))/(1+Math.sqrt(1-e2));
  const phi1=mu+(3*e1/2-27*e1**3/32)*Math.sin(2*mu)+(21*e1*e1/16-55*e1**4/32)*Math.sin(4*mu)+(151*e1**3/96)*Math.sin(6*mu);
  const sP=Math.sin(phi1),cP=Math.cos(phi1),tP=Math.tan(phi1);
  const N1=a/Math.sqrt(1-e2*sP*sP),T1=tP*tP,C1=ep2*cP*cP,R1=a*(1-e2)/Math.pow(1-e2*sP*sP,1.5);
  const D=x/(N1*k0);
  const lat=phi1-(N1*tP/R1)*(D*D/2-(5+3*T1+10*C1-4*C1*C1-9*ep2)*D**4/24+(61+90*T1+298*C1+45*T1*T1-252*ep2-3*C1*C1)*D**6/720);
  const lon=lcm+(D-(1+2*T1+C1)*D**3/6+(5-2*C1+28*T1-3*C1*C1+8*ep2+24*T1*T1)*D**5/120)/cP;
  return{lat:Math.round(lat*180/Math.PI*1e6)/1e6,lon:Math.round(lon*180/Math.PI*1e6)/1e6};
}

let _cl=false;
function onUTM(){
  if(_cl)return;
  const e=num(document.getElementById('utme').value);
  const n=num(document.getElementById('utmn').value);
  const zr=document.getElementById('utmz').value.trim();
  if(e===null||n===null||!zr)return;
  const zm=zr.match(/^(\d{1,2})([A-Za-z])$/);
  if(!zm)return;
  try{
    const r=utmToLatLon(e,n,parseInt(zm[1]),zm[2]);
    _cl=true;
    document.getElementById('lat').value=r.lat;
    document.getElementById('lon').value=r.lon;
    document.getElementById('lat-note').textContent='↑ converted from UTM';
    document.getElementById('lon-note').textContent='↑ converted from UTM';
    document.getElementById('utme-note').textContent='';
    document.getElementById('utmn-note').textContent='';
    setTimeout(()=>_cl=false,200);
  }catch(ex){}
}
function onLatLon(){
  if(_cl)return;
  const lat=num(document.getElementById('lat').value);
  const lon=num(document.getElementById('lon').value);
  if(lat===null||lon===null)return;
  try{
    const r=latLonToUtm(lat,lon);
    _cl=true;
    document.getElementById('utme').value=r.e;
    document.getElementById('utmn').value=r.n;
    document.getElementById('utmz').value=r.zn+''+r.zl;
    document.getElementById('utme-note').textContent='↑ converted from lat/lon';
    document.getElementById('utmn-note').textContent='↑ converted from lat/lon';
    document.getElementById('lat-note').textContent='';
    document.getElementById('lon-note').textContent='';
    setTimeout(()=>_cl=false,200);
  }catch(ex){}
}

// Theme --------------------------------------------------------------
function toggleTheme(){
  const dark=document.documentElement.getAttribute('data-theme')==='dark';
  document.documentElement.setAttribute('data-theme',dark?'light':'dark');
  document.getElementById('theme-btn').textContent=dark?'◑':'◐';
  try{localStorage.setItem('cp-theme',dark?'light':'dark');}catch(e){}
}
(function(){
  try{
    const t=localStorage.getItem('cp-theme');
    if(t==='dark'){document.documentElement.setAttribute('data-theme','dark');document.getElementById('theme-btn').textContent='◐';}
  }catch(e){}
})();

// Nav ----------------------------------------------------------------
function nav(el){
  document.querySelectorAll('.idx-item').forEach(n=>n.classList.remove('active'));
  el.classList.add('active');
  const target=document.getElementById(el.dataset.t);
  const main=document.querySelector('.main');
  const top=target.getBoundingClientRect().top
            -main.getBoundingClientRect().top
            +main.scrollTop;
  main.scrollTo({top,behavior:'smooth'});
}

let _pe=false;
function onPitEdit(){_pe=true;const v=document.getElementById('pitid').textContent.trim();document.getElementById('tb-pid').textContent=v||'—';tick();}
function onLoc(){const v=document.getElementById('loc').value;document.getElementById('loc-c').style.display=v==='__c'?'block':'none';updateId();tick();}
function updateId(){
  if(_pe)return;
  const loc=document.getElementById('loc').value;
  const site=document.getElementById('site').value.trim();
  const d=document.getElementById('date').value;
  if(!d)return;
  const ds=d.replace(/-/g,''),sc=site.replace(/\W+/g,'').toUpperCase().slice(0,6);
  const id=(sc||'PIT')+ds;
  document.getElementById('pitid').textContent=id;
  document.getElementById('tb-pid').textContent=id;
  tick();
}
function gv(id){return(document.getElementById(id)||{}).value||''}
function gr(name){const r=document.querySelector(`input[name="${name}"]:checked`);return r?r.value:''}

function tick(){
  const loc=document.getElementById('loc').value;
  const lok=loc&&loc!=='__c'||document.getElementById('loc-c').value.trim();
  const pid=document.getElementById('pitid').textContent.trim();
  const chk=[
    lok&&pid&&pid!=='—',
    gv('recby').trim(), gv('surv').trim(),
    gv('utme').trim()||gv('lat').trim(),
    gr('pr'), gr('gc'),
    document.getElementById('tb').children.length>0,
    document.getElementById('db').children.length>0,
    document.getElementById('lb').children.length>0,
    document.getElementById('sb').children.length>0,
  ];
  const done=chk.filter(Boolean).length,pct=Math.round(done/chk.length*100);
  document.getElementById('tb-fill').style.width=pct+'%';
  document.getElementById('tb-pct').textContent=pct+'%';
  document.getElementById('tb-prog').title='Completion: '+pct+'%';
  const ssaDone=document.getElementById('ssab').children.length>0;
  const instDone=document.querySelectorAll('.yn button.y.on').length>0;
  const pips={p1:chk[0]&&chk[1]&&chk[2]&&chk[3],p2:chk[4],p3:chk[5],
    p4:chk[6],p5:chk[7],p6:chk[8],p7:chk[9],p8:ssaDone,p9:instDone,p10:pct===100};
  Object.entries(pips).forEach(([id,v])=>{const e=document.getElementById(id);if(e)e.classList.toggle('done',!!v)});
  document.getElementById('cl-pct').textContent=pct+'%';
  document.getElementById('cl-fill').style.width=pct+'%';
  document.getElementById('cl-lbl').textContent=`${done} of ${chk.length} sections complete`;
  const labels=['Location & Pit ID','Recorded by','Field observers','Coordinates',
    'Weather','Ground','Temperature','Density','LWC','Stratigraphy'];
  document.getElementById('cl-items').innerHTML=labels.map((l,i)=>`
    <div class="ci"><div class="cd${chk[i]?' done':''}"></div>
    <span class="ct${chk[i]?' done':''}">${l}</span></div>`).join('');
  scheduleDraft();
  scheduleMini();
}

function collect(){
  const loc=document.getElementById('loc').value;
  const location=loc==='__c'?document.getElementById('loc-c').value:loc;
  const veg=[];
  [{id:'vb',n:'bare'},{id:'vg',n:'grass'},{id:'vs',n:'shrub'},{id:'vd',n:'deadfall'}]
    .forEach(({id,n})=>{if(document.getElementById(id)?.checked)veg.push(n)});
  const zr=gv('utmz'),zm=zr.match(/^(\d{1,2})([A-Za-z])$/);
  /* Tables: num() everywhere, and rows whose cells are ALL empty are skipped —
     an abandoned "+ add" row no longer fabricates a 0 cm / 0.0 degC reading. */
  const temperature=[];
  document.querySelectorAll('#tb tr').forEach(tr=>{
    const ins=tr.querySelectorAll('input');
    const h=num(ins[0].value),t=num(ins[1].value);
    if(h===null&&t===null)return;
    temperature.push({height:h,temp:t});
  });
  const density=[];
  document.querySelectorAll('#db tr').forEach(tr=>{
    const ins=tr.querySelectorAll('input');
    const r={top:num(ins[0].value),bottom:num(ins[1].value),
      a:num(ins[2].value),b:num(ins[3].value),c:num(ins[4].value)};
    if(r.top===null&&r.bottom===null&&r.a===null&&r.b===null&&r.c===null)return;
    density.push(r);
  });
  const lwc=[];
  document.querySelectorAll('#lb tr').forEach(tr=>{
    const ins=tr.querySelectorAll('input');
    const r={top:num(ins[0].value),bottom:num(ins[1].value),
      a:num(ins[2].value),b:num(ins[3].value)};
    if(r.top===null&&r.bottom===null&&r.a===null&&r.b===null)return;
    lwc.push(r);
  });
  const stratigraphy=[];
  document.querySelectorAll('#sb tr').forEach(tr=>{
    const ins=tr.querySelectorAll('input');const sels=tr.querySelectorAll('select');
    const r={top:num(ins[0].value),bottom:num(ins[1].value),
      gmin:num(ins[2].value),gmax:num(ins[3].value),gavg:num(ins[4].value),
      gtype:sels[0]?.value||'',hardness:sels[1]?.value||'',wetness:sels[2]?.value||'',comments:ins[5]?.value||''};
    if(r.top===null&&r.bottom===null&&r.gmin===null&&r.gmax===null&&r.gavg===null&&!r.comments)return;
    stratigraphy.push(r);
  });
  const ssa=[];
  document.querySelectorAll('#ssab tr').forEach(tr=>{
    const ins=tr.querySelectorAll('input');const sels=tr.querySelectorAll('select');
    const r={height:num(ins[0].value),signal:num(ins[1].value),
      reflectance:num(ins[2].value),ssa:num(ins[3].value),
      grain_type:sels[0]?.value||'',comments:ins[4]?.value||''};
    if(r.height===null&&r.signal===null&&r.reflectance===null&&r.ssa===null&&!r.comments)return;
    ssa.push(r);
  });
  const specStr=gv('ssa-spec'),calvStr=gv('ssa-calv');
  // Collect instrument log
  const instruments=[];
  INST.forEach((it,idx)=>{
    if(it.g)return;
    const i=instruments.length;
    const used=document.getElementById('yy'+i)?.classList.contains('on')?'Y':'N';
    const sn=document.getElementById('sn'+i)?.value||'—';
    instruments.push({name:it.n,sn,used});
  });
  // Density cutter — multi-select (100/250/1000 cc), joined as a string
  const cutters=[];
  [['dc100','100'],['dc250','250'],['dc1000','1000']].forEach(([id,v])=>{
    if(document.getElementById(id)?.checked)cutters.push(v);
  });
  const density_cutter=cutters.length?cutters.join(', ')+' cc':'';
  const ssaOp=gv('ssa-operator').trim();
  return{
    meta:{pit_id:document.getElementById('pitid').textContent.trim(),
      location,site:gv('site'),campaign:gv('campaign'),
      total_depth:num(gv('depth')),
      utm_easting:num(gv('utme')),utm_northing:num(gv('utmn')),
      utm_zone_number:zm?parseInt(zm[1]):null,utm_zone_letter:zm?zm[2]:'',
      latitude:num(gv('lat')),longitude:num(gv('lon')),
      coord_source:gv('utme')?'utm':'latlon',
      elevation:num(gv('elev')),slope_angle:num(gv('slope')),
      recorded_by:gv('recby'),surveyors:gv('surv'),date:gv('date'),
      pit_open_time:gv('po'),temp_time_start:gv('ts'),temp_time_end:gv('te'),
      gps_device:gv('gps'),
      gps_uncertainty:num(gv('gps-unc')),
      gps_uncertainty_unit:gv('gps-unc-unit'),
      wise_serial:gv('wise'),density_cutter:density_cutter,
      comments:gv('comments'),flags:gv('flags')||'None'},
    weather:{precip_rate:gr('pr'),precip_type:gr('pt'),sky:gr('sky'),wind:gr('wind')},
    ground:{condition:gr('gc'),roughness:gr('gr'),canopy:gr('tc'),
      snow_cover:gr('scc'),standing_water:gr('sw'),
      vegetation:veg,veg_height:num(gv('vh')),
      new_depth:num(gv('nd')),new_swe:num(gv('ns'))},
    temperature,density,lwc,stratigraphy,ssa,
    instruments,
    ssa_calibration:{
      instrument:gv('ssa-inst'),
      operator:ssaOp,
      spectralon:specStr?specStr.split(',').map(s=>parseFloat(s.trim())).filter(x=>!isNaN(x)):[],
      calib_values:calvStr?calvStr.split(',').map(s=>parseFloat(s.trim())).filter(x=>!isNaN(x)):[],
      measured_at:gv('ssa-cal-time'),notes:gv('ssa-notes')}
  };
}

// populate(): exact inverse of collect(). Used by pit loading AND draft
// restore, so both features share one battle-tested path. -------------------
function sv(id,v){const el=document.getElementById(id);if(el)el.value=(v===null||v===undefined)?'':v;}
function setRadio(name,val){
  document.querySelectorAll(`input[name="${name}"]`).forEach(r=>r.checked=(r.value===val));
}
function refreshTogs(){
  document.querySelectorAll('.toggles input').forEach(inp=>{
    inp.closest('.tog').classList.toggle('on',inp.checked);
  });
}
function clearTables(){['tb','db','lb','sb','ssab'].forEach(id=>document.getElementById(id).innerHTML='');}

function populate(p){
  if(!p||!p.meta)return;
  _restoring=true;
  try{
    const m=p.meta||{},wx=p.weather||{},g=p.ground||{};
    const locSel=document.getElementById('loc');
    const opts=[...locSel.options].map(o=>o.value||o.textContent);
    if(m.location&&opts.includes(m.location)){
      locSel.value=m.location;
      document.getElementById('loc-c').style.display='none';
      document.getElementById('loc-c').value='';
    }else if(m.location){
      locSel.value='__c';
      document.getElementById('loc-c').style.display='block';
      document.getElementById('loc-c').value=m.location;
    }else{
      locSel.value='';
      document.getElementById('loc-c').style.display='none';
    }
    sv('site',m.site);sv('date',m.date);sv('campaign',m.campaign||'SNEX25');
    if(m.pit_id&&m.pit_id!=='—')_pe=true;   // keep the stored ID, don't regenerate
    document.getElementById('pitid').textContent=m.pit_id||'—';
    document.getElementById('tb-pid').textContent=m.pit_id||'—';
    sv('depth',m.total_depth);sv('po',m.pit_open_time);sv('slope',m.slope_angle);
    sv('recby',m.recorded_by);sv('surv',m.surveyors);
    sv('gps',m.gps_device);sv('gps-unc',m.gps_uncertainty);
    sv('gps-unc-unit',m.gps_uncertainty_unit||'m');sv('wise',m.wise_serial);
    _cl=true;   // suppress the converters while restoring both coordinate sets
    sv('utme',m.utm_easting);sv('utmn',m.utm_northing);
    sv('utmz',(m.utm_zone_number?String(m.utm_zone_number):'')+(m.utm_zone_letter||''));
    sv('elev',m.elevation);sv('lat',m.latitude);sv('lon',m.longitude);
    setTimeout(()=>_cl=false,250);
    sv('flags',m.flags);sv('comments',m.comments);
    sv('ts',m.temp_time_start);sv('te',m.temp_time_end);
    const dc=m.density_cutter||'';
    document.getElementById('dc100').checked=/\b100\b/.test(dc);
    document.getElementById('dc250').checked=/\b250\b/.test(dc);
    document.getElementById('dc1000').checked=/\b1000\b/.test(dc);
    setRadio('pr',wx.precip_rate);setRadio('pt',wx.precip_type);
    setRadio('sky',wx.sky);setRadio('wind',wx.wind);
    setRadio('gc',g.condition);setRadio('gr',g.roughness);
    setRadio('tc',g.canopy);setRadio('scc',g.snow_cover);setRadio('sw',g.standing_water);
    const veg=g.vegetation||[];
    document.getElementById('vb').checked=veg.includes('bare');
    document.getElementById('vg').checked=veg.includes('grass');
    document.getElementById('vs').checked=veg.includes('shrub');
    document.getElementById('vd').checked=veg.includes('deadfall');
    sv('vh',g.veg_height);sv('nd',g.new_depth);sv('ns',g.new_swe);
    clearTables();
    (p.temperature||[]).forEach(r=>{
      const tr=addRow('t',false);const ins=tr.querySelectorAll('input');
      ins[0].value=(r.height===null||r.height===undefined)?'':r.height;
      ins[1].value=(r.temp===null||r.temp===undefined)?'':r.temp;
    });
    (p.density||[]).forEach(r=>{
      const tr=addRow('d',false);const ins=tr.querySelectorAll('input');
      [['top',0],['bottom',1],['a',2],['b',3],['c',4]].forEach(([k,i])=>{
        ins[i].value=(r[k]===null||r[k]===undefined)?'':r[k];});
      calcAvg(tr);
    });
    (p.lwc||[]).forEach(r=>{
      const tr=addRow('l',false);const ins=tr.querySelectorAll('input');
      [['top',0],['bottom',1],['a',2],['b',3]].forEach(([k,i])=>{
        ins[i].value=(r[k]===null||r[k]===undefined)?'':r[k];});
    });
    (p.stratigraphy||[]).forEach(r=>{
      const tr=addRow('s',false);
      const ins=tr.querySelectorAll('input');const sels=tr.querySelectorAll('select');
      [['top',0],['bottom',1],['gmin',2],['gmax',3],['gavg',4]].forEach(([k,i])=>{
        ins[i].value=(r[k]===null||r[k]===undefined)?'':r[k];});
      if(r.gtype)sels[0].value=r.gtype;
      if(r.hardness)sels[1].value=r.hardness;
      if(r.wetness)sels[2].value=r.wetness;
      ins[5].value=r.comments||'';
    });
    (p.ssa||[]).forEach(r=>{
      const tr=addRow('sa',false);
      const ins=tr.querySelectorAll('input');const sels=tr.querySelectorAll('select');
      [['height',0],['signal',1],['reflectance',2],['ssa',3]].forEach(([k,i])=>{
        ins[i].value=(r[k]===null||r[k]===undefined)?'':r[k];});
      if(r.grain_type)sels[0].value=r.grain_type;
      ins[4].value=r.comments||'';
    });
    (p.instruments||[]).forEach((it,i)=>{
      const snEl=document.getElementById('sn'+i);
      if(snEl)snEl.value=(it.sn&&it.sn!=='—')?it.sn:'';
      if(document.getElementById('yy'+i))setyn(i,it.used==='Y'?'Y':'N');
    });
    const sc=p.ssa_calibration||{};
    sv('ssa-inst',sc.instrument);sv('ssa-cal-time',sc.measured_at);
    sv('ssa-operator',sc.operator);
    sv('ssa-spec',(sc.spectralon||[]).join(','));
    sv('ssa-calv',(sc.calib_values||[]).join(','));
    sv('ssa-notes',sc.notes);
    refreshTogs();
    ['t','d','l','s','sa'].forEach(cnt);
  }finally{
    _restoring=false;
  }
  tick();
}

function validate(){
  const p=collect();const e=[];
  if(!p.meta.location)e.push('Location');
  if(!p.meta.pit_id||p.meta.pit_id==='—')e.push('Pit ID');
  if(!p.meta.recorded_by)e.push('Recorded by');
  if(!p.meta.surveyors)e.push('Field observers');
  if(!p.meta.date)e.push('Date');
  // HHMM times now block save when malformed instead of just tinting red
  [['po','Pit open time'],['ts','Profile start'],['te','Profile end'],
   ['ssa-cal-time','SSA calibration time']].forEach(([id,lbl])=>{
    const v=gv(id);
    if(v&&!goodTime(v))e.push(lbl+' (HHMM)');
  });
  return{p,e};
}

function setst(msg,cls){const el=document.getElementById('tb-st');el.textContent=msg;el.className='tb-status'+(cls?' '+cls:' unsaved');}

function post(path,payload){
  const ctrl=new AbortController();
  const tid=setTimeout(()=>ctrl.abort(),15000);
  return fetch(API+path,{
    method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(payload),signal:ctrl.signal
  }).finally(()=>clearTimeout(tid));
}
function fetchErr(err){
  return err.name==='AbortError'
    ? 'no response — is the app running?'
    : 'error: '+err.message;
}

// Draft autosave: the form state survives refreshes/crashes ----------
let _draftT=null;
function scheduleDraft(){
  if(_restoring)return;
  clearTimeout(_draftT);
  _draftT=setTimeout(()=>{
    try{localStorage.setItem('cp-draft',JSON.stringify(collect()));}catch(e){}
  },500);
}
function restoreDraft(){
  try{
    const d=localStorage.getItem('cp-draft');
    if(!d)return;
    const p=JSON.parse(d);
    if(!p||!p.meta)return;
    const meaningful=(p.meta.pit_id&&p.meta.pit_id!=='—')||p.meta.recorded_by||
      (p.temperature&&p.temperature.length)||(p.stratigraphy&&p.stratigraphy.length);
    if(!meaningful)return;
    populate(p);
    setst('● draft restored — not saved','unsaved');
  }catch(e){}
}
function newPit(){
  // State-aware warning: name the actual risk if the pit isn't archived.
  const archived = (_loaded_pid!==null && _loaded_pid===document.getElementById('pitid').textContent.trim());
  const msg = archived
    ? 'Start a new pit? (The current pit is archived.)'
    : 'Start a new pit?\n\nThis pit is NOT archived — it is not in the database. '
      + 'Anything you have not downloaded or archived will be lost.';
  if(!confirm(msg))return;
  try{localStorage.removeItem('cp-draft');}catch(e){}
  location.reload();
}

// Download: pure file delivery. Exports the CSVs to the user's browser and
// touches nothing server-side — no database write. A team that only wants CSVs
// is never forced into the database.
function doDownload(){
  const{p,e}=validate();
  if(e.length){setst('fill required fields first: '+e.join(', '),'err');return;}
  setst('exporting…','');
  post('/api/download',p)
    .then(r=>r.json())
    .then(r=>{
      if(!r.ok){setst('error: '+r.msg,'err');return;}
      downloadZip(r.zipname,r.zip);
      // NOTE: downloading does NOT change archived state — files ≠ recorded.
      setst('● downloaded (not archived) · '+r.zipname,'ok-dl');
    })
    .catch(err=>setst(fetchErr(err),'err'));
}

// Archive: the deliberate "record this pit" action. Saves to the database AND
// writes the CSVs to the server's configured export folder (CRYOPIT_EXPORT_DIR).
function doArchive(){
  const{p,e}=validate();
  if(e.length){setst('missing: '+e.join(', '),'err');return;}
  _archive(p,_loaded_pid!==null&&_loaded_pid===p.meta.pit_id);
}
function _archive(p,overwrite){
  setst('archiving…','');
  post('/api/archive',{...p,overwrite:!!overwrite})
    .then(r=>r.json())
    .then(r=>{
      if(r.exists){
        if(confirm('Pit "'+p.meta.pit_id+'" already exists in the database.\nOverwrite it? The previous version will be replaced.')){
          _archive(p,true);
        } else setst('● not archived — pit exists','unsaved');
        return;
      }
      if(!r.ok){setst('● error: '+r.msg,'err');return;}
      _loaded_pid=r.pit_id;          // archived → overwrite implied on re-archive
      loadSavedPits();
      const where = r.folder_count!=null
        ? ' · '+r.folder_count+' files → '+shortPath(r.folder)
        : '';
      setst('● archived · '+r.pit_id+where,'ok');
    })
    .catch(err=>setst(fetchErr(err),'err'));
}

function downloadZip(zipname,zipb64){
  // base64 -> bytes -> one Blob -> one download
  const bin=atob(zipb64);
  const len=bin.length;
  const bytes=new Uint8Array(len);
  for(let i=0;i<len;i++)bytes[i]=bin.charCodeAt(i);
  const blob=new Blob([bytes],{type:'application/zip'});
  const url=URL.createObjectURL(blob);
  const a=document.createElement('a');
  a.href=url;a.download=zipname;
  document.body.appendChild(a);a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

function shortPath(pth){
  if(!pth)return'';
  const parts=pth.split(/[\\/]/).filter(Boolean);
  return parts.length<=2?pth:'…/'+parts.slice(-2).join('/');
}

// Saved pits list + loading -------------------------------------------
function loadSavedPits(){
  fetch(API+'/api/pits')
    .then(r=>r.json())
    .then(r=>{
      const el=document.getElementById('saved-pits-list');
      el.innerHTML='';
      if(!r.pits||r.pits.length===0){
        el.innerHTML='<span class="nav-foot-empty">none yet</span>';return;
      }
      r.pits.forEach(p=>{
        // built with DOM APIs (not innerHTML interpolation) so a pit_id
        // containing quotes can't break out of an attribute
        const a=document.createElement('a');
        a.className='pit-entry';a.title='Load '+p.pit_id;
        a.appendChild(document.createTextNode(p.pit_id));
        const sp=document.createElement('span');
        sp.className='pit-date';sp.textContent=p.date||'';
        a.appendChild(sp);
        a.addEventListener('click',()=>loadPit(p.pit_id));
        el.appendChild(a);
      });
    })
    .catch(()=>{});
}

function formDirty(){
  const pid=document.getElementById('pitid').textContent.trim();
  return !!((pid&&pid!=='—')||gv('recby').trim()||
    document.getElementById('tb').children.length>0||
    document.getElementById('sb').children.length>0);
}
function loadPit(pid){
  if(formDirty()&&!confirm('Replace the current form contents with pit "'+pid+'"?'))return;
  fetch(API+'/api/load/'+encodeURIComponent(pid))
    .then(r=>r.json())
    .then(r=>{
      if(!r.ok){setst('load error: '+r.msg,'err');return;}
      populate(r.pit);
      _loaded_pid=pid;
      scheduleDraft();
      setst('● loaded · '+pid,'ok');
    })
    .catch(err=>setst(fetchErr(err),'err'));
}

// Profile plot (height above ground: HS at top, 0 at bottom) ----------
const HARD_SCALE={'F':1,'4F':2,'1F':3,'P':4,'K':5,'I':6};
const GRAIN_COLOR={
  PP:'#3bc',PPsd:'#4cd',PPgp:'#5ad',PPrm:'#2ab',
  MM:'#9ad',
  DF:'#6ad',
  RG:'#7c7',RGwp:'#8d8',RGxf:'#6b6',RGlr:'#9e9',
  FC:'#fb4',FCsf:'#fd8',FCxr:'#fa3',FCso:'#fc6',
  DH:'#f84',DHcp:'#f96',DHpr:'#f73',DHla:'#fa7',DHxr:'#e63',
  SH:'#e44',SHxr:'#f66',
  MF:'#c9e',MFcl:'#d9f',MFsl:'#b8e',MFcr:'#a8d',
  IF:'#9cf',IFsc:'#adf',IFrc:'#8be',IFbi:'#7ad'
};
function drawProfile(){
  const p=collect();
  const wrap=document.getElementById('profile-wrap');
  const HS=p.meta.total_depth||0;
  const strat=(p.stratigraphy||[]).filter(l=>l.top!=null&&l.bottom!=null);
  if(!HS||strat.length===0){
    wrap.innerHTML='<p style="font-family:var(--mono);font-size:12px;color:var(--ink3);padding:24px;text-align:center">'
      +'Add Total depth (§1) and at least one stratigraphy layer (§7), then redraw.</p>';
    return;
  }
  const W=720,padT=28,padB=28,padL=54,plotH=440;
  const hardW=150, grainW=46, gapA=26, densW=150;
  const xHard=padL, xGrain=xHard+hardW+6, xDens=xGrain+grainW+gapA;
  const Ht=padT+plotH+padB;
  const d2y=h=>padT+(1-(h/HS))*plotH;       // h = height above ground
  const dens=(p.density||[]).filter(d=>d.top!=null);
  let dmin=Infinity,dmax=-Infinity;
  dens.forEach(d=>{const v=[d.a,d.b,d.c].filter(x=>x!=null);v.forEach(x=>{dmin=Math.min(dmin,x);dmax=Math.max(dmax,x);});});
  if(!isFinite(dmin)){dmin=0;dmax=500;} if(dmin===dmax){dmin-=50;dmax+=50;}
  const dn2x=v=>xDens+((v-dmin)/(dmax-dmin))*densW;
  const temp=(p.temperature||[]).filter(t=>t.height!=null&&t.temp!=null);
  let tmin=Infinity,tmax=-Infinity;
  temp.forEach(t=>{tmin=Math.min(tmin,t.temp);tmax=Math.max(tmax,t.temp);});
  if(!isFinite(tmin)){tmin=-15;tmax=0;} if(tmin===tmax){tmin-=2;tmax+=2;}
  const tn2x=v=>xDens+((v-tmin)/(tmax-tmin))*densW;

  const css=getComputedStyle(document.documentElement);
  const ink=css.getPropertyValue('--ink').trim()||'#111';
  const ink3=css.getPropertyValue('--ink3').trim()||'#999';
  const rule=css.getPropertyValue('--rule').trim()||'#e0e2e6';
  const red=css.getPropertyValue('--red').trim()||'#d0021b';

  let s=`<svg viewBox="0 0 ${W} ${Ht}" xmlns="http://www.w3.org/2000/svg" style="width:100%;max-width:${W}px;font-family:var(--mono)">`;
  const ticks=5;
  for(let i=0;i<=ticks;i++){
    const h=HS*(1-i/ticks);
    const y=d2y(h);
    s+=`<line x1="${padL}" y1="${y}" x2="${xDens+densW}" y2="${y}" stroke="${rule}" stroke-width="1"/>`;
    s+=`<text x="${padL-6}" y="${y+3}" text-anchor="end" font-size="9" fill="${ink3}">${Math.round(h)}</text>`;
  }
  s+=`<text x="14" y="${padT+plotH/2}" font-size="9" fill="${ink3}" transform="rotate(-90 14 ${padT+plotH/2})" text-anchor="middle">HEIGHT ABOVE GROUND (cm)</text>`;

  strat.forEach(l=>{
    const yTop=d2y(l.top), yBot=d2y(l.bottom);
    const hh=HARD_SCALE[l.hardness]||1;
    const bw=(hh/6)*hardW;
    const col=GRAIN_COLOR[l.gtype]||ink3;
    s+=`<rect x="${xHard}" y="${yTop}" width="${bw}" height="${Math.max(1,yBot-yTop)}" fill="${col}" fill-opacity="0.55" stroke="${ink}" stroke-width="0.6"/>`;
    s+=`<rect x="${xGrain}" y="${yTop}" width="${grainW}" height="${Math.max(1,yBot-yTop)}" fill="${col}" fill-opacity="0.85" stroke="${rule}" stroke-width="0.5"/>`;
    if(yBot-yTop>11){
      s+=`<text x="${xGrain+grainW/2}" y="${(yTop+yBot)/2+3}" text-anchor="middle" font-size="8" fill="${ink}">${l.gtype||''}</text>`;
    }
  });
  ['F','4F','1F','P','K','I'].forEach((h,i)=>{
    const x=xHard+((i+1)/6)*hardW;
    s+=`<text x="${x}" y="${padT-8}" text-anchor="middle" font-size="8" fill="${ink3}">${h}</text>`;
  });
  s+=`<text x="${xHard}" y="${Ht-8}" font-size="9" fill="${ink3}">HARDNESS →</text>`;
  s+=`<text x="${xGrain}" y="${padT-8}" font-size="8" fill="${ink3}">GRAIN</text>`;

  if(dens.length){
    let pts=[];
    dens.slice().sort((a,b)=>b.top-a.top).forEach(d=>{
      const v=[d.a,d.b,d.c].filter(x=>x!=null);
      if(!v.length)return;
      const avg=v.reduce((a,b)=>a+b)/v.length;
      const bot=(d.bottom!=null)?d.bottom:d.top;
      const mid=(d.top+bot)/2;
      pts.push([dn2x(avg),d2y(mid)]);
    });
    if(pts.length){
      s+=`<polyline points="${pts.map(q=>q.join(',')).join(' ')}" fill="none" stroke="${ink}" stroke-width="1.5"/>`;
      pts.forEach(q=>s+=`<circle cx="${q[0]}" cy="${q[1]}" r="2" fill="${ink}"/>`);
    }
    s+=`<text x="${xDens}" y="${padT-8}" font-size="8" fill="${ink3}">DENSITY ${Math.round(dmin)}–${Math.round(dmax)} kg/m³</text>`;
  }
  if(temp.length){
    let tp=temp.slice().sort((a,b)=>b.height-a.height).map(t=>[tn2x(t.temp),d2y(t.height)]);
    s+=`<polyline points="${tp.map(q=>q.join(',')).join(' ')}" fill="none" stroke="${red}" stroke-width="1.5" stroke-dasharray="3,2"/>`;
    tp.forEach(q=>s+=`<circle cx="${q[0]}" cy="${q[1]}" r="2" fill="${red}"/>`);
    s+=`<text x="${xDens}" y="${Ht-8}" font-size="8" fill="${red}">TEMP ${tmin.toFixed(1)}–${tmax.toFixed(1)}°C (dashed)</text>`;
  }
  s+=`</svg>`;
  wrap.innerHTML=s;
}

// Live core rail — miniature of the snowpack, redrawn as you type ------
let _miniT=null;
function scheduleMini(){clearTimeout(_miniT);_miniT=setTimeout(drawMini,300);}
function drawMini(){
  const el=document.getElementById('mini-core');
  if(!el)return;
  const p=collect();
  const HS=p.meta.total_depth||0;
  const strat=(p.stratigraphy||[]).filter(l=>l.top!=null&&l.bottom!=null);
  document.getElementById('mc-hs').textContent=HS?HS+' cm':'—';
  document.getElementById('mc-lay').textContent=strat.length;
  // Thickness-weighted bulk density and SWE, computed ONLY over intervals that
  // actually have a density value. ρ bulk = Σ(ρ_i·t_i)/Σ(t_i) (a simple mean of
  // interval densities would mis-weight unequal layers). SWE = Σ(ρ_i·t_i)/ρ_water,
  // with ρ_water=1000 kg/m³, thickness in m → SWE in mm. Coverage reports the
  // measured span vs HS, since density often stops short of the ground
  // (vegetation, basal ice) — SWE is always for the verified column.
  let sumRT=0, sumT=0;   // Σ(ρ·t) and Σ(t), t in cm
  (p.density||[]).forEach(d=>{
    if(d.top==null||d.bottom==null)return;
    const v=[d.a,d.b,d.c].filter(x=>x!=null);
    if(!v.length)return;
    const rho=v.reduce((a,b)=>a+b)/v.length;
    const t=Math.abs(d.top-d.bottom);
    if(t<=0)return;
    sumRT+=rho*t; sumT+=t;
  });
  const bulk = sumT>0 ? sumRT/sumT : null;                 // kg/m³ (thickness-weighted)
  // SWE(mm) = Σ(ρ_i[kg/m³]·t_i[m]) / ρ_water[1000] × 1000[mm/m].  t in cm → t_m=t/100,
  // so SWE_mm = Σ(ρ·t_cm/100)/1000×1000 = Σ(ρ·t_cm)/100 = sumRT/100.
  const swe_mm = sumT>0 ? sumRT/100 : null;
  document.getElementById('mc-den').textContent = bulk!=null ? Math.round(bulk)+' kg/m³' : '—';
  document.getElementById('mc-swe').textContent = swe_mm!=null ? Math.round(swe_mm)+' mm' : '—';
  // coverage
  if(sumT>0 && HS){
    const full = sumT>=HS-0.5;
    document.getElementById('mc-cov-lbl').textContent = full ? 'full depth' : 'density covers';
    document.getElementById('mc-cov').textContent = full ? '' : Math.round(sumT)+'/'+HS+' cm';
  } else {
    document.getElementById('mc-cov-lbl').textContent='';
    document.getElementById('mc-cov').textContent='';
  }
  const temps=(p.temperature||[]).map(t=>t.temp).filter(t=>t!=null);
  document.getElementById('mc-tmin').textContent=
    temps.length?Math.min(...temps).toFixed(1)+' °C':'—';
  const css=getComputedStyle(document.documentElement);
  const ink3=css.getPropertyValue('--ink3').trim()||'#8fa1b3';
  const rule2=css.getPropertyValue('--rule2').trim()||'#c3d0dd';
  const Wm=84,Hm=252,cx=22,cw=40,pad=10,col=Hm-2*pad;
  if(!HS||!strat.length){
    el.innerHTML=`<svg width="${Wm}" height="${Hm}" xmlns="http://www.w3.org/2000/svg">`+
      `<rect x="${cx}" y="${pad}" width="${cw}" height="${col}" fill="none" stroke="${rule2}" stroke-width="1" stroke-dasharray="3,3" rx="2"/>`+
      `<text x="${cx+cw/2}" y="${Hm/2}" text-anchor="middle" font-size="8" font-family="var(--mono)" fill="${ink3}">empty</text></svg>`;
    return;
  }
  const y=h=>pad+(1-(h/HS))*col;
  let s2=`<svg width="${Wm}" height="${Hm}" xmlns="http://www.w3.org/2000/svg" style="font-family:var(--mono)">`;
  s2+=`<text x="${cx+cw/2}" y="${pad-2}" text-anchor="middle" font-size="7" fill="${ink3}">${HS}</text>`;
  s2+=`<text x="${cx+cw/2}" y="${Hm-1}" text-anchor="middle" font-size="7" fill="${ink3}">0</text>`;
  strat.forEach(l=>{
    const yT=y(Math.min(l.top,HS)),yB=y(Math.max(l.bottom,0));
    const c=GRAIN_COLOR[l.gtype]||ink3;
    s2+=`<rect x="${cx}" y="${yT}" width="${cw}" height="${Math.max(1,yB-yT)}" fill="${c}" fill-opacity="0.8" stroke="${rule2}" stroke-width="0.5"/>`;
    if(yB-yT>10)s2+=`<text x="${cx+cw+4}" y="${(yT+yB)/2+2}" font-size="7" fill="${ink3}">${l.gtype||''}</text>`;
  });
  s2+=`<rect x="${cx}" y="${y(HS)}" width="${cw}" height="${y(0)-y(HS)}" fill="none" stroke="${rule2}" stroke-width="1" rx="2"/>`;
  s2+=`</svg>`;
  el.innerHTML=s2;
}

document.querySelectorAll('.toggles input').forEach(inp=>{
  inp.addEventListener('change',()=>{
    if(inp.name){document.querySelectorAll(`input[name="${inp.name}"]`).forEach(r=>r.closest('.tog').classList.toggle('on',r.checked));}
    else{inp.closest('.tog').classList.toggle('on',inp.checked);}
    tick();
  });
});

buildInst(); tick(); loadSavedPits(); restoreDraft(); drawMini();
</script>
</body></html>"""

# -----------------------------------------------------------------------------
# FLASK APP — one origin for the form and the API
# -----------------------------------------------------------------------------
app = Flask(__name__)
_FORM_HTML = None  # rendered once at startup

def _render_form():
    """Inject startup-time values into the form.

    Brand badge gets a short label only — the full institution string is wide,
    uppercase, letter-spaced and would overflow the bar; the full name lives in
    the page <title> instead.
    """
    short = INSTITUTION.split("·")[0].split("-")[0].strip()[:18] or "CryoGARS"
    html = FORM.replace("__PAGE_TITLE__", f"{APP_TITLE} · {INSTITUTION}")
    html = html.replace(">CryoGARS</span>", f">{short}</span>")
    return html

def _json_or_400():
    """All POST routes require Content-Type: application/json.

    Besides being correct, this is the CSRF defense: a cross-origin page can
    fire 'simple' POSTs (form-encoded / text-plain) at localhost without
    permission, but a JSON POST triggers a CORS preflight — and since we send
    no CORS headers, the browser blocks it. Only our own page reaches these
    routes.
    """
    if not request.is_json:
        abort(400, "Expected application/json")
    return request.get_json()

@app.get("/")
def index():
    return _FORM_HTML

@app.get("/api/pits")
def api_pits():
    return jsonify(ok=True, pits=list_pits())

@app.get("/api/load/<path:pit_id>")
def api_load(pit_id):
    pit, err = load_pit(pit_id)
    if pit is None:
        return jsonify(ok=False, msg=err)
    return jsonify(ok=True, pit=pit)

@app.post("/api/download")
def api_download():
    """Build the six CSVs directly from the submitted form payload and return
    them as one base64 ZIP. Does NOT touch the database — pure file delivery."""
    payload = _json_or_400()
    pit_id   = (payload.get("meta") or {}).get("pit_id") or "pit"
    campaign = (payload.get("meta") or {}).get("campaign") or CAMPAIGN
    csvs = export_from_payload(payload)
    zipname, zipb64 = zip_csvs(csvs, pit_id, campaign)
    return jsonify(ok=True, pit_id=pit_id, zipname=zipname, zip=zipb64)

@app.post("/api/archive")
def api_archive():
    """The 'record this pit' action: save to the database AND write the CSVs to
    the server's configured export folder (CRYOPIT_EXPORT_DIR)."""
    payload = _json_or_400()
    status, info = save_pit(payload)
    if status == "exists":
        return jsonify(ok=False, exists=True, pit_id=info)
    if status == "error":
        return jsonify(ok=False, msg=info), 500
    pit_id = payload["meta"]["pit_id"]
    csvs   = export_all(pit_id)
    # Always write to the server-configured export dir (not a user-typed path).
    fok, finfo = save_csvs_to_folder(csvs, EXPORT_DIR)
    if fok:
        return jsonify(ok=True, pit_id=pit_id,
                       folder=finfo["folder"], folder_count=finfo["count"])
    # DB save succeeded but folder write failed — report saved, flag the folder.
    return jsonify(ok=True, pit_id=pit_id, folder=None, folder_count=None,
                   folder_error=finfo)

# -----------------------------------------------------------------------------
# MAIN
# -----------------------------------------------------------------------------
def main():
    global _FORM_HTML
    init_db()
    _FORM_HTML = _render_form()
    print(f"{APP_TITLE} · {INSTITUTION}")
    print(f"  database : {os.path.abspath(DB_PATH)}")
    print(f"  exports  : {os.path.abspath(EXPORT_DIR)} (default folder sink)")
    print(f"  open     : http://127.0.0.1:{PORT}")
    # Flask's dev server is threaded by default; fine for a lab tool. For a
    # shared deployment, run behind waitress/gunicorn instead:
    #   waitress-serve --port=8502 cryopit:make_app
    app.run(host="127.0.0.1", port=PORT, debug=False)

def make_app():
    """WSGI entry point for production servers (waitress, gunicorn)."""
    global _FORM_HTML
    init_db()
    _FORM_HTML = _render_form()
    return app

if __name__ == "__main__":
    main()