from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "server_sizing_eval", ROOT / "tests" / "evaluate_server_sizing.py"
)
mod = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(mod)


def good_report():
    return {
        "mode": "qualification",
        "heic_mode": "real HEIC",
        "peak_rss_mib": 1800.0,
        "system_total_memory_mib": 3550.0,
        "minimum_mem_available_mib": 900.0,
        "baseline_swap_used_mib": 0.0,
        "maximum_swap_used_mib": 0.0,
        "routine_p95_total_seconds": 0.10,
        "leftover_download_parts": 0,
        "leftover_upload_parts": 0,
        "soak": {
            "requested_minutes": 180.0,
            "cycles": 1000,
            "warmup_cycles_excluded": 100,
            "rss_slope_mib_per_cycle_after_warmup": 0.01,
            "leftover_download_parts": 0,
            "leftover_upload_parts": 0,
        },
    }


def test_pass():
    r = mod.evaluate(good_report(), 3.5)
    assert r["status"] == "PASS", r


def test_proxy_is_provisional():
    report = good_report()
    report["heic_mode"] = "decoded-image JPEG proxy (pillow-heif unavailable)"
    r = mod.evaluate(report, 3.5)
    assert r["status"] == "PROVISIONAL", r


def test_larger_host_is_provisional():
    report = good_report()
    report["system_total_memory_mib"] = 6000.0
    r = mod.evaluate(report, 3.5)
    assert r["status"] == "PROVISIONAL", r


def test_high_peak_fails():
    report = good_report()
    report["peak_rss_mib"] = 2700.0
    r = mod.evaluate(report, 3.5)
    assert r["status"] == "FAIL", r


def test_low_available_fails():
    report = good_report()
    report["minimum_mem_available_mib"] = 300.0
    r = mod.evaluate(report, 3.5)
    assert r["status"] == "FAIL", r


def test_soak_growth_fails():
    report = good_report()
    report["soak"]["rss_slope_mib_per_cycle_after_warmup"] = 0.5
    r = mod.evaluate(report, 3.5)
    assert r["status"] == "FAIL", r


if __name__ == "__main__":
    tests = [
        test_pass,
        test_proxy_is_provisional,
        test_larger_host_is_provisional,
        test_high_peak_fails,
        test_low_available_fails,
        test_soak_growth_fails,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"{len(tests)}/{len(tests)} passed")
