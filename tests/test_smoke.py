"""End-to-end smoke test: archive a pit, load it back, exercise the attachment
rules. Uses Flask's test client rather than a real socket, so CI never has to
pick a free port or poll for readiness.

This encodes the checks that were previously only ever run by hand against a
live server — the ones that catch integration faults the unit tests cannot see,
because each layer is individually correct and the seam between them is not.
"""
import base64
import hashlib
import io
import json
import os
import re
import sqlite3
import sys
import tempfile
import zipfile
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_TMP = tempfile.mkdtemp(prefix="cryopit-smoke-")
os.environ["CRYOPIT_DB_PATH"] = os.path.join(_TMP, "smoke.db")
os.environ["CRYOPIT_EXPORT_DIR"] = os.path.join(_TMP, "exports")
os.environ["CRYOPIT_ENABLE_EDIT"] = "1"

from cryopit import make_app                                    # noqa: E402
from cryopit.web import _ATTACH_LIMITS, _ATTACH_TOTAL, _STRAT_PER_LAYER, _layer_folder           # noqa: E402

_pass = _fail = 0


def check(cond, label):
    global _pass, _fail
    if cond:
        _pass += 1
        print(f"PASS {label}")
    else:
        _fail += 1
        print(f"FAIL {label}")


JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 64
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
PDF = b"%PDF-1.4\n" + b"x" * 64


def uniq(base, n):
    """A distinct file of the same TYPE. Attachments are identified by their
    bytes now, so a fixture that re-sends one image is testing deduplication,
    not the per-category cap."""
    return base + bytes([n % 251, (n * 7) % 251]) + b"\x00" * 8


def pit(pid, layers=3):
    hs = 100.0
    step = hs / layers
    return {
        "meta": {"pit_id": pid, "location": "Grand Mesa", "site": pid,
                 "campaign": "WY2026", "date": "2026-02-10", "total_depth": hs,
                 "recorded_by": "A. Marshall", "surveyors": "B. Ross",
                 "latitude": 39.03, "longitude": -108.06, "flags": "None"},
        "weather": {"sky": "Clear"}, "ground": {"condition": "Frozen"},
        "temperature": [{"height": round(hs - i * step, 1), "temp": -8 + i}
                        for i in range(layers)],
        "density": [{"top": round(hs - i * step, 1),
                     "bottom": round(hs - (i + 1) * step, 1),
                     "a": 200 + i * 10, "b": 205 + i * 10, "c": None}
                    for i in range(layers)],
        "lwc": [], "ssa": [],
        "stratigraphy": [{"top": round(hs - i * step, 1),
                          "bottom": round(hs - (i + 1) * step, 1),
                          "gtype": ["PP", "RG", "DH"][i % 3],
                          "hardness": ["F", "1F", "4F"][i % 3], "wetness": "D"}
                         for i in range(layers)],
        "instruments": [{"name": "SMP", "sn": "SMP-99", "used": "Y"},
                        {"name": "Avalanche probe", "sn": "AP-7", "used": "Y"},
                        {"name": "Digital LWC", "sn": "", "used": "N"},
                        {"name": "Lyte Probe", "sn": "", "used": None},
                        {"name": "Pit pictures", "sn": "", "used": "Y"}],
        "ssa_calibration": {},
    }


app = make_app()
app.config["TESTING"] = True
c = app.test_client()
SITE_IDS = {}

# Stage 12 protects every state-changing API call with a token embedded in the
# authenticated page. Keep the existing smoke test readable by adding that
# header centrally to every test-client POST.
_real_post = c.post
_csrf_token = None

def _csrf_post(*args, **kwargs):
    global _csrf_token
    if _csrf_token is None:
        page = c.get("/").get_data(as_text=True)
        match = re.search(r"const CSRF_TOKEN = '([^']+)'", page)
        assert match, "page did not contain a CryoPit CSRF token"
        _csrf_token = match.group(1)
    headers = dict(kwargs.pop("headers", {}) or {})
    headers.setdefault("X-CryoPit-CSRF", _csrf_token)
    kwargs["headers"] = headers
    return _real_post(*args, **kwargs)

