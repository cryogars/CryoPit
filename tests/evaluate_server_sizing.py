"""Evaluate a server-resource qualification report against CryoPit's RAM policy.

This does not benchmark the machine. It evaluates JSON produced by
``tests/benchmark_server_resources.py`` and reports whether a planned memory
allocation has been demonstrated on a representative host.

Example:
    python tests/evaluate_server_sizing.py server-resource-soak.json --ram-gib 3.5
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

MIB_PER_GIB = 1024.0
APP_PEAK_FRACTION = 0.70
MIN_AVAILABLE_FRACTION = 0.15
MIN_AVAILABLE_FLOOR_MIB = 512.0
MAX_SWAP_GROWTH_MIB = 64.0
MAX_ROUTINE_P95_SECONDS = 1.0
MIN_SOAK_MINUTES = 180.0
MAX_SOAK_GROWTH_MIB = 256.0
TARGET_HOST_TOLERANCE = 0.10


def evaluate(report: dict, ram_gib: float) -> dict:
    ram_mib = ram_gib * MIB_PER_GIB
    failures: list[str] = []
    provisional: list[str] = []
    notes: list[str] = []

    if report.get("mode") != "qualification":
        provisional.append("report was not produced with --qualification")

    if report.get("heic_mode") != "real HEIC":
        provisional.append("image workload was not real HEIC")

    system_total = report.get("system_total_memory_mib")
    if system_total is None:
        provisional.append("report does not include host total-memory measurement")
    elif system_total > ram_mib * (1.0 + TARGET_HOST_TOLERANCE):
        provisional.append(
            f"benchmark host has {system_total:.0f} MiB RAM, materially more than "
            f"the {ram_mib:.0f} MiB target"
        )
    else:
        notes.append(f"benchmark host total RAM: {system_total:.0f} MiB")

    peak = report.get("peak_rss_mib")
    peak_limit = ram_mib * APP_PEAK_FRACTION
    if peak is None:
        failures.append("peak RSS is missing")
    elif peak > peak_limit:
        failures.append(
            f"CryoPit peak RSS {peak:.0f} MiB exceeds the server planning ceiling "
            f"of {peak_limit:.0f} MiB ({APP_PEAK_FRACTION:.0%} of target RAM)"
        )
    else:
        notes.append(
            f"CryoPit peak RSS {peak:.0f} MiB is {peak / ram_mib:.1%} of target RAM"
        )

    min_available = report.get("minimum_mem_available_mib")
    available_floor = max(MIN_AVAILABLE_FLOOR_MIB, ram_mib * MIN_AVAILABLE_FRACTION)
    if min_available is None:
        provisional.append("report does not include minimum MemAvailable")
    elif system_total is not None and system_total <= ram_mib * (1.0 + TARGET_HOST_TOLERANCE):
        if min_available < available_floor:
            failures.append(
                f"minimum MemAvailable {min_available:.0f} MiB is below the server headroom "
                f"floor of {available_floor:.0f} MiB"
            )
        else:
            notes.append(f"minimum MemAvailable: {min_available:.0f} MiB")

    baseline_swap = report.get("baseline_swap_used_mib")
    max_swap = report.get("maximum_swap_used_mib")
    if baseline_swap is None or max_swap is None:
        provisional.append("report does not include swap-growth measurement")
    else:
        swap_growth = max(0.0, float(max_swap) - float(baseline_swap))
        if swap_growth > MAX_SWAP_GROWTH_MIB:
            failures.append(
                f"swap use grew by {swap_growth:.0f} MiB during qualification "
                f"(limit {MAX_SWAP_GROWTH_MIB:.0f} MiB)"
            )
        else:
            notes.append(f"swap growth during load: {swap_growth:.1f} MiB")

    for key, label in (
        ("leftover_download_parts", "download scratch files"),
        ("leftover_upload_parts", "upload scratch files"),
    ):
        if int(report.get(key, 0) or 0) != 0:
            failures.append(f"{label} remained after the mixed load")

    p95 = report.get("routine_p95_total_seconds")
    if p95 is None:
        provisional.append("routine p95 latency is missing")
    elif p95 > MAX_ROUTINE_P95_SECONDS:
        failures.append(
            f"routine p95 latency {p95:.3f} s exceeds {MAX_ROUTINE_P95_SECONDS:.1f} s"
        )
    else:
        notes.append(f"routine p95 latency: {p95:.3f} s")

    soak = report.get("soak")
    if not soak or float(soak.get("requested_minutes") or 0) < MIN_SOAK_MINUTES:
        provisional.append(f"a {MIN_SOAK_MINUTES:.0f}-minute soak has not been demonstrated")
    else:
        if int(soak.get("leftover_download_parts", 0) or 0) != 0:
            failures.append("download scratch files accumulated during soak")
        if int(soak.get("leftover_upload_parts", 0) or 0) != 0:
            failures.append("upload scratch files accumulated during soak")
        cycles = int(soak.get("cycles", 0) or 0)
        warm = int(soak.get("warmup_cycles_excluded", 0) or 0)
        slope = soak.get("rss_slope_mib_per_cycle_after_warmup")
        if slope is None:
            provisional.append("soak RSS slope is missing")
        else:
            projected = max(0.0, float(slope)) * max(0, cycles - warm)
            if projected > MAX_SOAK_GROWTH_MIB:
                failures.append(
                    f"post-warm-up RSS trend implies about {projected:.0f} MiB growth "
                    f"across the soak (limit {MAX_SOAK_GROWTH_MIB:.0f} MiB)"
                )
            else:
                notes.append(
                    f"post-warm-up RSS growth across soak: about {projected:.1f} MiB"
                )

    if failures:
        status = "FAIL"
    elif provisional:
        status = "PROVISIONAL"
    else:
        status = "PASS"

    return {
        "status": status,
        "target_ram_gib": ram_gib,
        "target_ram_mib": ram_mib,
        "policy": {
            "max_app_peak_fraction": APP_PEAK_FRACTION,
            "minimum_mem_available_mib": available_floor,
            "max_swap_growth_mib": MAX_SWAP_GROWTH_MIB,
            "max_routine_p95_seconds": MAX_ROUTINE_P95_SECONDS,
            "minimum_soak_minutes": MIN_SOAK_MINUTES,
            "max_soak_growth_mib": MAX_SOAK_GROWTH_MIB,
        },
        "failures": failures,
        "provisional_reasons": provisional,
        "notes": notes,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("report", help="server-resource qualification JSON")
    parser.add_argument("--ram-gib", type=float, default=3.5,
                        help="planned server RAM in GiB (default: 3.5)")
    parser.add_argument("--output", help="optional path for evaluator JSON")
    args = parser.parse_args()
    report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    result = evaluate(report, args.ram_gib)
    text = json.dumps(result, indent=2)
    print(text)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    return 1 if result["status"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
