"""Resource-hardening Stage 6: crash/restart cleanup and permit recovery."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TMP = Path(tempfile.mkdtemp(prefix="cryopit-resource-stage6-"))
EXPORT = TMP / "exports"
os.environ["CRYOPIT_EXPORT_DIR"] = str(EXPORT)
os.environ.pop("CRYOPIT_HEIC_CONCURRENCY", None)
os.environ.pop("CRYOPIT_PROFILE_CONCURRENCY", None)
sys.path.insert(0, str(ROOT))

from cryopit.download_staging import sweep_staged_downloads
from cryopit.upload_staging import sweep_staged_uploads


def _spawn(code: str) -> subprocess.Popen:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    env["CRYOPIT_EXPORT_DIR"] = str(EXPORT)
    return subprocess.Popen(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _kill_after_ready(code: str) -> tuple[Path | None, str]:
    proc = _spawn(code)
    assert proc.stdout is not None
    line = proc.stdout.readline().strip()
    assert line, "child never reported readiness"
    reported = None if line == "READY" else Path(line)
    proc.kill()  # SIGKILL on POSIX; abrupt TerminateProcess on Windows.
    proc.wait(timeout=5)
    return reported, line


def test_forced_process_kill_download_is_swept_on_restart():
    code = r'''
from cryopit.download_staging import create_staged_zip_path
import time
p = create_staged_zip_path()
with p.open("wb") as f:
    f.write(b"partial-download")
    f.flush()
print(p, flush=True)
time.sleep(60)
'''
    path, _ = _kill_after_ready(code)
    assert path is not None and path.exists()
    assert sweep_staged_downloads(EXPORT) == 1
    assert not path.exists()


def test_forced_process_kill_upload_is_swept_on_restart():
    code = r'''
from cryopit.upload_staging import stage_upload_stream
import io, time
s = stage_upload_stream(io.BytesIO(b"x" * (2 * 1024 * 1024)), max_bytes=3 * 1024 * 1024)
print(s.path, flush=True)
time.sleep(60)
'''
    path, _ = _kill_after_ready(code)
    assert path is not None and path.exists()
    assert sweep_staged_uploads(EXPORT) == 1
    assert not path.exists()


def test_killed_heic_holder_cannot_stick_next_process_permit():
    code = r'''
from cryopit.heic_conversion import heic_conversion_slot
import time
with heic_conversion_slot():
    print("READY", flush=True)
    time.sleep(60)
'''
    _kill_after_ready(code)
    probe = _spawn(r'''
from cryopit.heic_conversion import heic_conversion_slot
with heic_conversion_slot():
    print("READY", flush=True)
''')
    out, err = probe.communicate(timeout=5)
    assert probe.returncode == 0, err
    assert "READY" in out


def test_killed_profile_holder_cannot_stick_next_process_permit():
    code = r'''
from cryopit.profile_rendering import profile_render_slot
import time
with profile_render_slot():
    print("READY", flush=True)
    time.sleep(60)
'''
    _kill_after_ready(code)
    probe = _spawn(r'''
from cryopit.profile_rendering import profile_render_slot
with profile_render_slot():
    print("READY", flush=True)
''')
    out, err = probe.communicate(timeout=5)
    assert probe.returncode == 0, err
    assert "READY" in out


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
        raise SystemExit(f"{failures} resource Stage 6 resilience tests failed")
    print(f"{len(TESTS)} resource Stage 6 resilience tests passed")
