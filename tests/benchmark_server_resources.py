"""Reproducible CryoPit server-resource qualification harness.

This is intentionally not part of the normal CI suite: qualification loads are
large and machine-specific. Run it on the deployment host after installing
``requirements.lock``. It exercises the production download/upload/HEIC/profile resource paths in
one process and emits JSON suitable for attaching to an infrastructure ticket.

Examples:
  python tests/benchmark_server_resources.py --quick
  python tests/benchmark_server_resources.py --qualification --output server-resources.json
"""
from __future__ import annotations

import argparse
import concurrent.futures
import gc
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
from pathlib import Path

try:
    import psutil
except ImportError:
    psutil = None

ROOT = Path(__file__).resolve().parents[1]


class _ProcMetrics:
    """Current-process and host-memory counters without a runtime psutil dependency."""
    def __init__(self):
        if psutil is not None:
            self._proc = psutil.Process(os.getpid())
        elif Path("/proc/self/statm").exists():
            self._proc = None
        else:
            raise SystemExit(
                "Server-resource benchmark needs psutil on this platform; install it only "
                "for qualification (it is not a CryoPit runtime dependency)."
            )

    class _Mem:
        def __init__(self, rss):
            self.rss = rss

    def memory_info(self):
        if self._proc is not None:
            return self._proc.memory_info()
        resident_pages = int(Path("/proc/self/statm").read_text().split()[1])
        return self._Mem(resident_pages * os.sysconf("SC_PAGE_SIZE"))

    def num_fds(self):
        if self._proc is not None:
            return self._proc.num_fds()
        return len(list(Path("/proc/self/fd").iterdir()))

    def system_memory(self):
        """Return host memory/swap counters in bytes."""
        if psutil is not None:
            vm = psutil.virtual_memory()
            sw = psutil.swap_memory()
            return {
                "total": int(vm.total),
                "available": int(vm.available),
                "swap_total": int(sw.total),
                "swap_used": int(sw.used),
            }
        meminfo = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            if ":" not in line:
                continue
            key, rest = line.split(":", 1)
            value = rest.strip().split()[0]
            try:
                meminfo[key] = int(value) * 1024
            except ValueError:
                continue
        total = meminfo.get("MemTotal", 0)
        available = meminfo.get("MemAvailable", meminfo.get("MemFree", 0))
        swap_total = meminfo.get("SwapTotal", 0)
        swap_free = meminfo.get("SwapFree", swap_total)
        return {
            "total": total,
            "available": available,
            "swap_total": swap_total,
            "swap_used": max(0, swap_total - swap_free),
        }


def _pit(n: int):
    depth = 130.0
    dz = depth / n
    strat, density = [], []
    grains = ["RG", "DH", "FC", "DF"]
    for i in range(n):
        top = depth - i * dz
        bottom = max(0.0, depth - (i + 1) * dz)
        strat.append({"top": top, "bottom": bottom, "gtype": grains[i % 4],
                      "hardness": "1F" if i % 2 == 0 else "4F",
                      "wetness": "D", "gsize": "1"})
        density.append({"top": top, "bottom": bottom,
                        "a": 220 + (i % 10) * 5, "b": 225 + (i % 10) * 5})
    return {
        "meta": {"pit_id": f"RESOURCE{n}", "total_depth": depth,
                 "date": "2026-01-01", "campaign": "stage6"},
        "stratigraphy": strat,
        "density": density,
        "temperature": [{"height": depth, "temp": -12},
                        {"height": depth / 2, "temp": -6},
                        {"height": 0, "temp": -0.5}],
    }


def _percentile(values, p):
    values = sorted(values)
    return values[min(len(values) - 1, int(round((len(values) - 1) * p)))]