c.post = _csrf_post


def archive(data, bind_existing=True):
    """Archive through the same explicit new/edit identity rule as the UI."""
    data = dict(data)
    pid = (data.get("meta") or {}).get("pit_id")
    if bind_existing and pid in SITE_IDS and not data.get("site_id"):
        data["site_id"] = SITE_IDS[pid]
    r = c.post("/api/archive", json=data)
    body = r.get_json(silent=True) or {}
    if body.get("ok") and body.get("site_id"):
        SITE_IDS[body.get("pit_id") or pid] = body["site_id"]
    return r


def site_id(pid):
    return SITE_IDS[pid]


def load_saved(pid):
    return c.get("/api/load/" + site_id(pid))

# --- page serves, and revalidates ------------------------------------------
r = c.get("/")
check(r.status_code == 200, "GET / serves the page")
etag = r.headers.get("ETag")
check(bool(etag), "GET / sends an ETag")
r304 = c.get("/", headers={"If-None-Match": etag})
check(r304.status_code == 304, "GET / revalidates to 304 when unchanged")
check(len(r304.data) == 0, "304 carries no body")

# --- archive ---------------------------------------------------------------
r = archive(pit("SMOKE20260210"))
body = r.get_json()
check(r.status_code == 200 and body.get("ok"), "archive succeeds")
# reported separately so the archive message can name each kind rather than
# deriving one count from the other
check(body.get("csv_count") == 7, f"csv_count is 7 (got {body.get('csv_count')})")
check(body.get("has_png") is True, "has_png reports the profile figure")
check(body.get("has_pdf") is True, "has_pdf reports the vector copy")
check(body.get("figure_count") == 2, f"two figures written (got {body.get('figure_count')})")

# --- the seven CSVs + the PNG ----------------------------------------------
sub = os.path.join(os.environ["CRYOPIT_EXPORT_DIR"])
found = []
for root, _, files in os.walk(sub):
    found += [f for f in files]
csvs = [f for f in found if f.endswith(".csv")]
pngs = [f for f in found if f.endswith(".png")]
check(len(csvs) == 7, f"seven CSVs on disk (got {len(csvs)})")
check(len(pngs) == 1, f"one profile PNG on disk (got {len(pngs)})")
# figures live in figures/, symmetric with csv/ and uploads/ — the PNG used to
# sit loose at the pit-folder root while the CSVs were in csv/
_pf = [os.path.join(r, f) for r, _d, fs in os.walk(sub) for f in fs
       if f.endswith((".png", ".pdf")) and "SMOKE20260210" in r]
check(all(os.path.basename(os.path.dirname(x)) == "figures" for x in _pf),
      f"figures are in figures/ (got {[os.path.dirname(x).split('/')[-1] for x in _pf]})")
check(any(x.endswith(".pdf") for x in _pf), "a vector PDF is written for papers")
check(any(x.endswith(".png") for x in _pf), "and a PNG for slides and previews")
for _x in _pf:
    with open(_x, "rb") as _fh:
        _magic = _fh.read(5)
    check(_magic.startswith(b"%PDF-") or _magic.startswith(b"\x89PNG"),
          f"{os.path.basename(_x)} is a real file of its type")
check(any("density_gap_filled" in f for f in csvs), "gap-filled density CSV is one of them")

# The siteDetails CSV preserves the checklist's three states. Untouched rows
# are NO_DATA, never silently rewritten as N.
site_details = next(os.path.join(root, f)
                    for root, _, files in os.walk(sub)
                    for f in files if "siteDetails" in f and f.endswith(".csv"))
with open(site_details, encoding="utf-8-sig") as fh:
    site_text = fh.read()
check("# Instrument: SMP,Y (SN SMP-99)" in site_text,
      "siteDetails exports explicit instrument Y and serial")
check("# Instrument: Digital LWC,N" in site_text,
      "siteDetails exports explicit instrument N")
