"""Resource-hardening Stage 5: HTTP thread tuning and operator diagnostics."""
from __future__ import annotations

import contextlib
import io
import os
import subprocess
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.pop("CRYOPIT_THREADS", None)
os.environ.pop("CRYOPIT_HEIC_CONCURRENCY", None)
os.environ.pop("CRYOPIT_PROFILE_CONCURRENCY", None)
sys.path.insert(0, str(ROOT))

import cryopit.config as config
import cryopit.__main__ as entry


def test_shared_server_defaults_keep_eight_http_threads():
    assert config.THREADS == 8
    assert config.HEIC_CONCURRENCY == 1
    assert config.PROFILE_CONCURRENCY == 2
    # At the active heavy-operation limits, 8 leaves five request slots.
    assert config.THREADS - config.HEIC_CONCURRENCY - config.PROFILE_CONCURRENCY == 5


def test_four_threads_remains_valid_but_tight():
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    env["CRYOPIT_THREADS"] = "4"
    result = subprocess.run(
        [sys.executable, "-c", "import cryopit.config as c; print(c.THREADS)"],
        cwd=ROOT, env=env, text=True, capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "4"
    assert 4 - config.HEIC_CONCURRENCY - config.PROFILE_CONCURRENCY == 1


def test_waitress_receives_configured_thread_count_and_startup_reports_caps():
    calls = []

    class FakeApp:
        def run(self, **kwargs):
            raise AssertionError("Flask fallback should not run when fake Waitress is present")

    def fake_serve(app, **kwargs):
        calls.append((app, kwargs))

    fake_waitress = types.SimpleNamespace(serve=fake_serve)
    previous = sys.modules.get("waitress")
    old_make_app = entry.make_app
    old_preflight = entry._preflight_bind
    sys.modules["waitress"] = fake_waitress
    entry.make_app = lambda: FakeApp()
    entry._preflight_bind = lambda: None
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            entry.main()
    finally:
        entry.make_app = old_make_app
        entry._preflight_bind = old_preflight
        if previous is None:
            sys.modules.pop("waitress", None)
        else:
            sys.modules["waitress"] = previous

    assert len(calls) == 1
    assert calls[0][1]["threads"] == config.THREADS == 8
    text = out.getvalue()
    assert "8 HTTP threads" in text
    assert "HEIC 1" in text
    assert "profiles 2" in text


def test_stage5_docs_treat_threads_as_tunable_not_heavy_memory_control():
    readme = (ROOT / "README.md").read_text()
    config_doc = (ROOT / "docs" / "CONFIGURATION.md").read_text()
    deploy = (ROOT / "docs" / "DEPLOYMENT.md").read_text()
    production = (ROOT / ".env.production.example").read_text()

    assert "CRYOPIT_THREADS=8" in readme
    assert "Requests waiting for a heavy-operation semaphore still occupy a Waitress thread" in readme
    assert "### HTTP thread tuning" in config_doc
    assert "Four HTTP threads are valid" in config_doc
    assert "### HTTP concurrency on a shared server" in deploy
    assert "CRYOPIT_THREADS=8" in production


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
        raise SystemExit(f"{failures} resource Stage 5 thread tests failed")
    print(f"{len(TESTS)} resource Stage 5 thread tests passed")
