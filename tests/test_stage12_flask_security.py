"""Full Flask boundary tests for the Stage 12 release candidate.

These deliberately use real routes and two authenticated identities. They are
skipped only when the deployment environment has not installed Flask.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import sqlite3
import sys
import tempfile
import uuid
import zipfile
from pathlib import Path
from unittest.mock import patch

try:
    import flask  # noqa: F401
except ImportError:
    raise SystemExit("Flask is required: install requirements.lock before running this suite")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="cryopit-stage12-flask-"))
os.environ.update({
    "CRYOPIT_DB_PATH": str(TMP / "security.db"),
    "CRYOPIT_EXPORT_DIR": str(TMP / "exports"),
    "CRYOPIT_ENABLE_EDIT": "true",
    "CRYOPIT_TRUST_PROXY_AUTH": "true",
    "CRYOPIT_AUTH_HEADER": "X-Remote-User",
    "CRYOPIT_SECRET_KEY": "stage12-test-secret-0123456789-abcdefghijklmnopqrstuvwxyz",
    "CRYOPIT_MAX_BODY_MB": "4",
    "CRYOPIT_ATTACHMENT_MAX_MB": "1",
    "CRYOPIT_RATE_LIMIT_WRITES_PER_MINUTE": "1000",
    "CRYOPIT_RATE_LIMIT_UPLOADS_PER_MINUTE": "1000",
    "CRYOPIT_RATE_LIMIT_EXPORTS_PER_MINUTE": "1000",
})

from cryopit import make_app  # noqa: E402
import cryopit.web as web  # noqa: E402

# Keep boundary tests fast and deterministic; plotting has its own suite.
web._render_figures = lambda payload: (b"stage12-png", b"stage12-pdf")
app = make_app()
app.config["TESTING"] = True

@app.get("/api/stage12-boom")
def _boom():
    raise RuntimeError("/srv/secret/cryopit.db should never be returned")

client = app.test_client()


def identity(user):
    return {"X-Remote-User": user}


def token_for(user):
    response = client.get("/", headers=identity(user))
    assert response.status_code == 200
    match = re.search(r"const CSRF_TOKEN = '([^']+)'", response.get_data(as_text=True))
    assert match
    return match.group(1)


TOKENS = {u: token_for(u) for u in ("alice-subject", "bob-subject")}


def headers(user, *, csrf=False, **extra):
    out = identity(user)
    if csrf:
        out["X-CryoPit-CSRF"] = TOKENS[user]
    out.update(extra)
    return out


def pit(pid, site):
    return {
        "meta": {"pit_id": pid, "location": "Grand Mesa", "site": site,
                 "campaign": "WY2026", "date": "2026-02-10", "total_depth": 100,
                 "recorded_by": "Recorder", "surveyors": "Observer"},
        "weather": {}, "ground": {}, "temperature": [], "density": [],
        "lwc": [], "ssa": [], "stratigraphy": [], "instruments": [],
        "ssa_calibration": {},
    }


def archive(user, payload):
    response = client.post("/api/archive", json=payload, headers=headers(user, csrf=True))
    body = response.get_json()
    assert response.status_code == 200 and body["ok"], body
    return body["site_id"]


ALICE_ID = archive("alice-subject", pit("ALICE-001", "Alice Ridge"))
BOB_ID = archive("bob-subject", pit("BOB-001", "Bob Ridge"))


def test_proxy_auth_fails_closed_but_health_is_public():
    assert client.get("/").status_code == 401
    assert client.get("/api/pits").status_code == 401
    assert client.get("/healthz").status_code == 200
    assert client.get("/readyz").status_code == 200


def test_security_headers_are_on_page_api_and_health():
    for response in (
        client.get("/", headers=headers("alice-subject")),
        client.get("/api/pits", headers=headers("alice-subject")),
        client.get("/healthz"),
    ):
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["X-Frame-Options"] == "DENY"
        assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
        assert response.headers["Referrer-Policy"] == "no-referrer"
        assert response.headers.get("X-Request-ID")


def test_cross_site_api_and_missing_csrf_are_rejected():
    cross = client.get("/api/pits", headers=headers(
        "alice-subject", **{"Sec-Fetch-Site": "cross-site"}))
    assert cross.status_code == 403
    no_token = client.post("/api/archive", json=pit("NO-TOKEN", "No Token"),
                           headers=headers("alice-subject"))
    assert no_token.status_code == 403
    wrong_owner_token = headers("alice-subject")
    wrong_owner_token["X-CryoPit-CSRF"] = TOKENS["bob-subject"]
    wrong = client.post("/api/archive", json=pit("WRONG-TOKEN", "Wrong Token"),
                        headers=wrong_owner_token)
    assert wrong.status_code == 403


def test_each_user_searches_only_their_own_pits():
    alice = client.get("/api/pits", headers=headers("alice-subject")).get_json()
    bob = client.get("/api/pits", headers=headers("bob-subject")).get_json()
    assert [p["pit_id"] for p in alice["pits"]] == ["ALICE-001"]
    assert [p["pit_id"] for p in bob["pits"]] == ["BOB-001"]
    assert "BOB-001" not in repr(alice)
    assert "ALICE-001" not in repr(bob)


def test_known_site_id_cannot_cross_owner_boundary():
    load = client.get(f"/api/load/{BOB_ID}", headers=headers("alice-subject")).get_json()
    assert load["ok"] is False and "Bob Ridge" not in repr(load)

    changed = pit("BOB-HIJACK", "Hijacked")
    changed["site_id"] = BOB_ID
    update = client.post("/api/archive", json=changed,
                         headers=headers("alice-subject", csrf=True)).get_json()
    assert update["ok"] is False

    attachments = client.get(f"/api/attachments/{BOB_ID}",
                             headers=headers("alice-subject")).get_json()
    assert "bob" not in repr(attachments).lower()


def _install_bob_secret_attachment():
    conn = sqlite3.connect(os.environ["CRYOPIT_DB_PATH"])
    try:
        folder = conn.execute("SELECT export_folder FROM sites WHERE site_id=?", (BOB_ID,)).fetchone()[0]
        rel = Path("uploads") / "pitwall" / "bob-secret.jpg"
        path = Path(os.environ["CRYOPIT_EXPORT_DIR"]) / folder / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\xff\xd8\xffbob-secret")
        cur = conn.execute(
            "INSERT INTO attachments(site_id,category,filename,sha256,storage_status,pending_delete) "
            "VALUES(?,?,?,?,?,0)",
            (BOB_ID, "pitwall", "bob-secret.jpg", "a" * 64, "stored"),
        )
        conn.commit()
        return cur.lastrowid, path
    finally:
        conn.close()


BOB_ATTACHMENT_ID, BOB_ATTACHMENT_PATH = _install_bob_secret_attachment()


def test_cross_owner_download_and_delete_do_not_disclose_or_modify_attachment():
    payload = pit("ALICE-DOWNLOAD", "Alice Download")
    payload["site_id"] = BOB_ID
    response = client.post("/api/download", json=payload,
                           headers=headers("alice-subject", csrf=True))
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.data)) as zf:
        assert not any("bob-secret" in name for name in zf.namelist())

    deletion = client.post(f"/api/attachment/{BOB_ID}/{BOB_ATTACHMENT_ID}/delete",
                           headers=headers("alice-subject", csrf=True))
    assert deletion.status_code in {404, 409}
    assert BOB_ATTACHMENT_PATH.exists()


def test_cross_owner_recovery_is_denied_without_disclosure():
    conn = sqlite3.connect(os.environ["CRYOPIT_DB_PATH"])
    try:
        conn.execute("UPDATE sites SET pending_export_folder='pending-bob' WHERE site_id=?", (BOB_ID,))
        conn.commit()
    finally:
        conn.close()
    with patch.object(__import__("logging").getLogger(web.__name__), "exception") as expected_log:
        response = client.post(f"/api/recover/{BOB_ID}",
                               headers=headers("alice-subject", csrf=True))
    expected_log.assert_called_once()
    assert response.status_code == 409
    assert "Bob Ridge" not in response.get_data(as_text=True)


def test_page_renderer_substitutes_owner_specific_csrf_before_return():
    alice = web._render_form("alice-subject")
    bob = web._render_form("bob-subject")
    assert "__CSRF_TOKEN__" not in alice and "__CSRF_TOKEN__" not in bob
    a = re.search(r"const CSRF_TOKEN = '([^']+)'", alice)
    b = re.search(r"const CSRF_TOKEN = '([^']+)'", bob)
    assert a and b and a.group(1) != b.group(1)


def test_maintenance_mode_blocks_writes_and_readiness():
    marker = Path(os.environ["CRYOPIT_EXPORT_DIR"]) / ".cryopit-maintenance"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("stage12 test", encoding="utf-8")
    try:
        assert client.get("/readyz").status_code == 503
        response = client.post(
            "/api/archive", json=pit("MAINT", "Maintenance"),
            headers=headers("alice-subject", csrf=True),
        )
        assert response.status_code == 503
    finally:
        marker.unlink(missing_ok=True)
    assert client.get("/readyz").status_code == 200


def test_malformed_identity_fails_closed():
    # Werkzeug correctly refuses CR/LF header construction before Flask sees
    # it, so transport-impossible values belong in normalize_identity tests.
    # A zero-width format character is transport-safe, visually ambiguous in
    # logs, and must still fail closed through the real HTTP boundary.
    response = client.get("/api/pits", headers={"X-Remote-User": "alice\u200badmin"})
    assert response.status_code == 401


def test_attachment_upload_streaming_route_stores_and_cleans_inbound_scratch():
    good = b"\xff\xd8\xff" + b"stage2-photo" * 1000
    bad = b"not-a-real-jpeg" * 100
    good_q = str(uuid.uuid4())
    bad_q = str(uuid.uuid4())
    payload = pit("STAGE2-UPLOAD", "Stage 2")
    payload["attachment_manifest"] = [
        {"queue_id": good_q, "category": "pitwall", "filename": "good.jpg",
         "mime_type": "image/jpeg", "size_bytes": len(good),
         "sha256": hashlib.sha256(good).hexdigest(), "top_cm": None, "bottom_cm": None},
        {"queue_id": bad_q, "category": "pitwall", "filename": "bad.jpg",
         "mime_type": "image/jpeg", "size_bytes": len(bad),
         "sha256": hashlib.sha256(bad).hexdigest(), "top_cm": None, "bottom_cm": None},
    ]
    site_id = archive("alice-subject", payload)

    stored = client.post(
        f"/api/attach/{site_id}",
        data={"category": "pitwall", "queue_id": good_q,
              "file": (io.BytesIO(good), "good.jpg")},
        headers=headers("alice-subject", csrf=True),
        content_type="multipart/form-data",
    )
    assert stored.status_code == 200 and stored.get_json()["ok"]

    rejected = client.post(
        f"/api/attach/{site_id}",
        data={"category": "pitwall", "queue_id": bad_q,
              "file": (io.BytesIO(bad), "bad.jpg")},
        headers=headers("alice-subject", csrf=True),
        content_type="multipart/form-data",
    )
    assert rejected.status_code == 415

    scratch = Path(os.environ["CRYOPIT_EXPORT_DIR"]) / ".upload-staging"
    assert not scratch.exists() or not list(scratch.glob("*.upload.part"))


def test_oversized_body_and_internal_errors_are_safe():
    response = client.post("/api/archive", data=b"x" * (4 * 1024 * 1024 + 1),
                           content_type="application/json",
                           headers=headers("alice-subject", csrf=True))
    assert response.status_code == 413

    with patch.object(app.logger, "exception") as expected_log:
        boom = client.get("/api/stage12-boom", headers=headers("alice-subject"))
    expected_log.assert_called_once()
    text = boom.get_data(as_text=True)
    assert boom.status_code == 500
    assert "/srv/secret" not in text
    assert boom.get_json()["request_id"] == boom.headers["X-Request-ID"]


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
        raise SystemExit(f"{failures} Stage 12 Flask security tests failed")
    print(f"{len(TESTS)} Stage 12 Flask security tests passed")