check("# Instrument: Lyte Probe,-9999" in site_text,
      "siteDetails exports unanswered instrument as -9999")
check("# Instrument: Digital LWC,N (SN" not in site_text and
      "# Instrument: Lyte Probe,-9999 (SN" not in site_text,
      "siteDetails never exports serial numbers for N or unanswered rows")

# Normalized storage follows the same Y/N/NULL contract.
_db = sqlite3.connect(os.environ["CRYOPIT_DB_PATH"])
try:
    _states = dict(_db.execute("""SELECT i.name, si.used
        FROM site_instruments si JOIN instruments i USING (instrument_id)
        WHERE si.site_id = ?""", (site_id("SMOKE20260210"),)).fetchall())
    _serials = dict(_db.execute("""SELECT i.name, si.serial_number
        FROM site_instruments si JOIN instruments i USING (instrument_id)
        WHERE si.site_id = ?""", (site_id("SMOKE20260210"),)).fetchall())
finally:
    _db.close()
check(_states.get("SMP") == "Y", "SQLite stores explicit Y")
check(_states.get("Digital LWC") == "N", "SQLite stores explicit N")
check("Lyte Probe" in _states and _states["Lyte Probe"] is None,
      "SQLite stores unanswered as NULL")
check(_serials.get("Digital LWC") is None and _serials.get("Lyte Probe") is None,
      "SQLite clears serials for N and unanswered rows")

# --- load back: the instrument round-trip ----------------------------------
r = load_saved("SMOKE20260210")
loaded = r.get_json()["pit"]
by_name = {i["name"]: i for i in loaded["instruments"]}
check("Pit pictures" in by_name and by_name["Pit pictures"]["used"] == "Y",
      "instrument round-trip keeps 'Pit pictures' (the off-by-one bug)")
check(by_name.get("SMP", {}).get("sn") == "SMP-99",
      "instrument round-trip keeps serial numbers")
check("Avalanche probe" in by_name,
      "instrument round-trip keeps the write-in instrument")
check(by_name.get("Digital LWC", {}).get("used") == "N",
      "instrument round-trip preserves explicit N")
check("Lyte Probe" in by_name and by_name["Lyte Probe"].get("used") is None,
      "instrument round-trip preserves unanswered null")
check(len(loaded["density"]) == 3 and len(loaded["stratigraphy"]) == 3,
      "measurement rows survive the round-trip")

bad = pit("BADSTATE20260210")
bad["instruments"][0]["used"] = "maybe"
r = archive(bad)
check(r.get_json().get("ok") is False and "used must be Y, N" in r.get_json().get("msg", ""),
      "server rejects an invalid instrument category instead of coercing it")

contradict = pit("BADNONE20260210")
contradict["meta"]["no_instruments"] = True
r = archive(contradict)
check(r.get_json().get("ok") is False and "No instruments used" in r.get_json().get("msg", ""),
      "server rejects Yes rows that contradict 'No instruments used'")

# --- download zip ----------------------------------------------------------
r = c.post("/api/download", json=pit("SMOKE20260210"))
check(r.status_code == 200, "download returns 200")
check(r.headers.get("Content-Type") == "application/zip",
      f"download returns a zip, not JSON (got {r.headers.get('Content-Type')})")
check("attachment;" in (r.headers.get("Content-Disposition") or ""),
      "download sets Content-Disposition so the browser saves it")
check(r.headers.get("X-CryoPit-Zipname", "").endswith(".zip"),
      "the zip filename travels in its own header")
# the body IS the zip — no base64, no JSON envelope
zf = zipfile.ZipFile(io.BytesIO(r.data))
names = zf.namelist()

# Cleanup is asserted AFTER the body has been read. The ZIP is spooled to disk
# and streamed, so the file is deliberately still present while the response is
# in flight — that is the whole point of disk-backing it. It is removed when the
# stream closes, which for the test client happens when r.data is consumed
# above. Checking before that read tests the wrong instant and fails on a
# working implementation.
_download_stage = os.path.join(os.environ["CRYOPIT_EXPORT_DIR"], ".download-staging")
check(not os.path.isdir(_download_stage) or not any(
      name.endswith(".zip.part") for name in os.listdir(_download_stage)),
      "download stream cleans its staged ZIP after completion")
