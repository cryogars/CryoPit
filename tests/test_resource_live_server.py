"""Live Waitress resource-qualification smoke test.

Runs only when Flask + Waitress are installed. It starts the real CryoPit WSGI
server with eight threads, launches two profile requests, and probes health
concurrently. The existing end-to-end smoke suite separately exercises real
HEIC upload/conversion when pillow-heif is installed.
"""
from __future__ import annotations

import concurrent.futures
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

try:
    import flask  # noqa: F401
    import waitress  # noqa: F401
except ImportError:
    print("SKIP live Waitress resource test (Flask/Waitress unavailable)")
    raise SystemExit(0)

sys.path.insert(0, str(ROOT))
from cryopit.security import issue_csrf_token


def _port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _pit(layers=10):
    depth = 100.0
    dz = depth / layers
    strat, density = [], []
    grains = ["RG", "DH", "FC", "DF"]
    for i in range(layers):
        top = depth - i * dz
        bottom = max(0.0, depth - (i + 1) * dz)
        strat.append({"top": top, "bottom": bottom, "gtype": grains[i % 4],
                      "hardness": "1F", "wetness": "D", "gsize": "1"})
        density.append({"top": top, "bottom": bottom, "a": 230, "b": 235})
    return {
        "meta": {"pit_id": "STAGE6LIVE", "total_depth": depth,
                 "date": "2026-01-01", "campaign": "stage6"},
        "stratigraphy": strat,
        "density": density,
        "temperature": [{"height": depth, "temp": -10}, {"height": 0, "temp": -0.5}],
    }


def _get(url, timeout=10):
    started = time.perf_counter()
    with urllib.request.urlopen(url, timeout=timeout) as response:
        body = response.read()
        return response.status, time.perf_counter() - started, body


def _profile(url, token, payload):
    req = urllib.request.Request(
        url + "/api/profile",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", "X-CryoPit-CSRF": token},
    )
    started = time.perf_counter()
    with urllib.request.urlopen(req, timeout=30) as response:
        body = response.read()
        return response.status, time.perf_counter() - started, body


def main():
    tmp = Path(tempfile.mkdtemp(prefix="cryopit-stage6-live-"))
    port = _port()
    secret = "stage6-live-secret-0123456789-abcdefghijklmnopqrstuvwxyz"
    env = os.environ.copy()
    env.update({
        "PYTHONPATH": str(ROOT),
        "CRYOPIT_HOST": "127.0.0.1",
        "CRYOPIT_PORT": str(port),
        "CRYOPIT_DB_PATH": str(tmp / "cryopit.db"),
        "CRYOPIT_EXPORT_DIR": str(tmp / "exports"),
        "CRYOPIT_DEV_USER": "stage6",
        "CRYOPIT_SECRET_KEY": secret,
        "CRYOPIT_THREADS": "8",
        "CRYOPIT_HEIC_CONCURRENCY": "1",
        "CRYOPIT_PROFILE_CONCURRENCY": "2",
        "CRYOPIT_RATE_LIMIT_EXPORTS_PER_MINUTE": "100",
    })
    # Crash leftovers must disappear during real make_app() startup.
    (tmp / "exports" / ".download-staging").mkdir(parents=True)
    (tmp / "exports" / ".download-staging" / "download-crash.zip.part").write_bytes(b"partial")
    (tmp / "exports" / ".upload-staging").mkdir(parents=True)
    (tmp / "exports" / ".upload-staging" / "upload-crash.upload.part").write_bytes(b"partial")

    proc = subprocess.Popen(
        [sys.executable, "-m", "cryopit"], cwd=ROOT, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 20
        while True:
            if proc.poll() is not None:
                output = proc.stdout.read() if proc.stdout else ""
                raise AssertionError(f"CryoPit exited during startup:\n{output}")
            try:
                status, _, _ = _get(base + "/healthz", timeout=1)
                if status == 200:
                    break
            except Exception:
                if time.monotonic() >= deadline:
                    raise AssertionError("CryoPit did not become healthy")
                time.sleep(0.1)

        assert not (tmp / "exports" / ".download-staging" / "download-crash.zip.part").exists()
        assert not (tmp / "exports" / ".upload-staging" / "upload-crash.upload.part").exists()

        token = issue_csrf_token("stage6", secret)
        payload = _pit(10)
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            profile_futures = [pool.submit(_profile, base, token, payload) for _ in range(2)]
            time.sleep(0.05)
            health_futures = [pool.submit(_get, base + "/healthz") for _ in range(20)]
            health = [f.result(timeout=15) for f in health_futures]
            profiles = [f.result(timeout=30) for f in profile_futures]

        assert all(status == 200 for status, _, _ in health)
        assert all(status == 200 and body.startswith(b"\x89PNG") for status, _, body in profiles)
        p95 = sorted(latency for _, latency, _ in health)[int(0.95 * (len(health) - 1))]
        # Generous enough for shared CI, strict enough to catch total worker
        # starvation/deadlock while the two configured render slots are busy.
        assert p95 < 5.0, f"health p95 was {p95:.2f}s under two profile renders"
        print(f"PASS live Waitress: health p95={p95:.3f}s; profile durations="
              f"{', '.join(f'{x[1]:.2f}s' for x in profiles)}")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


if __name__ == "__main__":
    main()
