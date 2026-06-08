"""
CryoPit Snow Pit Logger v2.0
Design B — Clinical white · Braun/laboratory aesthetic
Streamlit shell · injected HTML form · local HTTP save endpoint
SnowEx-compatible CSV export · UTM ↔ lat/lon · SQLite backend
"""

import streamlit as st
import streamlit.components.v1 as components
import sqlite3
import pandas as pd
import json, io, csv as csvlib, math, os, threading, zipfile, base64
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from pyproj import Transformer

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────
DB_PATH     = os.getenv("CRYOPIT_DB_PATH",   "cryopit.db")
INSTITUTION = os.getenv("CRYOPIT_INSTITUTION","CryoGARS · Boise State University")
CAMPAIGN    = os.getenv("CRYOPIT_CAMPAIGN",   "SNEX25")
APP_TITLE   = os.getenv("CRYOPIT_APP_TITLE",  "CryoPit")
API_PORT    = int(os.getenv("CRYOPIT_API_PORT","8502"))
# Default destination for server-side CSV writes. This path is resolved by the
# PYTHON process — i.e. on whatever machine runs the app. Locally that's your
# laptop; once deployed it's the server. An institution can point this at a
# mounted Drive, an S3-backed mount, or a synced repo directory.
EXPORT_DIR  = os.getenv("CRYOPIT_EXPORT_DIR", "exports")
NO_DATA     = -9999