check(len([n for n in names if n.endswith(".csv")]) == 7,
      f"zip holds seven CSVs (got {len([n for n in names if n.endswith('.csv')])})")

# --- attachment limits -----------------------------------------------------
# The whole-pit ceiling is NOT the sum any more: stratigraphy is counted per
# layer, so the sum is unbounded by pit. 150 is the point beyond which the
# download path cannot assemble the result, sized so normal work never meets it.
check(_ATTACH_TOTAL == 150, f"whole-pit ceiling is 150 (got {_ATTACH_TOTAL})")
check(_STRAT_PER_LAYER == 20, f"stratigraphy allows 20 per layer (got {_STRAT_PER_LAYER})")


def _register_upload(pid, cat, data, fname, top=None, bottom=None):
    qid = str(uuid.uuid4())
    conn = sqlite3.connect(os.environ["CRYOPIT_DB_PATH"])
    try:
        conn.execute(
            """INSERT INTO attachment_uploads
               (queue_id,site_id,category,original_filename,mime_type,size_bytes,
                client_sha256,top_cm,bottom_cm,status)
               VALUES (?,?,?,?,?,?,?,?,?,'pending')""",
            (qid, site_id(pid), cat, fname, "application/octet-stream", len(data),
             hashlib.sha256(data).hexdigest(), top, bottom))
        conn.commit()
    finally:
        conn.close()
    return qid


def attach(pid, cat, data, fname):
    qid = _register_upload(pid, cat, data, fname)
    return c.post(f"/api/attach/{site_id(pid)}",
                  data={"category": cat, "queue_id": qid,
                        "file": (io.BytesIO(data), fname)},
                  content_type="multipart/form-data").get_json()


def attach_layer(pid, data, fname, top, bottom):
    qid = _register_upload(pid, "stratigraphy", data, fname, top, bottom)
    return c.post(f"/api/attach/{site_id(pid)}",
                  data={"category": "stratigraphy", "queue_id": qid,
                        "top_cm": str(top), "bottom_cm": str(bottom),
                        "file": (io.BytesIO(data), fname)},
                  content_type="multipart/form-data").get_json()


ok = sum(1 for i in range(_STRAT_PER_LAYER + 1)
         if attach_layer("SMOKE20260210", uniq(JPEG, i), f"s{i}.jpg", 100, 62).get("ok"))
check(ok == _STRAT_PER_LAYER,
      f"one layer accepts exactly {_STRAT_PER_LAYER} photos (got {ok})")
# ... and a DIFFERENT layer has its own budget
ok2 = sum(1 for i in range(3)
          if attach_layer("SMOKE20260210", uniq(JPEG, 60 + i), f"t{i}.jpg", 62, 45).get("ok"))
check(ok2 == 3, f"a second layer starts fresh (got {ok2})")
check(_layer_folder(62, 45) == "062-045cm",
      f"folders are named by interval, zero-padded (got {_layer_folder(62, 45)})")
check(_layer_folder(None, None) == "", "an unassigned photo has no layer folder")

ok = sum(1 for i in range(_ATTACH_LIMITS["pitwall"] + 1)
         if attach("SMOKE20260210", "pitwall", uniq(JPEG, i), f"p{i}.jpg").get("ok"))
check(ok == _ATTACH_LIMITS["pitwall"],
      f"pitwall accepts exactly {_ATTACH_LIMITS['pitwall']} (got {ok})")

