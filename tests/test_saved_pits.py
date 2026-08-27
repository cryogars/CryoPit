"""Stage 10 owner-scoped Saved Pits search, filters and pagination tests."""
from __future__ import annotations

import importlib
import os
import shutil
import sqlite3
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TMP = Path(tempfile.mkdtemp(prefix="cryopit-stage10-"))
os.environ["CRYOPIT_DB_PATH"] = str(TMP / "stage10.db")
os.environ["CRYOPIT_EXPORT_DIR"] = str(TMP / "exports")
os.environ["CRYOPIT_SAVED_PITS_LIMIT"] = "2"

PKG = "_cryopit_stage10_test"
pkg = types.ModuleType(PKG)
pkg.__path__ = [str(ROOT / "cryopit")]
sys.modules[PKG] = pkg


class _Blueprint:
    def __init__(self, *_a, **_k):
        pass

    @staticmethod
    def _decorator(*_a, **_k):
        return lambda fn: fn

    get = post = before_app_request = _decorator


class _Abort(RuntimeError):
    pass


def _abort(code, description=None):
    raise _Abort(f"{code}: {description}")


request = types.SimpleNamespace(args={}, headers={}, path="/api/pits", method="GET")
flask_stub = types.ModuleType("flask")
flask_stub.Blueprint = _Blueprint
flask_stub.Response = lambda body=None, **kwargs: (body, kwargs)
flask_stub.abort = _abort
flask_stub.jsonify = lambda obj=None, **kwargs: obj if obj is not None else kwargs
flask_stub.request = request
flask_stub.has_request_context = lambda: False
sys.modules["flask"] = flask_stub

db = importlib.import_module(f"{PKG}.db")
repository = importlib.import_module(f"{PKG}.repository")
web = importlib.import_module(f"{PKG}.web")

CURRENT_OWNER = "alice"
repository.current_user = lambda: CURRENT_OWNER