# ─────────────────────────────────────────────────────────────────────────────
# PAGE CONFIG
# ─────────────────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title=f"{APP_TITLE} · {INSTITUTION}",
    page_icon="❄",
    layout="wide",
    initial_sidebar_state="collapsed",
)
st.markdown("""
<style>
#MainMenu,header,footer,[data-testid="stDecoration"]{display:none!important}

/* lock every scroll container in the chain */
html,body{overflow:hidden!important;height:100vh!important}
[data-testid="stApp"],
[data-testid="stAppViewContainer"],
[data-testid="stMain"],
section.main{
  overflow:hidden!important;height:100vh!important;background:#fff
}
.block-container,
[data-testid="block-container"],
[data-testid="stMainBlockContainer"]{
  padding:0!important;margin:0!important;max-width:100%!important;
  height:100vh!important;overflow:hidden!important
}

/* clamp the component wrapper AND the iframe to the same height
   so there is no leftover region to scroll into */
[data-testid="stCustomComponentV1"],
.element-container:has(iframe),
.stCustomComponentV1{
  height:100vh!important;overflow:hidden!important
}
iframe{border:none!important;display:block;width:100%!important;height:100vh!important}
</style>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# COORDINATES
# ─────────────────────────────────────────────────────────────────────────────
def utm_to_latlon(e, n, zone_num, zone_let):
    hemi = "S" if zone_let.upper() < "N" else "N"
    epsg = 32600 + zone_num if hemi == "N" else 32700 + zone_num
    t = Transformer.from_crs(f"EPSG:{epsg}", "EPSG:4326", always_xy=True)
    lon, lat = t.transform(e, n)
    return round(lat, 6), round(lon, 6)

def latlon_to_utm(lat, lon):
    zone_num = int((lon + 180) / 6) + 1
    zone_let = "N" if lat >= 0 else "S"
    hemi     = "S" if lat < 0 else "N"
    epsg     = 32600 + zone_num if hemi == "N" else 32700 + zone_num
    t = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)
    e, n = t.transform(lon, lat)
    return round(e, 1), round(n, 1), zone_num, zone_let

# ─────────────────────────────────────────────────────────────────────────────
# DATABASE
# ─────────────────────────────────────────────────────────────────────────────
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
    pit_id TEXT NOT NULL UNIQUE, name TEXT,
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
    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.commit()
    conn.close()

def _migrate(conn):
    """Add columns introduced after a DB was first created.

    CREATE TABLE IF NOT EXISTS won't alter an existing table, so new columns
    must be added explicitly. Each ADD COLUMN is wrapped — if the column already
    exists SQLite raises OperationalError, which we ignore. This is idempotent:
    safe to run on a brand-new DB (columns already there) or an old one (adds them).
    Existing rows get NULL for the new columns, so nothing is lost.
    """
    adds = [
        ("sites", "gps_uncertainty REAL"),
        ("sites", "gps_uncertainty_unit TEXT"),
        ("ssa_calibration", "operator TEXT"),
    ]
    for table, coldef in adds:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {coldef}")
        except sqlite3.OperationalError:
            pass  # column already exists

def get_conn():
    return sqlite3.connect(DB_PATH)

def save_pit(payload):
    conn = get_conn()
    try:
        with conn:
            m   = payload["meta"]
            wx  = payload.get("weather", {})
            gnd = payload.get("ground", {})

            def get_or_create_observer(name):
                name = name.strip()
                if not name: return None
                row = conn.execute("SELECT id FROM observers WHERE name=?", (name,)).fetchone()
                if row: return row[0]
                conn.execute("INSERT INTO observers(name) VALUES(?)", (name,))
                return conn.execute("SELECT last_insert_rowid()").fetchone()[0]

            recorded_by_id = get_or_create_observer(m.get("recorded_by",""))
            surveyor_ids   = [get_or_create_observer(s.strip())
                              for s in m.get("surveyors","").split(",") if s.strip()]

            camp_name = m.get("campaign", CAMPAIGN) or CAMPAIGN
            camp_row  = conn.execute("SELECT id FROM campaigns WHERE name=?", (camp_name,)).fetchone()
            if camp_row:
                camp_id = camp_row[0]
            else:
                conn.execute("INSERT INTO campaigns(name) VALUES(?)", (camp_name,))
                camp_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

            pid = m["pit_id"]
            conn.execute("DELETE FROM layers WHERE site_id=(SELECT id FROM sites WHERE pit_id=?)", (pid,))
            conn.execute("DELETE FROM site_observers WHERE site_id=(SELECT id FROM sites WHERE pit_id=?)", (pid,))
            conn.execute("DELETE FROM site_instruments WHERE site_id=(SELECT id FROM sites WHERE pit_id=?)", (pid,))
            conn.execute("DELETE FROM ssa_calibration WHERE site_id=(SELECT id FROM sites WHERE pit_id=?)", (pid,))
            conn.execute("DELETE FROM sites WHERE pit_id=?", (pid,))

            conn.execute("""INSERT INTO sites(
                campaign_id,pit_id,name,date,pit_open_time,
                temp_time_start,temp_time_end,
                utm_easting,utm_northing,utm_zone_number,utm_zone_letter,
                latitude,longitude,coord_source,elevation,
                total_depth,slope_angle,
                precip_rate,precip_type,sky_condition,wind,
                ground_condition,ground_roughness,vegetation,vegetation_height,
                tree_canopy,snow_cover_condition,standing_water,
                wise_serial,gps_device,gps_uncertainty,gps_uncertainty_unit,density_cutter,
                new_snow_depth,new_snow_swe,new_snow_density,
                recorded_by,comments,flags)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (camp_id, pid, m.get("site",""), m["date"],
                 m.get("pit_open_time",""), m.get("temp_time_start",""), m.get("temp_time_end",""),
                 m.get("utm_easting"), m.get("utm_northing"),
                 m.get("utm_zone_number"), m.get("utm_zone_letter",""),
                 m.get("latitude"), m.get("longitude"), m.get("coord_source","utm"),
                 m.get("elevation"), m.get("total_depth",0), m.get("slope_angle",0),
                 wx.get("precip_rate",""), wx.get("precip_type",""),
                 wx.get("sky",""), wx.get("wind",""),
                 gnd.get("condition",""), gnd.get("roughness",""),
                 json.dumps(gnd.get("vegetation",[])), gnd.get("veg_height",0),
                 gnd.get("canopy",""), gnd.get("snow_cover",""), gnd.get("standing_water",""),
                 m.get("wise_serial",""), m.get("gps_device",""),
                 m.get("gps_uncertainty"), m.get("gps_uncertainty_unit",""),
                 m.get("density_cutter",""),
                 gnd.get("new_depth",0), gnd.get("new_swe",0), gnd.get("new_density",0.0),
                 recorded_by_id, m.get("comments",""), "None"))

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

            total_depth = m.get("total_depth", 0) or 0

            # Temperature
            tid = mt_id("temperature")
            for r in payload.get("temperature", []):
                conn.execute("""INSERT INTO layers(site_id,measurement_type_id,
                    top_cm,depth_from_surface,value,time_recorded)
                    VALUES(?,?,?,?,?,?)""",
                    (site_id, tid, r.get("height"), total_depth - r.get("height",0),
                     r.get("temp"), r.get("time","")))

            # Density
            did = mt_id("density")
            for r in payload.get("density", []):
                vals = [v for v in [r.get("a"), r.get("b"), r.get("c")] if v]
                avg  = round(sum(vals)/len(vals)) if vals else None
                conn.execute("""INSERT INTO layers(site_id,measurement_type_id,
                    top_cm,bottom_cm,depth_from_surface,value,value_b,value_c,value_avg)
                    VALUES(?,?,?,?,?,?,?,?,?)""",
                    (site_id, did, r.get("top"), r.get("bottom"),
                     total_depth - r.get("top",0),
                     r.get("a"), r.get("b"), r.get("c"), avg))

            # LWC
            lid  = mt_id("permittivity")
            sfid = inst_id("Snow Fork")
            for r in payload.get("lwc", []):
                conn.execute("""INSERT INTO layers(site_id,measurement_type_id,instrument_id,
                    top_cm,bottom_cm,depth_from_surface,value,value_b)
                    VALUES(?,?,?,?,?,?,?,?)""",
                    (site_id, lid, sfid, r.get("top"), r.get("bottom"),
                     total_depth - r.get("top",0),
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
                     total_depth - r.get("top",0),
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
            ssa_inst_name = payload.get("ssa_calibration",{}).get("instrument","IceCube")
            iceid = inst_id(ssa_inst_name) or inst_id("IceCube")
            for r in payload.get("ssa", []):
                conn.execute("""INSERT INTO layers(site_id,measurement_type_id,instrument_id,
                    top_cm,depth_from_surface,value,value_b,value_c,grain_type,comments)
                    VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (site_id, ssaid, iceid,
                     r.get("height"), total_depth - r.get("height",0),
                     r.get("ssa"), r.get("reflectance"), r.get("signal"),
                     r.get("grain_type",""), r.get("comments","")))

            ssa_cal = payload.get("ssa_calibration", {})
            if ssa_cal.get("spectralon") or ssa_cal.get("calib_values") or ssa_cal.get("operator"):
                conn.execute("""INSERT INTO ssa_calibration(
                    site_id,instrument_id,spectralon,calib_values,measured_at,operator,notes)
                    VALUES(?,?,?,?,?,?,?)""",
                    (site_id, iceid,
                     json.dumps(ssa_cal.get("spectralon",[])),
                     json.dumps(ssa_cal.get("calib_values",[])),
                     ssa_cal.get("measured_at",""),
                     ssa_cal.get("operator",""), ssa_cal.get("notes","")))

        return True, pid
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()

def get_pit_list():
    conn = get_conn()
    try:
        df = pd.read_sql_query(
            "SELECT pit_id,date,name,comments FROM sites ORDER BY created_at DESC", conn)
    except:
        df = pd.DataFrame()
    conn.close()
    return df

# ─────────────────────────────────────────────────────────────────────────────
# CSV EXPORT — SnowEx format
# ─────────────────────────────────────────────────────────────────────────────
def _c(v):
    if v is None: return NO_DATA
    if isinstance(v, float) and math.isnan(v): return NO_DATA
    if isinstance(v, str) and v.strip() == "": return NO_DATA
    return v

def _hdr(p, extra=None):
    rows = [
        ["# Location",                  _c(p.get("location", p.get("name")))],
        ["# Site",                      _c(p.get("name"))],
        ["# PitID",                     _c(p.get("pit_id"))],
        ["# Date/Local Standard Time",  str(p.get("date",""))+"T"+str(p.get("pit_open_time",""))],
        ["# UTM Zone",                  str(p.get("utm_zone_number",""))+str(p.get("utm_zone_letter",""))],
        ["# Easting",                   _c(p.get("utm_easting"))],
        ["# Northing",                  _c(p.get("utm_northing"))],
        ["# Latitude",                  _c(p.get("latitude"))],
        ["# Longitude",                 _c(p.get("longitude"))],
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

def export_all(pit_id):
    conn = get_conn()
    p_row = conn.execute("SELECT * FROM sites WHERE pit_id=?", (pit_id,)).fetchone()
    if not p_row:
        conn.close()
        return {}
    cols = [d[0] for d in conn.execute("SELECT * FROM sites WHERE pit_id=?", (pit_id,)).description]
    p    = dict(zip(cols, p_row))
    date_str = (p.get("date") or "00000000").replace("-","")
    # Campaign name as the user entered it (stored on the campaigns table),
    # falling back to the configured default if somehow unset.
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

    # ── siteDetails ───────────────────────────────────────────────
    obs = conn.execute("""SELECT o.name FROM observers o
        JOIN site_observers so ON so.observer_id=o.id WHERE so.site_id=?""",
        (p["id"],)).fetchall()
    obs_str = ", ".join([r[0] for r in obs]) or NO_DATA
    veg = json.loads(p.get("vegetation") or "[]")
    sd = _hdr(p) + [
        ["# HS (cm)",                   _c(p.get("total_depth"))],
        ["# Observers",                 obs_str],
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

    # ── density ───────────────────────────────────────────────────
    dens_data = lrows(mt("density"))
    dens = _hdr(p) + [["# Top (cm)","Bottom (cm)","Density A (kg/m3)","Density B (kg/m3)","Density C (kg/m3)"]]
    for d in dens_data:
        dens.append([_c(d["top_cm"]),_c(d["bottom_cm"]),
                     _c(d["value"]),_c(d["value_b"]),_c(d["value_c"])])

    # ── temperature ───────────────────────────────────────────────
    temp_data = lrows(mt("temperature"))
    temp = _hdr(p) + [["# Depth (cm)","Temperature (deg C)","Time start/end"]]
    for i,d in enumerate(temp_data):
        t_val = _c(d.get("time_recorded"))
        if 0 < i < len(temp_data)-1: t_val = NO_DATA
        temp.append([_c(d["depth_from_surface"]),_c(d["value"]),t_val])

    # ── LWC ───────────────────────────────────────────────────────
    lwc_data  = lrows(mt("permittivity"))
    dens_map  = {d["top_cm"]: d["value_avg"] for d in dens_data}
    lwc = _hdr(p) + [["# Top (cm)","Bottom (cm)","Avg Density (kg/m3)","Permittivity A","Permittivity B","LWC-vol A (%)","LWC-vol B (%)"]]
    for d in lwc_data:
        lwc.append([_c(d["top_cm"]),_c(d["bottom_cm"]),
                    _c(dens_map.get(d["top_cm"])),
                    _c(d["value"]),_c(d["value_b"]),NO_DATA,NO_DATA])

    # ── stratigraphy ──────────────────────────────────────────────
    strat_data = lrows(mt("grain_size"))
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
        mn,mx = _c(d["grain_size_min"]),_c(d["grain_size_max"])
        gs = f"{mn}-{mx} mm" if mn!=NO_DATA and mx!=NO_DATA else NO_DATA
        strat.append([_c(d["top_cm"]),_c(d["bottom_cm"]),
                      gs,_c(d["grain_type"]),_c(d["hand_hardness"]),
                      _c(d["snow_wetness"]),_c(d["comments"])])

    # ── SSA ───────────────────────────────────────────────────────
    ssa_data = lrows(mt("ssa"))
    cal_row  = conn.execute(
        "SELECT spectralon,calib_values,measured_at,operator FROM ssa_calibration WHERE site_id=? LIMIT 1",
        (p["id"],)).fetchone()
    inst_row = conn.execute(
        "SELECT i.name FROM instruments i JOIN ssa_calibration sc ON sc.instrument_id=i.id WHERE sc.site_id=? LIMIT 1",
        (p["id"],)).fetchone()
    ssa_extra = [["# Instrument", inst_row[0] if inst_row else "IceCube"]]
    if cal_row:
        spec = json.loads(cal_row[0] or "[]")
        cval = json.loads(cal_row[1] or "[]")
        if cal_row[3]: ssa_extra.append(["# SSA Operator", cal_row[3]])
        if spec: ssa_extra.append(["# Spectralon"] + spec)
        if cval: ssa_extra.append(["# Calibration Values (V)"] + cval)
        if cal_row[2]: ssa_extra.append(["# Timing", cal_row[2]])
    ssa = _hdr(p, ssa_extra) + \
        [["# Sample_signal(V)","Reflectance(%)","SSA(m2 kg-1)","Sample_top_height(cm)","Grain type","Comments"]]
    for d in ssa_data:
        ssa.append([_c(d["value_c"]),_c(d["value_b"]),
                    _c(d["value"]),_c(d["top_cm"]),
                    _c(d["grain_type"]),_c(d["comments"])])

    # Add instruments to siteDetails
    inst_rows = conn.execute("""
        SELECT i.name, si.notes, si.used
        FROM site_instruments si
        JOIN instruments i ON i.id=si.instrument_id
        WHERE si.site_id=?""", (p["id"],)).fetchall()
    if inst_rows:
        sd.append([])
        sd.append(["# INSTRUMENTS"])
        sd.append(["# Instrument","Serial No.","Used"])
        for ir in inst_rows:
            sd.append([ir[0], ir[1] or "—", "Y" if ir[2]==1 else "N"])

    conn.close()
    return {
        _fname(pit_id,date_str,"siteDetails",campaign):  _csv(sd),
        _fname(pit_id,date_str,"density",campaign):      _csv(dens),
        _fname(pit_id,date_str,"temperature",campaign):  _csv(temp),
        _fname(pit_id,date_str,"LWC",campaign):          _csv(lwc),
        _fname(pit_id,date_str,"stratigraphy",campaign): _csv(strat),
        _fname(pit_id,date_str,"SSA",campaign):          _csv(ssa),
    }

# ─────────────────────────────────────────────────────────────────────────────
# EXPORT DESTINATIONS
#
# export_all() produces {filename: csv_string}. Everything below is a "sink":
# a function that takes that dict and puts it somewhere. This is the seam that
# keeps destinations modular — adding Drive / S3 / a repo means writing another
# sink, never touching the export logic above. Browser-download is the third
# sink and lives in the form's JS (it can't run server-side).
# ─────────────────────────────────────────────────────────────────────────────
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


    """Write {filename: content} into `folder` on the machine running Python.

    NOTE: `folder` is resolved by THIS process. Locally that's the user's own
    machine; deployed, it's the server. Returns (ok, info) where info is the
    absolute folder path on success or an error message on failure.
    """
    if not folder or not str(folder).strip():
        folder = EXPORT_DIR
    folder = os.path.abspath(os.path.expanduser(str(folder).strip()))
    try:
        os.makedirs(folder, exist_ok=True)
        # Fail loudly if it's not actually a writable directory.
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

# ─────────────────────────────────────────────────────────────────────────────
# LOCAL HTTP API — runs in background thread
# JS posts JSON to http://localhost:8502/save or /csv
# ─────────────────────────────────────────────────────────────────────────────
_api_results = {}  # shared dict for results

class APIHandler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass  # silence server logs

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_POST(self):
        length  = int(self.headers.get("Content-Length", 0))
        body    = self.rfile.read(length)
        path    = urlparse(self.path).path

        try:
            payload = json.loads(body)
        except:
            self._respond(400, {"ok": False, "msg": "Invalid JSON"})
            return

        if path == "/save":
            ok, result = save_pit(payload)
            _api_results["last"] = {"ok": ok, "pit_id": result if ok else None,
                                     "msg": result if not ok else None}
            self._respond(200, {"ok": ok, "pit_id": result if ok else None,
                                 "msg": result if not ok else None})

        elif path == "/pits":
            # GET-style but called via POST for simplicity
            conn = get_conn()
            try:
                rows = conn.execute(
                    "SELECT pit_id, date FROM sites ORDER BY created_at DESC LIMIT 50"
                ).fetchall()
                pits = [{"pit_id": r[0], "date": r[1]} for r in rows]
                self._respond(200, {"ok": True, "pits": pits})
            except Exception as e:
                self._respond(200, {"ok": True, "pits": []})
            finally:
                conn.close()

        elif path == "/csv":
            ok, result = save_pit(payload)
            if not ok:
                self._respond(500, {"ok": False, "msg": result})
                return
            pit_id = payload["meta"]["pit_id"]
            csvs   = export_all(pit_id)
            # Bundle all CSVs into ONE zip so the browser saves a single file
            # (one prompt, not six). Built in memory, sent as base64.
            campaign = payload["meta"].get("campaign") or CAMPAIGN
            zipname, zipb64 = zip_csvs(csvs, pit_id, campaign)
            self._respond(200, {"ok": True, "pit_id": pit_id,
                                 "zipname": zipname, "zip": zipb64})

        elif path == "/csv_folder":
            # Folder-only: save the pit, write CSVs to the path, return no file
            # bodies (nothing to download). Used when the user picks "folder".
            ok, result = save_pit(payload)
            if not ok:
                self._respond(500, {"ok": False, "msg": result})
                return
            pit_id = payload["meta"]["pit_id"]
            csvs   = export_all(pit_id)
            fok, finfo = save_csvs_to_folder(csvs, payload.get("folder", ""))
            if fok:
                self._respond(200, {"ok": True, "pit_id": pit_id,
                                     "folder": finfo["folder"],
                                     "folder_count": finfo["count"]})
            else:
                self._respond(200, {"ok": False, "msg": finfo})
        else:
            self._respond(404, {"ok": False, "msg": "Not found"})

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin",  "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _respond(self, code, data):
        body = json.dumps(data).encode()
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

def start_api():
    if st.session_state.get("_api_started"):
        return
    try:
        # 127.0.0.1, not "localhost" — avoids the IPv4/IPv6 resolution mismatch
        # that makes fetch() hang forever (browser tries 127.0.0.1, a server
        # bound to ::1 never answers). ThreadingHTTPServer so a preflight OPTIONS
        # and the POST can't serialize-stall against each other.
        #
        # allow_reuse_address (SO_REUSEADDR) lets a fresh launch re-bind the port
        # even if the OS is still holding the old socket in TIME_WAIT after a clean
        # Ctrl-C exit. This removes the most common reason for needing to manually
        # kill the PID. NOTE: it does NOT help if a previous process is genuinely
        # still alive and serving — that port is truly occupied and still needs a
        # kill. Must be set before the server is constructed, since __init__ binds.
        ThreadingHTTPServer.allow_reuse_address = True
        server = ThreadingHTTPServer(("127.0.0.1", API_PORT), APIHandler)
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        st.session_state["_api_started"] = True
    except OSError as e:
        # Port busy — almost always a stale server from a prior `streamlit run`
        # reload still holding the port across reruns. This is the NORMAL case on
        # every rerun, so we reuse it silently. Genuine hangs are caught by the
        # 8s fetch timeout in the form JS, which reports a visible error instead.
        st.session_state["_api_started"] = True

# ─────────────────────────────────────────────────────────────────────────────
# HTML FORM
# ─────────────────────────────────────────────────────────────────────────────
FORM = r"""<!DOCTYPE html>
<html lang="en" data-theme="light">
<head>
<meta charset="UTF-8">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Instrument+Sans:wght@400;500;600&family=Roboto+Mono:wght@300;400&display=swap" rel="stylesheet">
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --w:   #ffffff; --bg:  #f0f2f5; --ink: #111111;
  --ink2:#555555; --ink3:#999999; --acc: #0057ff;
  --red: #d0021b; --grn: #007a3d;
  --rule:#e0e2e6; --rule2:#c8cace;
  --sans:'Instrument Sans',sans-serif;
  --mono:'Roboto Mono',monospace;
  --nav: 52px;
}
html[data-theme="dark"]{
  --w:   #1a1a1f; --bg:  #111116; --ink: #e8e8f0;
  --ink2:#9090a8; --ink3:#55556a; --acc: #4a9eff;
  --red: #ff6b6b; --grn: #4af0a0;
  --rule:#2a2a35; --rule2:#3a3a48;
}
html,body{height:100%;background:var(--w);font-family:var(--sans);color:var(--ink);font-size:14px;overflow:hidden}

/* ── TOP BAR ── */
.topbar{
  position:fixed;top:0;left:0;right:0;z-index:200;height:var(--nav);
  background:var(--ink);display:flex;align-items:center;
  padding:0 32px;border-bottom:1px solid rgba(255,255,255,.08);
}
html[data-theme="dark"] .topbar{background:#0d0d12;border-bottom:1px solid var(--rule)}
.tb-brand{display:flex;align-items:center;gap:10px;margin-right:20px}
.tb-wordmark{font-size:15px;font-weight:400;color:#fff;letter-spacing:.01em}
.tb-wordmark strong{font-weight:700;color:#fff}
.tb-divider{color:rgba(255,255,255,.2);font-size:16px;font-weight:200;margin:0 2px}
.tb-inst{font-family:var(--mono);font-size:10px;letter-spacing:.14em;text-transform:uppercase;color:rgba(255,255,255,.35)}
.tb-title{font-size:13px;font-weight:500;color:#fff;letter-spacing:.02em}
.tb-pitid{font-family:var(--mono);font-size:12px;color:#fff;opacity:.92;margin-left:18px;letter-spacing:.04em;padding:4px 11px;background:rgba(255,255,255,.10);border:1px solid rgba(255,255,255,.16);border-radius:3px;white-space:nowrap;max-width:240px;overflow:hidden;text-overflow:ellipsis}
.tb-right{display:flex;align-items:center;gap:8px;margin-left:auto}
.tb-pct{font-family:var(--mono);font-size:11px;color:#fff;opacity:.45;margin-right:2px}
.tb-prog{width:64px;height:2px;background:rgba(255,255,255,.12);border-radius:1px;overflow:hidden}
.tb-fill{height:100%;background:var(--acc);border-radius:1px;transition:width .3s ease}
.tb-save{padding:6px 16px;background:var(--acc);color:#fff;border:none;border-radius:3px;font-size:12px;font-weight:600;cursor:pointer;font-family:var(--sans);transition:opacity .1s;margin-left:8px}
.tb-save:hover{opacity:.82}
/* export = dest dropdown + button, with a folder panel that drops below */
.tb-export{position:relative;display:flex;align-items:center;gap:6px}
.tb-csv{padding:6px 14px;background:transparent;color:#fff;border:1px solid rgba(255,255,255,.22);border-radius:3px;font-size:12px;cursor:pointer;font-family:var(--sans);transition:all .1s}
.tb-csv:hover{border-color:rgba(255,255,255,.55)}
.tb-dest{padding:5px 8px;background:rgba(255,255,255,.06);color:#fff;border:1px solid rgba(255,255,255,.22);border-radius:3px;font-size:12px;cursor:pointer;font-family:var(--sans);outline:none}
.tb-dest option{background:#1a1a1f;color:#fff}
.tb-folder-pop{position:absolute;top:calc(100% + 8px);right:0;z-index:210;display:flex;flex-direction:column;gap:5px;padding:10px 12px;background:var(--ink);border:1px solid rgba(255,255,255,.18);border-radius:4px;box-shadow:0 6px 24px rgba(0,0,0,.35);min-width:280px}
html[data-theme="dark"] .tb-folder-pop{background:#0d0d12;border-color:var(--rule)}
.tb-folder-lbl{font-family:var(--mono);font-size:9px;letter-spacing:.08em;text-transform:uppercase;color:rgba(255,255,255,.45)}
.tb-folder{padding:6px 9px;background:rgba(255,255,255,.06);color:#fff;border:1px solid rgba(255,255,255,.22);border-radius:3px;font-size:12px;font-family:var(--mono);outline:none;width:100%;letter-spacing:.02em}
.tb-folder::placeholder{color:rgba(255,255,255,.35)}
.tb-folder:focus{border-color:rgba(255,255,255,.55)}
.tb-status{font-family:var(--mono);font-size:10px;color:#fff;opacity:.35;margin-left:4px;min-width:84px;letter-spacing:.02em}
.tb-status.ok{color:#4af0a0;opacity:.9}
.tb-status.unsaved{color:#f0a84a;opacity:.8}
.tb-status.err{color:#ff6b6b;opacity:.9}
.tb-theme{background:transparent;border:1px solid rgba(255,255,255,.18);border-radius:3px;color:#fff;opacity:.55;cursor:pointer;font-size:13px;padding:4px 8px;transition:all .1s;margin-left:4px}
.tb-theme:hover{opacity:.9}

/* ── SHELL ── */
.shell{display:flex;height:calc(100vh - var(--nav));margin-top:var(--nav);overflow:hidden}

/* ── INDEX ── */
.index{width:200px;min-width:200px;background:var(--bg);border-right:1px solid var(--rule);height:100%;overflow-y:auto;flex-shrink:0;display:flex;flex-direction:column}
.idx-item{display:flex;align-items:center;padding:11px 20px;cursor:pointer;border-bottom:1px solid var(--rule);gap:10px;transition:background .1s;user-select:none}
.idx-item:hover{background:var(--rule)}
.idx-item.active{background:var(--w);border-right:2px solid var(--acc)}
.idx-num{font-family:var(--mono);font-size:10px;color:var(--ink3);min-width:18px}
.idx-lbl{font-size:12px;color:var(--ink2);font-weight:500}
.idx-item.active .idx-lbl{color:var(--ink)}
.idx-pip{width:6px;height:6px;border-radius:50%;border:1.5px solid var(--rule2);flex-shrink:0;margin-left:auto;transition:all .2s}
.nav-foot{border-top:1px solid var(--rule);padding:10px 0;margin-top:auto}
.nav-foot-label{font-family:var(--mono);font-size:9px;letter-spacing:.12em;text-transform:uppercase;color:var(--ink3);padding:8px 20px 5px}
.nav-foot-empty{font-family:var(--mono);font-size:11px;color:var(--ink3);padding:4px 20px;display:block}
.pit-entry{display:block;padding:6px 20px;font-family:var(--mono);font-size:11px;color:var(--ink2);border-bottom:1px solid var(--rule);cursor:pointer;transition:background .1s;text-decoration:none;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.pit-entry:hover{background:var(--rule);color:var(--ink)}
.pit-entry .pit-date{font-size:10px;color:var(--ink3);display:block;margin-top:1px}
.idx-pip.done{background:var(--grn);border-color:var(--grn)}

/* ── MAIN ── */
.main{flex:1;min-width:0;height:100%;overflow-y:auto}

/* ── SECTION ── */
.sec{border-bottom:1px solid var(--rule)}
.sec-hd{display:flex;align-items:center;gap:12px;padding:18px 40px 14px;border-bottom:1px solid var(--rule);background:var(--bg)}
.sec-num{font-family:var(--mono);font-size:10px;color:var(--ink3);letter-spacing:.06em;min-width:18px}
.sec-title{font-size:13px;font-weight:600;letter-spacing:.03em;text-transform:uppercase}
.sec-meta{font-family:var(--mono);font-size:11px;color:var(--ink3);margin-left:auto}
.sec-body{padding:24px 40px}

/* ── FIELD ROWS ── */
.row{display:flex;border:1px solid var(--rule);border-radius:3px;overflow:hidden;margin-bottom:14px}
.ri{flex:1;border-right:1px solid var(--rule);display:flex;flex-direction:column}
.ri:last-child{border-right:none}
.rl{font-family:var(--mono);font-size:9px;letter-spacing:.1em;text-transform:uppercase;color:var(--ink3);background:var(--bg);padding:5px 12px;border-bottom:1px solid var(--rule);display:flex;align-items:center;gap:4px}
.req{color:var(--red);font-size:10px}
.ri input,.ri select,.ri textarea{font-family:var(--sans);font-size:13px;color:var(--ink);border:none;background:var(--w);padding:8px 12px;outline:none;width:100%}
.ri input:focus,.ri select:focus,.ri textarea:focus{background:var(--bg)}
.ri input::placeholder,.ri textarea::placeholder{color:var(--ink3)}
.ri textarea{resize:vertical;line-height:1.6;min-height:64px}
.ri select{cursor:pointer;appearance:none;background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='10' viewBox='0 0 10 10'%3E%3Cpath d='M1 3l4 4 4-4' stroke='%23999' stroke-width='1.5' fill='none'/%3E%3C/svg%3E");background-repeat:no-repeat;background-position:right 10px center;padding-right:26px}
.pitid{font-family:var(--mono);font-size:13px;color:var(--ink);padding:8px 12px;cursor:text;outline:none;background:var(--w);letter-spacing:.04em}
.pitid:focus{background:var(--bg)}
.hint{font-family:var(--mono);font-size:9px;color:var(--ink3);padding:3px 12px 5px;letter-spacing:.04em}
.coord-note{font-family:var(--mono);font-size:9px;color:var(--grn);padding:2px 12px 5px}
.coord-or{font-family:var(--mono);font-size:10px;color:var(--ink3);text-align:center;padding:5px 0;letter-spacing:.1em;width:50%}

/* ── TOGGLES ── */
.toggles{display:flex;flex-wrap:wrap;gap:4px;padding:9px 12px}
.tog{display:inline-flex;align-items:center;padding:4px 10px;border:1px solid var(--rule2);border-radius:2px;font-size:12px;color:var(--ink2);cursor:pointer;transition:all .08s;user-select:none;font-family:var(--sans)}
.tog:hover{border-color:var(--acc);color:var(--acc)}
.tog.on{background:var(--acc);color:#fff;border-color:var(--acc);font-weight:600}
.tog input{display:none}

/* ── TABLES ── */
.pw{border:1px solid var(--rule);border-radius:3px;overflow:hidden;margin-bottom:8px}
.pt{width:100%;border-collapse:collapse}
.pt thead tr{background:var(--bg)}
.pt th{padding:7px 12px;text-align:left;font-family:var(--mono);font-size:9px;color:var(--ink3);letter-spacing:.09em;text-transform:uppercase;font-weight:400;border-bottom:1px solid var(--rule);white-space:nowrap}
.pt td{border-bottom:1px solid var(--rule)}
.pt tr:last-child td{border-bottom:none}
.pt td input,.pt td select{border:none;background:transparent;padding:7px 12px;font-size:13px;font-family:var(--mono);color:var(--ink);width:100%;outline:none}
.pt td input:focus,.pt td select:focus{background:var(--bg)}
.avg input{color:var(--ink2);font-style:italic}
.del{width:26px;height:26px;border:none;background:transparent;color:var(--ink3);cursor:pointer;font-size:14px;display:flex;align-items:center;justify-content:center;margin:4px;border-radius:2px;transition:all .1s}
.del:hover{background:rgba(208,2,27,.1);color:var(--red)}
.add{width:100%;border:none;background:var(--bg);padding:7px 16px;font-size:12px;font-family:var(--mono);color:var(--ink3);cursor:pointer;text-align:left;border-top:1px solid var(--rule);letter-spacing:.04em;transition:all .1s}
.add:hover{background:var(--rule);color:var(--ink)}

/* ── INSTRUMENTS ── */
.ig-lbl{font-family:var(--mono);font-size:9px;letter-spacing:.12em;text-transform:uppercase;color:var(--ink3);padding:14px 0 6px}
.it{width:100%;border-collapse:collapse;border:1px solid var(--rule);border-radius:3px;overflow:hidden;margin-bottom:8px}
.it th{padding:7px 12px;text-align:left;font-family:var(--mono);font-size:9px;color:var(--ink3);letter-spacing:.09em;text-transform:uppercase;font-weight:400;border-bottom:1px solid var(--rule);background:var(--bg)}
.it td{padding:8px 12px;border-bottom:1px solid var(--rule);font-size:12px;vertical-align:middle}
.it tr:last-child td{border-bottom:none}
.sn{font-family:var(--mono);font-size:12px;border:1px solid var(--rule);border-radius:2px;padding:3px 8px;background:var(--bg);color:var(--ink);width:110px}
.yn{display:flex;border:1px solid var(--rule);border-radius:2px;overflow:hidden}
.yn button{padding:3px 10px;font-size:11px;font-family:var(--mono);background:transparent;border:none;cursor:pointer;color:var(--ink3);transition:all .1s}
.yn button.y.on{background:var(--grn);color:#fff;font-weight:600}
.yn button.n.on{background:var(--bg);color:var(--ink);font-weight:600}

/* ── CHECKLIST ── */
.cl-sum{display:flex;align-items:center;gap:16px;padding:14px 18px;background:var(--bg);border:1px solid var(--rule);border-radius:3px;margin-bottom:18px}
.cl-pct{font-family:var(--mono);font-size:30px;font-weight:300;color:var(--ink);min-width:56px}
.cl-bw{flex:1}
.cl-bt{height:2px;background:var(--rule);border-radius:1px;margin-bottom:5px;overflow:hidden}
.cl-bf{height:100%;background:var(--grn);border-radius:1px;transition:width .35s}
.cl-bl{font-family:var(--mono);font-size:11px;color:var(--ink3)}
.ci{display:flex;align-items:center;gap:10px;padding:9px 0;border-bottom:1px solid var(--rule);font-size:12px}
.ci:last-child{border-bottom:none}
.cd{width:7px;height:7px;border-radius:50%;flex-shrink:0;border:1.5px solid var(--rule2);transition:all .2s}
.cd.done{background:var(--grn);border-color:var(--grn)}
.ct{color:var(--ink2);font-family:var(--mono);font-size:12px}
.ct.done{color:var(--ink)}
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
    <div class="tb-export">
      <select class="tb-dest" id="tb-dest" onchange="onDest()" title="Where exported CSVs go">
        <option value="download">↓ Download (.zip)</option>
        <option value="folder">▢ Folder</option>
      </select>
      <button class="tb-csv" onclick="doCSV()">Export CSVs</button>
      <!-- folder path drops below, only when Folder is selected — kept out of
           the bar flow so it can't crowd the Pit ID -->
      <div class="tb-folder-pop" id="tb-folder-pop" style="display:none">
        <span class="tb-folder-lbl">Save to folder (on the machine running the app)</span>
        <input class="tb-folder" id="tb-folder" placeholder="exports/" spellcheck="false"
               oninput="rememberFolder()">
      </div>
    </div>
    <button class="tb-save" onclick="doSave()">Save to DB</button>
    <span class="tb-status unsaved" id="tb-st">● not saved</span>
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
    <div class="nav-foot-label">Saved pits</div>
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
      <div class="ri"><div class="rl">Total depth (cm)</div><input type="number" id="depth" min="0" placeholder="120"></div>
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
    <div class="coord-or">— or enter lat / lon —</div>
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
      <div class="ri" style="flex:2"></div>
    </div>
    <div class="pw"><table class="pt">
      <thead><tr><th>Height above ground (cm)</th><th>Temperature (°C)</th><th>Time (HHMM)</th><th style="width:36px"></th></tr></thead>
      <tbody id="tb"></tbody>
    </table><button class="add" onclick="addRow('t')">+ add measurement</button></div>
  </div>
</section>

<!-- 05 DENSITY -->
<section class="sec" id="s5">
  <div class="sec-hd"><span class="sec-num">05</span><span class="sec-title">Density</span><span class="sec-meta" id="dc-cnt">0 intervals</span></div>
  <div class="sec-body">
    <p style="font-family:var(--mono);font-size:11px;color:var(--ink3);margin-bottom:14px;letter-spacing:.02em">Height above ground · A B C = three cutter samples · average auto-computed</p>
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
    <span class="sec-meta">depth ↓ from surface · SnowPilot convention</span></div>
  <div class="sec-body">
    <p style="font-family:var(--mono);font-size:11px;color:var(--ink3);margin-bottom:14px;letter-spacing:.02em">
      Live plot from stratigraphy + density + temperature. Needs Total depth (§1) and stratigraphy layers (§7).
      <button class="add" style="display:inline-block;width:auto;border:1px solid var(--rule);border-radius:3px;margin-left:8px;padding:4px 12px" onclick="drawProfile()">↻ redraw</button>
    </p>
    <div id="profile-wrap" style="overflow-x:auto;border:1px solid var(--rule);border-radius:3px;background:var(--w);padding:8px"></div>
  </div>
</section>

</main>
</div>

<script>
const API = 'http://127.0.0.1:__API_PORT__';
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
}
function so(a){return a.map(v=>`<option value="${v}">${v}</option>`).join('')}

function addRow(t){
  const map={t:'tb',d:'db',l:'lb',s:'sb',sa:'ssab'};
  const tr=document.createElement('tr');
  if(t==='t'){
    tr.innerHTML=`<td><input type="number" placeholder="100"></td>
      <td><input type="number" step="0.1" placeholder="-2.0"></td>
      <td><input maxlength="4" placeholder="0808" oninput="milCheck(this)" style="font-family:var(--mono)"></td>
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
  tr.querySelector('input,select').focus();
}

function calcAvg(tr){
  const ins=tr.querySelectorAll('input[type=number]');
  const v=[ins[2],ins[3],ins[4]].map(i=>parseFloat(i.value)||null);
  const filled=v.filter(x=>x!==null);
  tr.querySelector('.avg input').value=filled.length?Math.round(filled.reduce((a,b)=>a+b)/filled.length):'';
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
    const ok=/^\d{4}$/.test(v)&&parseInt(v.slice(0,2))<=23&&parseInt(v.slice(2))<=59;
    inp.style.color=ok?'var(--ink)':'var(--red)';
  } else inp.style.color='var(--ink)';
}

// ── Pure JS UTM <-> WGS84 ─────────────────────────────────────────
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
  const e=parseFloat(document.getElementById('utme').value);
  const n=parseFloat(document.getElementById('utmn').value);
  const zr=document.getElementById('utmz').value.trim();
  if(!e||!n||!zr)return;
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
  const lat=parseFloat(document.getElementById('lat').value);
  const lon=parseFloat(document.getElementById('lon').value);
  if(isNaN(lat)||isNaN(lon))return;
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

// ── Theme ─────────────────────────────────────────────────────────
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

// ── Nav ───────────────────────────────────────────────────────────
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
}

function collect(){
  const loc=document.getElementById('loc').value;
  const location=loc==='__c'?document.getElementById('loc-c').value:loc;
  const veg=[];
  [{id:'vb',n:'bare'},{id:'vg',n:'grass'},{id:'vs',n:'shrub'},{id:'vd',n:'deadfall'}]
    .forEach(({id,n})=>{if(document.getElementById(id)?.checked)veg.push(n)});
  const zr=gv('utmz'),zm=zr.match(/^(\d{1,2})([A-Za-z])$/);
  const temperature=[];
  document.querySelectorAll('#tb tr').forEach(tr=>{
    const ins=tr.querySelectorAll('input');
    temperature.push({height:parseFloat(ins[0].value)||0,temp:parseFloat(ins[1].value)||0,time:ins[2].value||''});
  });
  const density=[];
  document.querySelectorAll('#db tr').forEach(tr=>{
    const ins=tr.querySelectorAll('input');
    density.push({top:parseFloat(ins[0].value)||0,bottom:parseFloat(ins[1].value)||0,
      a:parseFloat(ins[2].value)||null,b:parseFloat(ins[3].value)||null,c:parseFloat(ins[4].value)||null});
  });
  const lwc=[];
  document.querySelectorAll('#lb tr').forEach(tr=>{
    const ins=tr.querySelectorAll('input');
    lwc.push({top:parseFloat(ins[0].value)||0,bottom:parseFloat(ins[1].value)||0,
      a:parseFloat(ins[2].value)||null,b:parseFloat(ins[3].value)||null});
  });
  const stratigraphy=[];
  document.querySelectorAll('#sb tr').forEach(tr=>{
    const ins=tr.querySelectorAll('input');const sels=tr.querySelectorAll('select');
    stratigraphy.push({top:parseFloat(ins[0].value)||0,bottom:parseFloat(ins[1].value)||0,
      gmin:parseFloat(ins[2].value)||null,gmax:parseFloat(ins[3].value)||null,gavg:parseFloat(ins[4].value)||null,
      gtype:sels[0]?.value||'',hardness:sels[1]?.value||'',wetness:sels[2]?.value||'',comments:ins[5]?.value||''});
  });
  const ssa=[];
  document.querySelectorAll('#ssab tr').forEach(tr=>{
    const ins=tr.querySelectorAll('input');const sels=tr.querySelectorAll('select');
    ssa.push({height:parseFloat(ins[0].value)||0,signal:parseFloat(ins[1].value)||null,
      reflectance:parseFloat(ins[2].value)||null,ssa:parseFloat(ins[3].value)||null,
      grain_type:sels[0]?.value||'',comments:ins[4]?.value||''});
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
  // SSA operator is now its own column (no longer folded into notes).
  const ssaOp=gv('ssa-operator').trim();
  return{
    meta:{pit_id:document.getElementById('pitid').textContent.trim(),
      location,site:gv('site'),campaign:gv('campaign'),
      total_depth:parseFloat(gv('depth'))||0,
      utm_easting:parseFloat(gv('utme'))||null,utm_northing:parseFloat(gv('utmn'))||null,
      utm_zone_number:zm?parseInt(zm[1]):null,utm_zone_letter:zm?zm[2]:'',
      latitude:parseFloat(gv('lat'))||null,longitude:parseFloat(gv('lon'))||null,
      coord_source:gv('utme')?'utm':'latlon',
      elevation:parseFloat(gv('elev'))||null,slope_angle:parseFloat(gv('slope'))||null,
      recorded_by:gv('recby'),surveyors:gv('surv'),date:gv('date'),
      pit_open_time:gv('po'),temp_time_start:gv('ts'),temp_time_end:gv('te'),
      gps_device:gv('gps'),
      gps_uncertainty:parseFloat(gv('gps-unc'))||null,
      gps_uncertainty_unit:gv('gps-unc-unit'),
      wise_serial:gv('wise'),density_cutter:density_cutter,
      comments:gv('comments'),flags:gv('flags')||'None'},
    weather:{precip_rate:gr('pr'),precip_type:gr('pt'),sky:gr('sky'),wind:gr('wind')},
    ground:{condition:gr('gc'),roughness:gr('gr'),canopy:gr('tc'),
      snow_cover:gr('scc'),standing_water:gr('sw'),
      vegetation:veg,veg_height:parseFloat(gv('vh'))||0,
      new_depth:parseFloat(gv('nd'))||0,new_swe:parseFloat(gv('ns'))||0},
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

function validate(){
  const p=collect();const e=[];
  if(!p.meta.location)e.push('Location');
  if(!p.meta.pit_id||p.meta.pit_id==='—')e.push('Pit ID');
  if(!p.meta.recorded_by)e.push('Recorded by');
  if(!p.meta.surveyors)e.push('Field observers');
  if(!p.meta.date)e.push('Date');
  return{p,e};
}

function setst(msg,cls){const el=document.getElementById('tb-st');el.textContent=msg;el.className='tb-status'+(cls?' '+cls:' unsaved');}
// Set initial unsaved state
document.getElementById('tb-st').textContent='● not saved';

// POST helper with an 8s timeout. Without this, an unreachable endpoint leaves
// the promise pending forever and the status sits on "saving…" with no error.
function post(path,payload){
  const ctrl=new AbortController();
  const tid=setTimeout(()=>ctrl.abort(),8000);
  return fetch(API+path,{
    method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(payload),signal:ctrl.signal
  }).finally(()=>clearTimeout(tid));
}
function fetchErr(err){
  return err.name==='AbortError'
    ? 'no response — is the app running locally?'
    : 'error: '+err.message;
}

function doSave(){
  const{p,e}=validate();
  if(e.length){setst('missing: '+e.join(', '),'err');return;}
  setst('saving…','');
  post('/save',p)
    .then(r=>r.json())
    .then(r=>{
      if(r.ok){setst('● saved · '+r.pit_id,'ok');window._saved_pid=r.pit_id;}
      else setst('● error: '+r.msg,'err');
    })
    .catch(err=>setst(fetchErr(err),'err'));
}

function onDest(){
  const folder=document.getElementById('tb-dest').value==='folder';
  document.getElementById('tb-folder-pop').style.display=folder?'flex':'none';
  try{localStorage.setItem('cp-dest',document.getElementById('tb-dest').value);}catch(e){}
  if(folder){const f=document.getElementById('tb-folder');setTimeout(()=>f.focus(),0);}
}
function rememberFolder(){
  try{localStorage.setItem('cp-folder',document.getElementById('tb-folder').value);}catch(e){}
}
(function(){
  try{
    const d=localStorage.getItem('cp-dest');
    if(d){document.getElementById('tb-dest').value=d;}
    const f=localStorage.getItem('cp-folder');
    if(f){document.getElementById('tb-folder').value=f;}
    onDest();
  }catch(e){}
})();

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

function doCSV(){
  const{p,e}=validate();
  if(e.length){setst('fill required fields first','err');return;}
  const dest=document.getElementById('tb-dest').value;
  const folder=document.getElementById('tb-folder').value;

  if(dest==='folder'){
    setst('writing to folder…','');
    post('/csv_folder',{...p,dest:'folder',folder})
      .then(r=>r.json())
      .then(r=>{
        if(!r.ok){setst('folder error: '+r.msg,'err');return;}
        setst('● saved '+r.folder_count+' files → '+shortPath(r.folder),'ok');
      })
      .catch(err=>setst(fetchErr(err),'err'));
    return;
  }

  // default: one-click zip download
  setst('exporting…','');
  post('/csv',{...p,dest:'download'})
    .then(r=>r.json())
    .then(r=>{
      if(!r.ok){setst('error: '+r.msg,'err');return;}
      downloadZip(r.zipname,r.zip);
      setst('● downloaded · '+r.zipname,'ok');
    })
    .catch(err=>setst(fetchErr(err),'err'));
}

function shortPath(pth){
  if(!pth)return'';
  const parts=pth.split(/[\\/]/).filter(Boolean);
  return parts.length<=2?pth:'…/'+parts.slice(-2).join('/');
}

// ── Profile plot (SnowPilot convention: 0 at surface, depth increasing down) ──
const HARD_SCALE={'F':1,'4F':2,'1F':3,'P':4,'K':5,'I':6};
const GRAIN_COLOR={
  // colored by IACS family so the plot stays readable
  PP:'#3bc',PPsd:'#4cd',PPgp:'#5ad',PPrm:'#2ab',          // precipitation — blue
  MM:'#9ad',                                               // machine made — pale blue
  DF:'#6ad',                                               // decomposing — mid blue
  RG:'#7c7',RGwp:'#8d8',RGxf:'#6b6',RGlr:'#9e9',           // rounded — green
  FC:'#fb4',FCsf:'#fd8',FCxr:'#fa3',FCso:'#fc6',           // faceted — amber
  DH:'#f84',DHcp:'#f96',DHpr:'#f73',DHla:'#fa7',DHxr:'#e63', // depth hoar — orange
  SH:'#e44',SHxr:'#f66',                                   // surface hoar — red
  MF:'#c9e',MFcl:'#d9f',MFsl:'#b8e',MFcr:'#a8d',           // melt forms — purple
  IF:'#9cf',IFsc:'#adf',IFrc:'#8be',IFbi:'#7ad'            // ice — light blue
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
  // Layout
  const W=720,padT=28,padB=28,padL=54,plotH=440;
  const hardW=150, grainW=46, gapA=26, densW=150, gap=20;
  const xHard=padL, xGrain=xHard+hardW+6, xDens=xGrain+grainW+gapA;
  const H=padT+plotH+padB;
  // depth (height-above-ground) -> y. Surface (y=HS) at top, ground (0) at bottom.
  const d2y=h=>padT+(1-(h/HS))*plotH;       // h = height above ground
  // density range
  const dens=(p.density||[]).filter(d=>d.top!=null);
  let dmin=Infinity,dmax=-Infinity;
  dens.forEach(d=>{const v=[d.a,d.b,d.c].filter(x=>x!=null);v.forEach(x=>{dmin=Math.min(dmin,x);dmax=Math.max(dmax,x);});});
  if(!isFinite(dmin)){dmin=0;dmax=500;} if(dmin===dmax){dmin-=50;dmax+=50;}
  const dn2x=v=>xDens+((v-dmin)/(dmax-dmin))*densW;
  // temperature range
  const temp=(p.temperature||[]).filter(t=>t.height!=null);
  let tmin=Infinity,tmax=-Infinity;
  temp.forEach(t=>{tmin=Math.min(tmin,t.temp);tmax=Math.max(tmax,t.temp);});
  if(!isFinite(tmin)){tmin=-15;tmax=0;} if(tmin===tmax){tmin-=2;tmax+=2;}
  const tn2x=v=>xDens+((v-tmin)/(tmax-tmin))*densW;

  const css=getComputedStyle(document.documentElement);
  const ink=css.getPropertyValue('--ink').trim()||'#111';
  const ink3=css.getPropertyValue('--ink3').trim()||'#999';
  const rule=css.getPropertyValue('--rule').trim()||'#e0e2e6';
  const acc=css.getPropertyValue('--acc').trim()||'#0057ff';
  const red=css.getPropertyValue('--red').trim()||'#d0021b';

  let s=`<svg viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg" style="width:100%;max-width:${W}px;font-family:var(--mono)">`;
  // depth axis ticks (every ~5th of HS, labelled as depth from surface)
  const ticks=5;
  for(let i=0;i<=ticks;i++){
    const h=HS*(1-i/ticks);            // height above ground at this tick
    const depth=Math.round(HS-h);      // depth from surface
    const y=d2y(h);
    s+=`<line x1="${padL}" y1="${y}" x2="${xDens+densW}" y2="${y}" stroke="${rule}" stroke-width="1"/>`;
    s+=`<text x="${padL-6}" y="${y+3}" text-anchor="end" font-size="9" fill="${ink3}">${depth}</text>`;
  }
  s+=`<text x="14" y="${padT+plotH/2}" font-size="9" fill="${ink3}" transform="rotate(-90 14 ${padT+plotH/2})" text-anchor="middle">DEPTH (cm) ↓</text>`;

  // hand-hardness bars (width = hardness) + grain colour column
  strat.forEach(l=>{
    const yTop=d2y(l.top), yBot=d2y(l.bottom);
    const hh=HARD_SCALE[l.hardness]||1;
    const bw=(hh/6)*hardW;
    const col=GRAIN_COLOR[l.gtype]||ink3;
    // hardness bar
    s+=`<rect x="${xHard}" y="${yTop}" width="${bw}" height="${Math.max(1,yBot-yTop)}" fill="${col}" fill-opacity="0.55" stroke="${ink}" stroke-width="0.6"/>`;
    // grain colour swatch + label
    s+=`<rect x="${xGrain}" y="${yTop}" width="${grainW}" height="${Math.max(1,yBot-yTop)}" fill="${col}" fill-opacity="0.85" stroke="${rule}" stroke-width="0.5"/>`;
    if(yBot-yTop>11){
      s+=`<text x="${xGrain+grainW/2}" y="${(yTop+yBot)/2+3}" text-anchor="middle" font-size="8" fill="${ink}">${l.gtype||''}</text>`;
    }
  });
  // hardness scale labels
  ['F','4F','1F','P','K','I'].forEach((h,i)=>{
    const x=xHard+((i+1)/6)*hardW;
    s+=`<text x="${x}" y="${padT-8}" text-anchor="middle" font-size="8" fill="${ink3}">${h}</text>`;
  });
  s+=`<text x="${xHard}" y="${H-8}" font-size="9" fill="${ink3}">HARDNESS →</text>`;
  s+=`<text x="${xGrain}" y="${padT-8}" font-size="8" fill="${ink3}">GRAIN</text>`;

  // density line (right panel)
  if(dens.length){
    let pts=[];
    dens.slice().sort((a,b)=>b.top-a.top).forEach(d=>{
      const v=[d.a,d.b,d.c].filter(x=>x!=null);
      if(!v.length)return;
      const avg=v.reduce((a,b)=>a+b)/v.length;
      const mid=(d.top+d.bottom)/2;
      pts.push([dn2x(avg),d2y(mid)]);
    });
    if(pts.length){
      s+=`<polyline points="${pts.map(q=>q.join(',')).join(' ')}" fill="none" stroke="${ink}" stroke-width="1.5"/>`;
      pts.forEach(q=>s+=`<circle cx="${q[0]}" cy="${q[1]}" r="2" fill="${ink}"/>`);
    }
    s+=`<text x="${xDens}" y="${padT-8}" font-size="8" fill="${ink3}">DENSITY ${Math.round(dmin)}–${Math.round(dmax)} kg/m³</text>`;
  }
  // temperature curve (same right panel, accent colour)
  if(temp.length){
    let tp=temp.slice().sort((a,b)=>b.height-a.height).map(t=>[tn2x(t.temp),d2y(t.height)]);
    s+=`<polyline points="${tp.map(q=>q.join(',')).join(' ')}" fill="none" stroke="${red}" stroke-width="1.5" stroke-dasharray="3,2"/>`;
    tp.forEach(q=>s+=`<circle cx="${q[0]}" cy="${q[1]}" r="2" fill="${red}"/>`);
    s+=`<text x="${xDens}" y="${H-8}" font-size="8" fill="${red}">TEMP ${tmin.toFixed(1)}–${tmax.toFixed(1)}°C (dashed)</text>`;
  }
  s+=`</svg>`;
  wrap.innerHTML=s;
}

document.querySelectorAll('.toggles input').forEach(inp=>{
  inp.addEventListener('change',()=>{
    if(inp.name){document.querySelectorAll(`input[name="${inp.name}"]`).forEach(r=>r.closest('.tog').classList.toggle('on',r.checked));}
    else{inp.closest('.tog').classList.toggle('on',inp.checked);}
    tick();
  });
});

buildInst(); tick(); loadSavedPits();

function loadSavedPits(){
  post('/pits',{})
    .then(r=>r.json())
    .then(r=>{
      const el=document.getElementById('saved-pits-list');
      if(!r.pits||r.pits.length===0){
        el.innerHTML='<span class="nav-foot-empty">none yet</span>';return;
      }
      el.innerHTML=r.pits.map(p=>
        `<a class="pit-entry" title="${p.pit_id}">${p.pit_id}
         <span class="pit-date">${p.date||''}</span></a>`
      ).join('');
    })
    .catch(()=>{});
}

// Reload saved pits after save
const _origDoSave=doSave;
doSave=function(){_origDoSave();setTimeout(loadSavedPits,800);};
</script>
</body></html>"""

# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────
def main():
    init_db()
    start_api()

    # Inject API port into the form. We deliberately do NOT stuff the full
    # institution string into the small topbar badge — it's a wide, uppercase,
    # letter-spaced label and a long name overflows the bar. The full name still
    # appears in the browser tab title via st.set_page_config above.
    form = FORM.replace("__API_PORT__", str(API_PORT))
    # Short brand label only: first token before any separator, capped in length.
    _short = INSTITUTION.split("·")[0].split("-")[0].strip()[:18] or "CryoGARS"
    form = form.replace('>CryoGARS</span>', f'>{_short}</span>')

    # Height is kept low on purpose: the page CSS clamps both the iframe and its
    # wrapper to 100vh, so a small reservation avoids a flash of oversized empty
    # block on first paint and prevents any leftover region below the iframe.
    components.html(form, height=800, scrolling=False)

    # Saved pits are shown inside the form's left panel

if __name__ == "__main__":
    main()