# --- a layer photo keeps its depths across a re-archive --------------------
# top_cm/bottom_cm are what make a photograph a LAYER photo rather than a
# general shot of the pit. The site row is updated in place and attachment rows are never deleted (attachments are the one child table that cannot be
# regenerated), and that carry-over listed category/filename/sha256/uploaded_at
# and quietly dropped the depths. The file survived, the filename survived, and
# the association did not — on the FIRST correction to any field, because any
# edit re-archives.
archive(pit("LPHOTO20260210"))
attach_layer("LPHOTO20260210", uniq(JPEG, 31), "layer.jpg", 100, 60)
_q = "SELECT filename, top_cm, bottom_cm FROM attachments WHERE site_id=?"
conn = sqlite3.connect(os.environ["CRYOPIT_DB_PATH"])
_before = conn.execute(_q, (site_id("LPHOTO20260210"),)).fetchall()
conn.close()
archive({**pit("LPHOTO20260210")})
conn = sqlite3.connect(os.environ["CRYOPIT_DB_PATH"])
_after = conn.execute(_q, (site_id("LPHOTO20260210"),)).fetchall()
conn.close()
check(_before and _before[0][1] == 100.0 and _before[0][2] == 60.0,
      f"layer photo is stored against its interval (got {_before})")
check(_before == _after,
      f"and keeps that interval across a re-archive (got {_after})")

# --- per-layer density reaches the TABLES, not just raw_json ---------------
# §7's optional density adds two readings and a mean. Only the mean used to be
# written, so SQL against the database saw 285 and had no way back to the 280
# and 290 behind it — while §5, writing to these very columns on its own rows,
# kept both. The round trip hid it: the form reloads from raw_json, which holds
# everything. Anyone reaching the data the way tables are meant to be reached
# got the derived number alone.
_lp = pit("LAYERDEN20260210")
_lp["stratigraphy"] = [
    {"top": 100, "bottom": 60, "gtype": "RG", "hardness": "1F", "wetness": "D",
     "layer_density_a": 280, "layer_density_b": 290, "layer_density": 285},
    {"top": 60, "bottom": 0, "gtype": "FC", "hardness": "4F", "wetness": "D",
     "layer_density_a": 300, "layer_density_b": 320, "layer_density": 310},
]
r = archive({**_lp}).get_json()
check(r.get("ok"), f"archive with per-layer density: {r}")
conn = __import__("sqlite3").connect(os.environ["CRYOPIT_DB_PATH"])
rows = conn.execute(
    "SELECT value_a, value_b, layer_density_kgm3 FROM layers "
    "WHERE site_id=? AND kind='stratigraphy' ORDER BY top_cm DESC",
    (site_id("LAYERDEN20260210"),)).fetchall()
conn.close()
check(rows == [(280.0, 290.0, 285.0), (300.0, 320.0, 310.0)],
      f"both readings and the mean are stored per layer (got {rows})")

# --- pit-sheet rule: one PDF OR up to three images, never a mix -------------
cases = [
    ("3 images then a 4th", [(uniq(JPEG, 1), "a.jpg"), (uniq(PNG, 2), "b.png"),
                             (uniq(JPEG, 3), "c.jpg"), (uniq(JPEG, 4), "d.jpg")],
     [True, True, True, False]),
    ("2 images then a PDF", [(uniq(JPEG, 5), "a.jpg"), (uniq(PNG, 6), "b.png"), (PDF, "s.pdf")],
     [True, True, False]),
    ("PDF then an image", [(PDF, "s.pdf"), (JPEG, "a.jpg")], [True, False]),
    ("PDF then a 2nd PDF", [(PDF, "a.pdf"), (PDF, "b.pdf")], [True, False]),
]
for label, files, expect in cases:
    pid = "SHEET" + str(abs(hash(label)) % 10 ** 6)
    archive(pit(pid))
    got = [bool(attach(pid, "sheet", data, name).get("ok")) for data, name in files]
    check(got == expect, f"pit sheet — {label}: {got} == {expect}")