def main():
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--quick", action="store_true", help="small developer smoke load")
    mode.add_argument("--qualification", action="store_true", help="field-server qualification load")
    parser.add_argument("--output", help="write JSON report to this path")
    parser.add_argument("--soak-minutes", type=float, default=0.0,
                        help="after the mixed load, repeat a moderate allocation/cleanup cycle for this many minutes")
    args = parser.parse_args()

    qualification = bool(args.qualification)
    download_mib = 750 if qualification else 100
    upload_mib = 25 if qualification else 10
    photo_count = 150 if qualification else 20
    profile_layers = 40 if qualification else 10
    heic_mp = 48 if qualification else 12

    tmp = Path(tempfile.mkdtemp(prefix="cryopit-stage6-benchmark-"))
    export = tmp / "exports"
    export.mkdir()
    os.environ["CRYOPIT_EXPORT_DIR"] = str(export)
    os.environ.setdefault("CRYOPIT_HEIC_CONCURRENCY", "1")
    os.environ.setdefault("CRYOPIT_PROFILE_CONCURRENCY", "2")
    sys.path.insert(0, str(ROOT))

    from PIL import Image
    from cryopit.download_staging import create_staged_zip_path, cleanup_staged_zip
    from cryopit.export import export_from_payload, write_zip_to_path
    import cryopit.heic_conversion as heic
    import cryopit.profile_rendering as profiles
    from cryopit.plot import render_profile
    from cryopit.upload_staging import cleanup_staged_upload, stage_upload_stream

    pit = _pit(profile_layers)
    small = _pit(2)

    # Build per-file incompressible inputs once, outside the measurement window.
    photos = tmp / "photos"
    photos.mkdir()
    per_file = download_mib * 1024 * 1024 // photo_count
    block = os.urandom(per_file)
    for i in range(photo_count):
        (photos / f"p{i:03d}.jpg").write_bytes(block)
    del block

    upload = tmp / "upload.bin"
    with upload.open("wb") as handle:
        one = os.urandom(1024 * 1024)
        for _ in range(upload_mib):
            handle.write(one)
    del one

    # Prefer a real HEIC when pillow-heif is available. Otherwise use a JPEG
    # decoded-pixel proxy and label the result honestly.
    width = 8000 if heic_mp >= 48 else 4000
    height = 6000 if heic_mp >= 48 else 3000
    source = tmp / ("source.heic" if heic_mp else "source.jpg")
    heic_mode = "real HEIC"
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
        image = Image.new("RGB", (width, height), (135, 145, 155))
        image.save(source, format="HEIF", quality=90)
        image.close()
    except Exception:
        heic_mode = "decoded-image JPEG proxy (pillow-heif unavailable)"
        source = tmp / "source.jpg"
        image = Image.new("RGB", (width, height), (135, 145, 155))
        image.save(source, format="JPEG", quality=95)
        image.close()
        # Let the file-to-file helper exercise the same Pillow decode /
        # encode path without claiming this is HEIC codec performance.
        import types
        sys.modules["pillow_heif"] = types.SimpleNamespace(register_heif_opener=lambda: None)
        heic._HEIF_REGISTERED = False

    routine_db = tmp / "routine.sqlite"
    conn = sqlite3.connect(routine_db)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE events(id INTEGER PRIMARY KEY, value TEXT)")
    conn.commit()
    conn.close()
    gc.collect()

    proc = _ProcMetrics()
    baseline = proc.memory_info().rss
    baseline_system = proc.system_memory()
    samples = []
    stop = threading.Event()
    stage_max = {"download": 0, "upload": 0}

    def monitor():
        while not stop.is_set():
            try:
                ddir, udir = export / ".download-staging", export / ".upload-staging"
                dbytes = sum(p.stat().st_size for p in ddir.glob("*") if p.is_file()) if ddir.exists() else 0
                ubytes = sum(p.stat().st_size for p in udir.glob("*") if p.is_file()) if udir.exists() else 0
                stage_max["download"] = max(stage_max["download"], dbytes)
                stage_max["upload"] = max(stage_max["upload"], ubytes)
                host = proc.system_memory()
                samples.append((time.perf_counter(), proc.memory_info().rss, proc.num_fds(),
                                host["available"], host["swap_used"]))
            except Exception:
                pass
            time.sleep(0.02)

    def profile_job(i):
        started = time.perf_counter()
        with profiles.profile_render_slot():
            png, pdf = render_profile(pit, dpi=150, fmt="both")
        return {"job": f"profile-{i}", "seconds": time.perf_counter() - started,
                "output_bytes": len(png) + len(pdf)}

    def heic_job():
        started = time.perf_counter()
        converted = None
        with heic.heic_conversion_slot():
            converted = heic._convert_heic_to_jpeg_file(source, export_dir=export)
        size = converted.size_bytes
        cleanup_staged_upload(converted.path)
        return {"job": f"image-{heic_mp}MP", "seconds": time.perf_counter() - started,
                "output_bytes": size, "mode": heic_mode}

    def zip_job():
        started = time.perf_counter()
        path = create_staged_zip_path(export)
        uploads = {f"uploads/pitwall/{p.name}": str(p) for p in sorted(photos.glob("*.jpg"))}
        _, size = write_zip_to_path(export_from_payload(small), small["meta"], path, uploads=uploads)
        cleanup_staged_zip(path)
        return {"job": f"download-{download_mib}MiB", "seconds": time.perf_counter() - started,
                "output_bytes": size}

    def upload_job():
        started = time.perf_counter()
        with upload.open("rb") as handle:
            staged = stage_upload_stream(handle, max_bytes=(upload_mib + 1) * 1024 * 1024,
                                         export_dir=export)
        size = staged.size_bytes
        cleanup_staged_upload(staged.path)
        return {"job": f"upload-{upload_mib}MiB", "seconds": time.perf_counter() - started,
                "output_bytes": size}

    def routine_job(i, submitted):
        started = time.perf_counter()
        export_from_payload(small)
        conn = sqlite3.connect(routine_db, timeout=5)
        try:
            conn.execute("INSERT INTO events(value) VALUES (?)", (str(i),))
            conn.commit()
            conn.execute("SELECT COUNT(*) FROM events").fetchone()
        finally:
            conn.close()
        ended = time.perf_counter()
        return {"start_delay": started - submitted, "total": ended - submitted}

    monitor_thread = threading.Thread(target=monitor, daemon=True)
    monitor_thread.start()
    started = time.perf_counter()
    routine = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        heavy = [pool.submit(profile_job, 1), pool.submit(profile_job, 2),
                 pool.submit(heic_job), pool.submit(zip_job), pool.submit(upload_job)]
        time.sleep(0.05)
        routine_futures = []
        for i in range(50):
            submitted = time.perf_counter()
            routine_futures.append(pool.submit(routine_job, i, submitted))
            time.sleep(0.005)
        heavy_results = [f.result() for f in heavy]
        routine = [f.result() for f in routine_futures]
    elapsed = time.perf_counter() - started
    stop.set()
    monitor_thread.join(timeout=2)
    gc.collect()
    time.sleep(0.2)

    soak = None
    if args.soak_minutes > 0:
        # A wall-clock soak is deliberately separate from the mixed spike. It
        # repeats the mechanisms most likely to leak: Matplotlib allocation,
        # upload staging, download staging, and periodic image conversion.
        soak_pit = _pit(10)
        soak_photos = sorted(photos.glob("*.jpg"))[:max(1, min(4, photo_count))]
        soak_deadline = time.monotonic() + args.soak_minutes * 60.0
        post_rss = []
        cycle_seconds = []
        cycles = 0
        while time.monotonic() < soak_deadline:
            cycle_started = time.perf_counter()
            with profiles.profile_render_slot():
                png, pdf = render_profile(soak_pit, dpi=150, fmt="both")
            del png, pdf

            with upload.open("rb") as handle:
                staged = stage_upload_stream(
                    handle, max_bytes=(upload_mib + 1) * 1024 * 1024, export_dir=export
                )
            cleanup_staged_upload(staged.path)

            staged_zip = create_staged_zip_path(export)
            small_uploads = {f"uploads/pitwall/{p.name}": str(p) for p in soak_photos}
            write_zip_to_path(export_from_payload(small), small["meta"], staged_zip,
                              uploads=small_uploads)
            cleanup_staged_zip(staged_zip)

            # Exercise the image-conversion allocation periodically without
            # making every soak cycle a worst-case 48 MP conversion.
            if cycles % 5 == 0:
                converted = None
                with heic.heic_conversion_slot():
                    converted = heic._convert_heic_to_jpeg_file(source, export_dir=export)
                cleanup_staged_upload(converted.path)

            gc.collect()
            time.sleep(0.05)
            cycles += 1
            post_rss.append(proc.memory_info().rss / 2**20)
            cycle_seconds.append(time.perf_counter() - cycle_started)

        warm = min(len(post_rss), max(3, len(post_rss) // 5))
        ys = post_rss[warm:]
        slope = None
        if len(ys) >= 2:
            xs = list(range(len(ys)))
            xm, ym = sum(xs) / len(xs), sum(ys) / len(ys)
            denom = sum((x - xm) ** 2 for x in xs)
            slope = (sum((x - xm) * (y - ym) for x, y in zip(xs, ys)) / denom
                     if denom else 0.0)
        soak = {
            "requested_minutes": args.soak_minutes,
            "cycles": cycles,
            "warmup_cycles_excluded": warm,
            "first_post_cycle_rss_mib": post_rss[0] if post_rss else None,
            "last_post_cycle_rss_mib": post_rss[-1] if post_rss else None,
            "min_post_warmup_rss_mib": min(ys) if ys else None,
            "max_post_warmup_rss_mib": max(ys) if ys else None,
            "rss_slope_mib_per_cycle_after_warmup": slope,
            "median_cycle_seconds": _percentile(cycle_seconds, .50) if cycle_seconds else None,
            "leftover_download_parts": len(list((export / ".download-staging").glob("*.zip.part"))) if (export / ".download-staging").exists() else 0,
            "leftover_upload_parts": len(list((export / ".upload-staging").glob("*.upload.part"))) if (export / ".upload-staging").exists() else 0,
        }

    result = {
        "mode": "qualification" if qualification else "quick",
        "heic_mode": heic_mode,
        "baseline_rss_mib": baseline / 2**20,
        "peak_rss_mib": max(rss for _, rss, _, _, _ in samples) / 2**20,
        "post_run_rss_mib": proc.memory_info().rss / 2**20,
        "system_total_memory_mib": baseline_system["total"] / 2**20,
        "baseline_mem_available_mib": baseline_system["available"] / 2**20,
        "minimum_mem_available_mib": min(avail for _, _, _, avail, _ in samples) / 2**20,
        "swap_total_mib": baseline_system["swap_total"] / 2**20,
        "baseline_swap_used_mib": baseline_system["swap_used"] / 2**20,
        "maximum_swap_used_mib": max(swap for _, _, _, _, swap in samples) / 2**20,
        "elapsed_seconds": elapsed,
        "max_open_fds": max(fd for _, _, fd, _, _ in samples),
        "max_download_staging_mib": stage_max["download"] / 2**20,
        "max_upload_staging_mib": stage_max["upload"] / 2**20,
        "routine_p50_start_seconds": _percentile([r["start_delay"] for r in routine], .50),
        "routine_p95_start_seconds": _percentile([r["start_delay"] for r in routine], .95),
        "routine_p95_total_seconds": _percentile([r["total"] for r in routine], .95),
        "heavy_jobs": heavy_results,
        "leftover_download_parts": len(list((export / ".download-staging").glob("*.zip.part"))) if (export / ".download-staging").exists() else 0,
        "leftover_upload_parts": len(list((export / ".upload-staging").glob("*.upload.part"))) if (export / ".upload-staging").exists() else 0,
        "soak": soak,
    }
    print(json.dumps(result, indent=2))
    if args.output:
        Path(args.output).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