def reset():
    global CURRENT_OWNER
    CURRENT_OWNER = "alice"
    db_path = Path(os.environ["CRYOPIT_DB_PATH"])
    if db_path.exists():
        db_path.unlink()
    if TMP.joinpath("exports").exists():
        shutil.rmtree(TMP / "exports")
    db.init_db()
    conn = sqlite3.connect(db_path)
    try:
        conn.executemany("INSERT OR IGNORE INTO campaigns(name) VALUES(?)",
                         [("WY2026",), ("WY2027",), ("SECRET",)])
        campaigns = dict(conn.execute("SELECT name,campaign_id FROM campaigns"))

        def site(sid, pit_id, owner, campaign, date, place, location, updated,
                 recorder="Recorder", surveyors="Surveyor", pending=None):
            conn.execute(
                """INSERT INTO sites(site_id,pit_id,owner,campaign_id,date,site,location,
                                     recorded_by,surveyors,export_folder,
                                     pending_export_folder,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (sid, pit_id, owner, campaigns[campaign], date, place, location,
                 recorder, surveyors, None if pending else f"{campaign}_{pit_id}_{date}",
                 pending, updated, updated))

        site("a-1", "ALPHA-01", "alice", "WY2026", "2026-01-10", "Upper Ridge",
             "Grand Mesa", "2026-03-01 10:00:00", "Jane Recorder", "Ben Crew")
        site("a-2", "BRAVO-02", "alice", "WY2027", "2027-02-11", "Lower Basin",
             "Mores Creek", "2027-02-12 09:00:00", "Nora Notes", "Jane Smith")
        site("a-3", "CHARLIE-03", "alice", "WY2026", "2026-02-15", "Upper Ridge",
             "Grand Mesa", "2026-02-16 08:00:00")
        site("a-4", "PIT%20", "alice", "WY2026", "2026-02-20", "Percent Site",
             "Grand Mesa", "2026-02-21 08:00:00")
        site("a-pending", "RECOVER-01", "alice", "WY2026", "2026-03-01", "Upper Ridge",
             "Grand Mesa", "2026-03-02 08:00:00", pending="WY2026_RECOVER-01_20260301")
        site("b-1", "BOB-PRIVATE", "bob", "SECRET", "2028-01-01", "Upper Ridge",
             "Secret Place", "2028-01-02 08:00:00", "Jane Smith", "Ben Crew")

        conn.execute("INSERT INTO observers(name) VALUES('Dr Observer')")
        observer_id = conn.execute(
            "SELECT observer_id FROM observers WHERE name='Dr Observer'").fetchone()[0]
        conn.execute("INSERT INTO site_observers(site_id,observer_id,role) VALUES(?,?,?)",
                     ("a-3", observer_id, "surveyor"))

        conn.execute(
            """INSERT INTO attachment_uploads(queue_id,site_id,status,category,
                                               original_filename,created_at,updated_at)
               VALUES('11111111-1111-4111-8111-111111111111','a-1','pending',
                      'pitwall','pending.jpg',datetime('now'),datetime('now'))""")
        conn.execute(
            """INSERT INTO attachments(site_id,category,filename,sha256,storage_status,pending_delete)
               VALUES('a-1','pitwall','stored.jpg',?, 'stored',0)""", ("a" * 64,))
        conn.execute(
            """INSERT INTO attachments(site_id,category,filename,sha256,storage_status,pending_delete)
               VALUES('a-1','pitwall','missing.jpg',?, 'missing',0)""", ("b" * 64,))
        conn.execute(
            """INSERT INTO attachments(site_id,category,filename,sha256,storage_status,pending_delete)
               VALUES('a-1','pitwall','deleting.jpg',?, 'delete_pending',1)""", ("c" * 64,))
        conn.commit()
    finally:
        conn.close()


def test_owner_scope_and_recovery_separation():
    reset()
    result = repository.search_pits(limit=50)
    assert result["total"] == 4
    assert [p["pit_id"] for p in result["pits"]] == [
        "BRAVO-02", "PIT%20", "CHARLIE-03", "ALPHA-01"]
    assert {p["site_id"] for p in result["pits"]} == {"a-1", "a-2", "a-3", "a-4"}
    assert all(p["pit_id"] != "BOB-PRIVATE" for p in result["pits"])
    pending = repository.list_pending_pits()
    assert [p["site_id"] for p in pending] == ["a-pending"]


def test_searches_pit_site_campaign_date_and_observer():
    reset()
    assert [p["site_id"] for p in repository.search_pits(query="alpha")["pits"]] == ["a-1"]
    assert {p["site_id"] for p in repository.search_pits(query="upper ridge")["pits"]} == {"a-1", "a-3"}
    assert [p["site_id"] for p in repository.search_pits(query="WY2027")["pits"]] == ["a-2"]
    assert [p["site_id"] for p in repository.search_pits(query="2026-01-10")["pits"]] == ["a-1"]
    assert [p["site_id"] for p in repository.search_pits(query="Jane Smith")["pits"]] == ["a-2"]
    assert [p["site_id"] for p in repository.search_pits(query="Dr Observer")["pits"]] == ["a-3"]
    assert [p["site_id"] for p in repository.search_pits(query="%")["pits"]] == ["a-4"]


def test_filters_sort_and_pagination_are_stable():
    reset()
    filtered = repository.search_pits(campaign="WY2026", date_from="2026-02-01",
                                      date_to="2026-02-28", sort="pit_id", limit=20)
    assert [p["pit_id"] for p in filtered["pits"]] == ["CHARLIE-03", "PIT%20"]

    page1 = repository.search_pits(limit=2, offset=0, sort="pit_id")
    page2 = repository.search_pits(limit=2, offset=2, sort="pit_id")
    assert page1["total"] == page2["total"] == 4
    assert page1["has_more"] is True and page2["has_more"] is False
    ids = [p["site_id"] for p in page1["pits"] + page2["pits"]]
    assert len(ids) == len(set(ids)) == 4
    assert [p["pit_id"] for p in page1["pits"] + page2["pits"]] == [
        "ALPHA-01", "BRAVO-02", "CHARLIE-03", "PIT%20"]


def test_status_counts_and_campaign_facets():
    reset()
    alpha = repository.search_pits(query="ALPHA")["pits"][0]
    assert alpha["pending_photos"] == 1
    assert alpha["attachment_count"] == 2
    assert alpha["missing_attachments"] == 1
    assert repository.list_owner_campaigns() == [
        {"name": "WY2026", "count": 3}, {"name": "WY2027", "count": 1}]


def test_api_defaults_to_newest_observation_date():
    reset()
    request.args = {}
    response = web.api_pits()
    assert [p["pit_id"] for p in response["pits"]] == ["BRAVO-02", "PIT%20"]


def test_api_exposes_filters_without_accepting_an_owner():
    reset()
    request.args = {"q": "Upper Ridge", "campaign": "WY2026", "sort": "pit_id",
                    "limit": "1", "offset": "0"}
    response = web.api_pits()
    assert response["total"] == 2 and len(response["pits"]) == 1
    assert response["pits"][0]["site_id"] == "a-1"
    assert response["has_more"] is True
    assert response["campaigns"][0] == {"name": "WY2026", "count": 3}
    assert response["pending"][0]["site_id"] == "a-pending"
    assert "owner" not in response


def test_api_rejects_invalid_dates_and_sort():
    reset()
    request.args = {"date_from": "02/01/2026"}
    try:
        web.api_pits()
    except _Abort as exc:
        assert str(exc).startswith("400:")
    else:
        raise AssertionError("invalid date was accepted")
    request.args = {"sort": "owner"}
    try:
        web.api_pits()
    except _Abort as exc:
        assert str(exc).startswith("400:")
    else:
        raise AssertionError("arbitrary sort was accepted")
    request.args = {"date_from": "2027-01-01", "date_to": "2026-01-01"}
    try:
        web.api_pits()
    except _Abort as exc:
        assert "date_from" in str(exc)
    else:
        raise AssertionError("inverted date range was accepted")



def test_assembled_page_contains_accessible_finder_controls():
    html = web._render_form()
    assert 'id="saved-pits-search"' in html
    assert 'role="search"' in html
    assert 'id="saved-pits-more"' in html
    assert 'id="recovery-pits"' in html
    assert '<option value="date" selected>Observation date — newest first</option>' in html
    assert 'initSavedPitsFinder(); loadSavedPits();' in html


def test_saved_pit_indexes_migrate_on_existing_database():
    reset()
    conn = sqlite3.connect(os.environ["CRYOPIT_DB_PATH"])
    try:
        names = {row[1] for row in conn.execute("PRAGMA index_list('sites')")}
        assert {"idx_sites_owner_pit_search", "idx_sites_owner_site_search",
                "idx_sites_owner_campaign_date", "idx_sites_pending"} <= names
        attachment_names = {row[1] for row in conn.execute("PRAGMA index_list('attachments')")}
        assert "idx_attachments_site_storage" in attachment_names
    finally:
        conn.close()


TESTS = [v for k, v in list(globals().items()) if k.startswith("test_")]
if __name__ == "__main__":
    failures = 0
    for test in TESTS:
        try:
            test()
            print("PASS", test.__name__)
        except Exception as exc:
            failures += 1
            print("FAIL", test.__name__, repr(exc))
    if failures:
        raise SystemExit(f"{failures} Stage 10 saved-pits tests failed")
    print(f"{len(TESTS)} Stage 10 saved-pits tests passed")