# --- attachment identity: same bytes = same attachment ---------------------
archive(pit("DUP20260210"))
r1 = attach("DUP20260210", "pitwall", JPEG, "a.jpg")
r2 = attach("DUP20260210", "pitwall", JPEG, "a-renamed.jpg")   # same bytes, new name
r3 = attach("DUP20260210", "pitwall", uniq(JPEG, 99), "b.jpg")   # different bytes
check(r1.get("ok") and not r1.get("duplicate"), "first upload is stored")
check(r2.get("ok") and r2.get("duplicate"), "re-upload of the same bytes is a no-op")
check(r2.get("filename") == r1.get("filename"),
      "duplicate points at the file already stored")
check(r3.get("ok") and not r3.get("duplicate"), "a genuinely different photo is stored")

conn = __import__("sqlite3").connect(os.environ["CRYOPIT_DB_PATH"])
n = conn.execute("SELECT COUNT(*) FROM attachments WHERE site_id=?",
                 (site_id("DUP20260210"),)).fetchone()[0]
check(n == 2, f"two rows for three uploads (got {n})")
idx = conn.execute("SELECT 1 FROM sqlite_master WHERE type='index' "
                   "AND name='idx_attachments_identity'").fetchone()
check(bool(idx), "attachment identity index exists (makes merges idempotent)")
conn.close()

# --- REGRESSION: re-archiving must NOT lose the photo list ------------------
# Photos are uploaded after archiving, so they are not in the form payload.
# Re-archiving updates the pit in place; attachment rows and files must remain untouched.
archive(pit("REARCH20260210"))
for i in range(3):
    attach("REARCH20260210", "pitwall", uniq(JPEG, 200 + i), f"w{i}.jpg")
conn = __import__("sqlite3").connect(os.environ["CRYOPIT_DB_PATH"])
q = lambda: conn.execute(
    "SELECT COUNT(*) FROM attachments WHERE site_id=?",
    (site_id("REARCH20260210"),)).fetchone()[0]
check(q() == 3, "three photos attached")

p2 = pit("REARCH20260210")
p2["meta"]["recorded_by"] = "A. Marshall"      # a typo fix, nothing to do with photos
r = archive(p2).get_json()
check(r.get("ok"), "re-archive succeeds")
check(q() == 3, f"photo list SURVIVES a re-archive (got {q()})")
r = archive(p2).get_json()
check(q() == 3, f"and survives a third archive (got {q()})")
check(load_saved("REARCH20260210").get_json()["pit"]["meta"]["recorded_by"] == "A. Marshall",
      "the correction itself was applied")

# fingerprints survived too, so re-adding the same photo is still a no-op
dup = attach("REARCH20260210", "pitwall", uniq(JPEG, 200), "w0-again.jpg")
check(dup.get("duplicate"), "sha256 fingerprints survive the re-archive")
# scope to THIS pit's folder — other tests in this run wrote their own
pit_dirs = [d for d in os.listdir(os.environ["CRYOPIT_EXPORT_DIR"])
            if "REARCH20260210" in d]
n_files = sum(1 for d in pit_dirs
              for _, _, fs in os.walk(os.path.join(os.environ["CRYOPIT_EXPORT_DIR"], d))
              for f in fs if f.endswith(".jpg"))
check(n_files == 3, f"no orphan files created by re-archiving (got {n_files})")
conn.close()

# --- missing completed folder is detected, never silently recreated ----------
# Once a pit is complete, sites.export_folder is authoritative. If an operator
# deletes that directory, re-archiving must not quietly create a fresh folder
# and conceal the missing photographs or other material that may have lived in
# the original tree. The pit becomes pending and is surfaced for recovery.
import shutil as _sh

archive(pit("REGEN20260210"))
_exp = os.environ["CRYOPIT_EXPORT_DIR"]
_folders = [d for d in os.listdir(_exp)
            if "REGEN20260210" in d and not d.startswith(".")]
check(len(_folders) == 1, "the pit wrote exactly one export folder")
_dir = os.path.join(_exp, _folders[0])
_sh.rmtree(_dir)
check(not os.path.isdir(_dir), "export folder deleted")

def _folder_name(_p):
    """The export folder a pit should occupy, as the app names it."""
    _m = _p.get("meta") or {}
    return f"{_m.get('campaign')}_{_m.get('pit_id')}_{(_m.get('date') or '').replace('-', '')}"


