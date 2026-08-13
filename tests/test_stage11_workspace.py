"""Stage 11 owner-scoped workspace summary and assembled-page tests."""
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
TMP = Path(tempfile.mkdtemp(prefix="cryopit-stage11-"))
os.environ["CRYOPIT_DB_PATH"] = str(TMP / "stage11.db")
os.environ["CRYOPIT_EXPORT_DIR"] = str(TMP / "exports")
os.environ["CRYOPIT_ENABLE_EDIT"] = "true"

PKG = "_cryopit_stage11_test"
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


request = types.SimpleNamespace(args={}, headers={}, path="/api/workspace", method="GET")
flask_stub = types.ModuleType("flask")
flask_stub.Blueprint = _Blueprint
flask_stub.Response = lambda body=None, **kwargs: (body, kwargs)
flask_stub.abort = lambda code, description=None: (_ for _ in ()).throw(RuntimeError(f"{code}: {description}"))
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
    path = Path(os.environ["CRYOPIT_DB_PATH"])
    if path.exists():
        path.unlink()
    shutil.rmtree(TMP / "exports", ignore_errors=True)
    db.init_db()
    conn = sqlite3.connect(path)
    try:
        conn.execute("INSERT OR IGNORE INTO campaigns(name) VALUES('WY2026')")
        campaign_id = conn.execute("SELECT campaign_id FROM campaigns WHERE name='WY2026'").fetchone()[0]

        def add_site(site_id, pit_id, owner, updated, pending=None, date="2026-01-20"):
            conn.execute(
                """INSERT INTO sites(site_id,pit_id,owner,campaign_id,date,site,location,
                                     recorded_by,surveyors,export_folder,pending_export_folder,
                                     created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (site_id, pit_id, owner, campaign_id, date, "Upper Ridge",
                 "Grand Mesa", "Recorder", "Observer",
                 None if pending else f"WY2026_{pit_id}_20260120", pending, updated, updated),
            )

        # The most recently modified pit deliberately has the older observation
        # date. Workspace Recent Pits must remain update-ordered even though the
        # Saved Pits finder now defaults to observation date.
        add_site("a-new", "ALICE-NEW", "alice", "2026-03-03 12:00:00", date="2025-12-01")
        add_site("a-old", "ALICE-OLD", "alice", "2026-02-01 12:00:00", date="2026-02-01")
        add_site("a-recover", "ALICE-RECOVER", "alice", "2026-03-02 12:00:00",
                 pending="WY2026_ALICE-RECOVER_20260120")
        add_site("b-private", "BOB-PRIVATE", "bob", "2026-04-01 12:00:00")

        conn.execute(
            """INSERT INTO attachment_uploads(queue_id,site_id,status,category,
                                               original_filename,created_at,updated_at)
               VALUES('11111111-1111-4111-8111-111111111111','a-new','pending',
                      'pitwall','alice.jpg',datetime('now'),datetime('now'))"""
        )
        conn.execute(
            """INSERT INTO attachment_uploads(queue_id,site_id,status,category,
                                               original_filename,created_at,updated_at)
               VALUES('22222222-2222-4222-8222-222222222222','b-private','pending',
                      'pitwall','bob.jpg',datetime('now'),datetime('now'))"""
        )
        conn.execute(
            """INSERT INTO attachments(site_id,category,filename,sha256,storage_status,pending_delete)
               VALUES('a-old','pitwall','missing.jpg',?,'missing',0)""",
            ("a" * 64,),
        )
        conn.execute(
            """INSERT INTO attachments(site_id,category,filename,sha256,storage_status,pending_delete)
               VALUES('b-private','pitwall','private-missing.jpg',?,'missing',0)""",
            ("b" * 64,),
        )
        conn.commit()
    finally:
        conn.close()


def test_workspace_summary_is_owner_scoped():
    reset()
    summary = repository.workspace_summary(recent_limit=3)
    assert [p["pit_id"] for p in summary["recent"]] == ["ALICE-NEW", "ALICE-OLD"]
    assert summary["total_pits"] == 2
    assert [p["pit_id"] for p in summary["recovery"]] == ["ALICE-RECOVER"]
    assert summary["recovery_count"] == 1
    assert summary["expected_photos"] == 1
    assert summary["missing_attachments"] == 1
    assert "BOB-PRIVATE" not in repr(summary)


def test_workspace_api_uses_authenticated_identity_only():
    reset()
    request.args = {"owner": "bob"}
    response = web.api_workspace()
    assert response["total_pits"] == 2
    assert response["recent"][0]["pit_id"] == "ALICE-NEW"
    assert "owner" not in response
    assert "BOB-PRIVATE" not in repr(response)


def test_workspace_page_is_the_initial_operational_view():
    reset()
    html = web._render_form()
    assert 'id="workspace"' in html
    assert 'id="workspace-new"' in html
    assert 'id="workspace-find"' in html
    assert 'id="workspace-current"' in html
    assert 'id="workspace-recent"' in html
    assert 'id="workspace-recovery"' in html
    assert 'id="workspace-photo-summary"' in html
    assert 'id="app-shell" hidden' in html
    assert 'initWorkspace();' in html
    assert 'Hosted CryoPit is designed to use your institution’s SSO identity' in html


def test_workspace_endpoint_does_not_expose_browser_local_photo_bytes():
    reset()
    response = web.api_workspace()
    assert set(response) == {
        "recent", "total_pits", "recovery", "recovery_count",
        "expected_photos", "missing_attachments",
    }
    assert "file" not in repr(response).lower()
    assert "blob" not in repr(response).lower()


def test_workspace_listing_is_empty_when_saved_record_access_is_disabled():
    reset()
    original = web.ENABLE_EDIT
    try:
        web.ENABLE_EDIT = False
        response = web.api_workspace()
        assert response == {
            "recent": [], "total_pits": 0, "recovery": [],
            "recovery_count": 0, "expected_photos": 0,
            "missing_attachments": 0,
        }
    finally:
        web.ENABLE_EDIT = original


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
        raise SystemExit(f"{failures} Stage 11 workspace tests failed")
    print(f"{len(TESTS)} Stage 11 workspace tests passed")
