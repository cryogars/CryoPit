"""Resource-hardening Stage 4: bounded, single-build profile rendering."""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.pop("CRYOPIT_PROFILE_CONCURRENCY", None)
os.environ.pop("CRYOPIT_FIGURE_DPI", None)
sys.path.insert(0, str(ROOT))

import cryopit.plot as plot
import cryopit.profile_rendering as profiles

PIT = {
    "meta": {"pit_id": "R4", "total_depth": 95, "date": "2026-01-01"},
    "stratigraphy": [
        {"top": 95, "bottom": 40, "gtype": "RG", "hardness": "1F", "wetness": "D"},
        {"top": 40, "bottom": 0, "gtype": "DH", "hardness": "4F", "wetness": "D"},
    ],
    "density": [
        {"top": 95, "bottom": 40, "a": 220, "b": 226},
        {"top": 40, "bottom": 0, "a": 330, "b": 336},
    ],
    "temperature": [{"height": 95, "temp": -8}, {"height": 0, "temp": -0.2}],
}


def test_default_profile_limit_is_two():
    assert profiles.PROFILE_CONCURRENCY == 2


def test_profile_limiter_caps_parallel_workers():
    old_slots = profiles._PROFILE_SLOTS
    gate = threading.BoundedSemaphore(2)
    active = 0
    peak = 0
    lock = threading.Lock()

    def worker():
        nonlocal active, peak
        with profiles.profile_render_slot():
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.06)
            with lock:
                active -= 1

    profiles._PROFILE_SLOTS = gate
    try:
        threads = [threading.Thread(target=worker) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=2)
            assert not thread.is_alive()
        assert peak == 2
    finally:
        profiles._PROFILE_SLOTS = old_slots


def test_profile_failure_releases_permit():
    old_slots = profiles._PROFILE_SLOTS
    gate = threading.BoundedSemaphore(1)
    profiles._PROFILE_SLOTS = gate
    try:
        try:
            with profiles.profile_render_slot():
                raise RuntimeError("synthetic render failure")
        except RuntimeError:
            pass
        assert gate.acquire(blocking=False), "failed render leaked its profile permit"
        gate.release()
    finally:
        profiles._PROFILE_SLOTS = old_slots


def test_combined_render_matches_individual_outputs_exactly():
    for dpi in (150, 300):
        expected_png = plot.render_profile(PIT, dpi=dpi, fmt="png")
        expected_pdf = plot.render_profile(PIT, fmt="pdf")
        png, pdf = plot.render_profile(PIT, dpi=dpi, fmt="both")
        assert png == expected_png
        assert pdf == expected_pdf


def test_combined_render_constructs_one_matplotlib_figure():
    real = plot.Figure
    count = 0

    class SpyFigure(real):
        def __init__(self, *args, **kwargs):
            nonlocal count
            count += 1
            super().__init__(*args, **kwargs)

    plot.Figure = SpyFigure
    try:
        png, pdf = plot.render_profile(PIT, dpi=150, fmt="both")
        assert png.startswith(b"\x89PNG")
        assert pdf and pdf.startswith(b"%PDF-")
        assert count == 1, f"combined archive render constructed {count} figures"
    finally:
        plot.Figure = real


def test_figure_dpi_guardrail_accepts_300_and_rejects_301():
    base = os.environ.copy()
    base["PYTHONPATH"] = str(ROOT)
    base["CRYOPIT_FIGURE_DPI"] = "300"
    ok = subprocess.run(
        [sys.executable, "-c", "import cryopit.config as c; print(c.FIGURE_DPI)"],
        cwd=ROOT, env=base, text=True, capture_output=True,
    )
    assert ok.returncode == 0 and ok.stdout.strip() == "300"

    base["CRYOPIT_FIGURE_DPI"] = "301"
    bad = subprocess.run(
        [sys.executable, "-c", "import cryopit.config"],
        cwd=ROOT, env=base, text=True, capture_output=True,
    )
    assert bad.returncode != 0
    assert "must not exceed 300" in (bad.stderr + bad.stdout)


def test_web_routes_use_the_profile_limiter_and_single_build_mode():
    source = (ROOT / "cryopit" / "web.py").read_text()
    assert source.count("with profile_render_slot():") >= 2
    assert 'render_profile(payload, dpi=FIGURE_DPI, fmt="both")' in source


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
        raise SystemExit(f"{failures} resource Stage 4 profile tests failed")
    print(f"{len(TESTS)} resource Stage 4 profile tests passed")