_loaded = load_saved("REGEN20260210").get_json()
check(_loaded.get("ok"), "pit still loads from SQLite when its folder is missing")
r = archive({**_loaded["pit"]})
_body = r.get_json()
check(r.status_code == 409 and not _body.get("ok") and _body.get("pending"),
      "re-archive reports a recoverable storage-integrity failure")
# The pit's campaign and date are unchanged here, so the recorded folder and the
# desired one are the SAME path — and the message says exactly which path is
# missing rather than the generic "neither exists", which is the wording for the
# rename case where the two names differ. Asserting the generic phrase here was
# asking for a less useful message than the code already produces.
check("Recorded pit folder is missing" in (_body.get("msg") or ""),
      f"the failure names the missing folder (got {_body.get('msg')!r})")
check(_folder_name(_loaded["pit"]) in (_body.get("msg") or ""),
      "and identifies it by path, so it can be restored from backup")
_pending = c.get("/api/pits").get_json().get("pending", [])
check(any(x.get("site_id") == site_id("REGEN20260210") for x in _pending),
      "the interrupted pit appears under Needs recovery")

# --- ground temperature: the one table where a negative height is real ------
# The profile runs down the pack (40, 30, 20, 10, 0) and a crew may take a
# single reading BELOW the snow-ground interface. The old rule rejected it.
gp = pit("GROUND20260210")
gp["meta"]["total_depth"] = 40.0
# the helper builds its rows against a 100 cm pit; rebuild them for 40 cm or a
# density blocker fires first and masks what this test is about
gp["density"] = [{"top": 40, "bottom": 0, "a": 250, "b": 260, "c": None}]
gp["stratigraphy"] = [{"top": 40, "bottom": 0, "gtype": "RG",
                       "hardness": "1F", "wetness": "D"}]
gp["temperature"] = [{"height": 40, "temp": -8}, {"height": 20, "temp": -4},
                     {"height": 0, "temp": -1}, {"height": -10, "temp": -0.5}]
r = archive(gp).get_json()
check(r.get("ok"), f"a ground reading at -10 cm archives (msg: {r.get('msg')})")

gp2 = dict(gp)
gp2["temperature"] = [{"height": -25, "temp": -1}]
r = archive(gp2).get_json()
check(not r.get("ok"), "but anything below -10 cm is still refused")
check("soil readings" in (r.get("msg") or ""),
      "and the message explains that negative heights are soil readings")

gp3 = dict(gp)
gp3["temperature"] = [{"height": 60, "temp": -1}]
check(not archive(gp3).get_json().get("ok"),
      "a height above the pit is still refused")

# --- HEIC: iPhones shoot it by default, so it WILL arrive ------------------
def _heic_bytes():
    try:
        import io as _io
        import pillow_heif
        from PIL import Image
        pillow_heif.register_heif_opener()
        b = _io.BytesIO()
        Image.new("RGB", (64, 48), (120, 140, 200)).save(b, format="HEIF", quality=90)
        return b.getvalue()
    except Exception:
        return None


_heic = _heic_bytes()
if _heic is None:
    print("SKIP HEIC tests (pillow-heif unavailable)")
else:
    from cryopit.web import _sniff, _is_heif
    check(_is_heif(_heic), "HEIC is recognised by its ftyp brand")
    check(_sniff(_heic) == "heic", "and sniffed as heic rather than rejected")

    archive(pit("HEIC20260210"))
    r = attach("HEIC20260210", "pitwall", _heic, "IMG_4821.HEIC")
    check(r.get("ok"), f"an iPhone HEIC uploads (msg: {r.get('msg')})")
    check(r.get("converted_from") == "heic", "and reports that it was converted")
    check(r.get("filename", "").endswith(".jpg"),
          f"stored as JPEG so anything can open it (got {r.get('filename')})")

    # full resolution: only the compression changes, never the pixel count
    from PIL import Image as _Im
    src = _Im.open(io.BytesIO(_heic)).size
    got = None
    for _root, _d, _fs in os.walk(os.environ["CRYOPIT_EXPORT_DIR"]):
        for _f in _fs:
            if _f.endswith("pitwall_01.jpg") and "HEIC20260210" in _root:
                got = _Im.open(os.path.join(_root, _f)).size
    check(got == src, f"every pixel is kept ({src} -> {got})")

    # conversion is deterministic, so deduplication still works on HEIC
    r2 = attach("HEIC20260210", "pitwall", _heic, "again.HEIC")
    check(r2.get("duplicate"), "the same HEIC twice is still caught as a duplicate")

