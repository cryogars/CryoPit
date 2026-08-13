"""Three-state instrument persistence tests.

Runs without Flask so the Y / N / unanswered contract can be tested in the
smallest CI environment:

    python3 tests/test_instrument_state.py
"""
from __future__ import annotations

import importlib
import json
import os
import sqlite3
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TMP = Path(tempfile.mkdtemp(prefix="cryopit-instruments-"))
os.environ["CRYOPIT_DB_PATH"] = str(TMP / "test.db")
os.environ["CRYOPIT_EXPORT_DIR"] = str(TMP / "exports")

# Import the submodules under a private package alias so this plain-script test
# does not execute cryopit/__init__.py or disturb normal imports when collected
# by another runner. auth.py needs only these two Flask symbols in local mode.
PKG = "_cryopit_instrument_test"
pkg = types.ModuleType(PKG)
pkg.__path__ = [str(ROOT / "cryopit")]
sys.modules[PKG] = pkg
_had_flask = "flask" in sys.modules
if not _had_flask:
    flask_stub = types.ModuleType("flask")
    flask_stub.request = types.SimpleNamespace(headers={})
    flask_stub.has_request_context = lambda: False
    sys.modules["flask"] = flask_stub
try:
    db = importlib.import_module(f"{PKG}.db")
    export = importlib.import_module(f"{PKG}.export")
    repository = importlib.import_module(f"{PKG}.repository")
finally:
    if not _had_flask:
        sys.modules.pop("flask", None)


def payload(pid: str):
    return {
        "meta": {
            "pit_id": pid,
            "campaign": "WY2026",
            "date": "2026-08-04",
            "total_depth": 10,
            "recorded_by": "Tester",
            "surveyors": "",
            "no_instruments": False,
            "no_tasks": False,
        },
        "weather": {},
        "ground": {},
        "temperature": [],
        "density": [],
        "lwc": [],
        "stratigraphy": [],
        "ssa": [],
        "ssa_calibration": {},
        "instruments": [
            {"name": "SMP", "sn": "S-1", "used": "Y"},
            {"name": "Digital LWC", "sn": "", "used": "N"},
            {"name": "Lyte Probe", "sn": "", "used": None},
            {"name": "Pit pictures", "sn": "", "used": "N"},
        ],
    }


def normalized(site_id: str):
    conn = sqlite3.connect(os.environ["CRYOPIT_DB_PATH"])
    try:
        rows = conn.execute(
            """SELECT i.name, si.used, si.serial_number
                 FROM site_instruments si
                 JOIN instruments i USING (instrument_id)
                WHERE si.site_id = ?""",
            (site_id,),
        ).fetchall()
        raw = conn.execute(
            "SELECT raw_json FROM sites WHERE site_id = ?", (site_id,)
        ).fetchone()[0]
        return {name: (used, serial) for name, used, serial in rows}, json.loads(raw)
    finally:
        conn.close()


def test_normalizer():
    assert repository._instrument_used("Y") == "Y"
    assert repository._instrument_used("N") == "N"
    assert repository._instrument_used(None) is None
    assert repository._instrument_used("") is None
    try:
        repository._instrument_used("maybe")
    except ValueError:
        pass
    else:
        raise AssertionError("invalid category was accepted")


def test_database_and_raw_json():
    p = payload("TRI20260804")
    status, info = repository.save_pit(p, pending_export_folder="WY2026_TRI20260804_20260804")
    assert status == "ok"
    rows, raw = normalized(info["site_id"])
    assert rows["SMP"] == ("Y", "S-1")
    assert rows["Digital LWC"] == ("N", None)
    assert rows["Lyte Probe"] == (None, None)
    by_name = {item["name"]: item for item in raw["instruments"]}
    assert by_name["Digital LWC"]["used"] == "N"
    assert by_name["Lyte Probe"]["used"] is None


def test_overwrite_preserves_null():
    p = payload("OVERWRITE20260804")
    status, info = repository.save_pit(p, pending_export_folder="WY2026_OVERWRITE20260804_20260804")
    assert status == "ok"
    p["instruments"][0]["used"] = None
    p["instruments"][0]["sn"] = ""
    assert repository.save_pit(p, site_id=info["site_id"],
                               pending_export_folder="WY2026_OVERWRITE20260804_20260804")[0] == "ok"
    rows, raw = normalized(info["site_id"])
    assert rows["SMP"] == (None, None)
    assert {x["name"]: x for x in raw["instruments"]}["SMP"]["used"] is None


def test_csv_three_states():
    p = payload("CSV20260804")
    # Defensive export behavior: even a caller that bypasses archive validation
    # cannot leak a stale serial for N or unanswered.
    p["instruments"][1]["sn"] = "stale-n"
    p["instruments"][2]["sn"] = "stale-u"
    csvs = export._build_csvs(p)
    text = next(value for name, value in csvs.items() if "siteDetails" in name)
    assert "# Instrument: SMP,Y (SN S-1)" in text
    assert "# Instrument: Digital LWC,N" in text
    assert "# Instrument: Lyte Probe,-9999" in text
    assert "stale-n" not in text and "stale-u" not in text


def test_invalid_and_contradictory_payloads():
    bad = payload("BADSTATE20260804")
    bad["instruments"][0]["used"] = "maybe"
    status, msg = repository.save_pit(bad)
    assert status == "error" and "used must be Y, N" in msg

    stale = payload("BADSERIAL20260804")
    stale["instruments"][1]["sn"] = "not-valid-for-N"
    status, msg = repository.save_pit(stale)
    assert status == "error" and "serial number requires Used=Y" in msg

    no_inst = payload("NOINST20260804")
    no_inst["meta"]["no_instruments"] = True
    status, msg = repository.save_pit(no_inst)
    assert status == "error" and "No instruments used" in msg

    no_tasks = payload("NOTASKS20260804")
    no_tasks["meta"]["no_tasks"] = True
    no_tasks["instruments"][-1]["used"] = "Y"
    status, msg = repository.save_pit(no_tasks)
    assert status == "error" and "No tasks done" in msg

    consistent = payload("CONSISTENT20260804")
    consistent["meta"]["no_instruments"] = True
    for item in consistent["instruments"]:
        if item["name"] != "Pit pictures":
            item["used"] = "N"
            item["sn"] = ""
    assert repository.save_pit(consistent)[0] == "ok"


if __name__ == "__main__":
    db.init_db()
    tests = [value for name, value in sorted(globals().items())
             if name.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"{len(tests)} instrument-state tests passed")
