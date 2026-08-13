"""Stage 12 framework-independent security checks."""
from __future__ import annotations

import importlib
import os
import subprocess
import sys
import tempfile
import types
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TMP = Path(tempfile.mkdtemp(prefix="cryopit-stage12-security-"))
os.environ["CRYOPIT_DB_PATH"] = str(TMP / "security.db")
os.environ["CRYOPIT_EXPORT_DIR"] = str(TMP / "exports")
os.environ["CRYOPIT_TRUST_PROXY_AUTH"] = "false"

PKG = "_cryopit_stage12_security"
pkg = types.ModuleType(PKG)
pkg.__path__ = [str(ROOT / "cryopit")]
sys.modules[PKG] = pkg

class AbortError(Exception):
    pass

flask_stub = types.ModuleType("flask")
flask_stub.abort = lambda code, description=None: (_ for _ in ()).throw(AbortError(f"{code}: {description}"))
flask_stub.has_request_context = lambda: True
flask_stub.request = types.SimpleNamespace(headers={})
sys.modules["flask"] = flask_stub

config = importlib.import_module(f"{PKG}.config")
auth = importlib.import_module(f"{PKG}.auth")
security = importlib.import_module(f"{PKG}.security")


def test_csrf_token_is_owner_bound_and_expires():
    secret = "s" * 48
    token = security.issue_csrf_token("alice", secret, now=100_000, ttl_seconds=1000)
    assert security.validate_csrf_token(token, "alice", secret, now=100_100, ttl_seconds=1000)
    assert not security.validate_csrf_token(token, "bob", secret, now=100_100, ttl_seconds=1000)
    assert not security.validate_csrf_token(token, "alice", "x" * 48, now=100_100, ttl_seconds=1000)
    assert security.validate_csrf_token(token, "alice", secret, now=101_050, ttl_seconds=1000)
    assert not security.validate_csrf_token(token, "alice", secret, now=102_050, ttl_seconds=1000)


def test_csrf_token_rejects_malformed_values():
    for value in (None, "", "v1", "v1.nope.deadbeef", "v2.10." + "a" * 64,
                  "v1.10." + "g" * 64):
        assert not security.validate_csrf_token(value, "alice", "s" * 48,
                                                now=10_500, ttl_seconds=1000)


def test_identity_normalization_accepts_opaque_sso_subjects():
    assert auth.normalize_identity("  00u81abc123:tenant  ") == "00u81abc123:tenant"
    decomposed = "Jose\u0301"
    assert auth.normalize_identity(decomposed) == unicodedata.normalize("NFC", decomposed)


def test_identity_rejects_blank_control_and_oversize_values():
    for value in (None, "", "   ", "alice\nadmin", "alice\u200badmin",
                  "x" * (config.IDENTITY_MAX_LENGTH + 1)):
        try:
            auth.normalize_identity(value)
        except ValueError:
            pass
        else:
            raise AssertionError(f"unsafe identity was accepted: {value!r}")


def test_local_mode_ignores_spoofed_proxy_header():
    flask_stub.request.headers = {config.AUTH_HEADER: "mallory"}
    assert auth.current_user() == config.DEV_USER


def test_rate_limiter_is_sliding_and_reports_retry_after():
    limiter = security.SlidingWindowLimiter(window_seconds=60)
    assert limiter.check("alice", 2, now=0) == (True, 0)
    assert limiter.check("alice", 2, now=10) == (True, 0)
    allowed, retry = limiter.check("alice", 2, now=20)
    assert allowed is False and retry == 40
    assert limiter.check("bob", 2, now=20) == (True, 0)
    assert limiter.check("alice", 2, now=61) == (True, 0)


def test_proxy_mode_requires_a_stable_secret():
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    env["CRYOPIT_TRUST_PROXY_AUTH"] = "true"
    env.pop("CRYOPIT_SECRET_KEY", None)
    proc = subprocess.run(
        [sys.executable, "-c", "from cryopit.config import validate_config; validate_config()"],
        env=env, text=True, capture_output=True,
    )
    assert proc.returncode != 0
    assert "CRYOPIT_SECRET_KEY is required" in (proc.stdout + proc.stderr)


def test_proxy_mode_rejects_a_short_secret():
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    env["CRYOPIT_TRUST_PROXY_AUTH"] = "true"
    env["CRYOPIT_SECRET_KEY"] = "short"
    proc = subprocess.run(
        [sys.executable, "-c", "from cryopit.config import validate_config; validate_config()"],
        env=env, text=True, capture_output=True,
    )
    assert proc.returncode != 0
    assert "at least 32 characters" in (proc.stdout + proc.stderr)


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
        raise SystemExit(f"{failures} Stage 12 security tests failed")
    print(f"{len(TESTS)} Stage 12 security tests passed")