# Only HEIC is converted. PNG and WebP are already universally readable, so
# re-encoding them would be lossy for no gain — docs/PHOTOGRAPHS.md says so.
archive(pit("FMT20260210"))
r = attach("FMT20260210", "pitwall", uniq(PNG, 7), "shot.png")
check(r.get("ok") and r.get("filename", "").endswith(".png"),
      f"a PNG stays a PNG (got {r.get('filename')})")
check(not r.get("converted_from"), "and is not reported as converted")

# One wide shot may document several layers, so the LAYER is part of an
# attachment's identity. Without it the second layer was refused as a duplicate.
archive(pit("LAYERDUP20260210"))
_wide = uniq(JPEG, 200)


def _lay(top, bot):
    return attach_layer("LAYERDUP20260210", _wide, "wide.jpg", top, bot)


check(_lay(100, 62).get("ok"), "a layer photograph attaches")
r1 = _lay(62, 45)
check(r1.get("ok") and not r1.get("duplicate"),
      "the same photograph on a DIFFERENT layer is accepted")
r2 = _lay(62, 45)
check(r2.get("duplicate"), "but on the SAME layer it is a duplicate")
check("062-045cm" in (r2.get("msg") or ""),
      f"and the message names the layer (got {r2.get('msg')})")

# ground temperature is marked in the CSV header, not left as a bare negative
gcsv = _build = None
from cryopit.export import _build_csvs
_gp = pit("GT2")
_gp["meta"]["total_depth"] = 40.0
_gp["temperature"] = [{"height": 40, "temp": -8}, {"height": -10, "temp": -0.5}]
for _n, _t in _build_csvs(_gp).items():
    if "temperature" in _n:
        gcsv = _t
check("# Soil temperature,Yes" in gcsv,
      "a negative height marks the temperature CSV as holding a soil reading")
check("10" in [l.split(",")[-1] for l in gcsv.splitlines()
               if l.startswith("# Soil temperature depths")][0],
      "and records the depth below the interface")
_gp["temperature"] = [{"height": 40, "temp": -8}]
for _n, _t in _build_csvs(_gp).items():
    if "temperature" in _n:
        check("# Soil temperature,No" in _t, "a normal profile says No")

# Coverage is stated once for the profiles present, not tagged onto every
# derived value, and Extra is omitted when no profile used it.
from cryopit.export import _build_csvs as _bc
_cp = pit("COV")
_cp["density"] = [{"top": 100, "bottom": 50, "a": 250, "b": 260, "c": None}]
_g = [t for n, t in _bc(_cp).items() if "gap" in n][0]
check(any(l.startswith("# Measured coverage A / B (%)") for l in _g.splitlines()),
      "coverage is one line naming the profiles present")
check("Extra" not in _g.split("# Derived")[0],
      "Extra is absent from the header when no profile used it")
check("% measured]" not in _g, "and is not repeated as a tag on every value")
_cp["density"] = [{"top": 100, "bottom": 50, "a": 250, "b": 260, "c": 255}]
_g2 = [t for n, t in _bc(_cp).items() if "gap" in n][0]
check(any(l.startswith("# Measured coverage A / B / Extra (%)") for l in _g2.splitlines()),
      "Extra appears once it is used")

print(f"\n{_pass} passed, {_fail} failed")
sys.exit(1 if _fail else 0)
