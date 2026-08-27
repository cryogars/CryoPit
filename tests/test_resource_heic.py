"""Bounded, disk-backed HEIC conversion resource tests."""
from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import threading
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TMP = Path(tempfile.mkdtemp(prefix="cryopit-resource-stage3-"))
os.environ["CRYOPIT_EXPORT_DIR"] = str(TMP / "exports")
os.environ.pop("CRYOPIT_HEIC_CONCURRENCY", None)
sys.path.insert(0, str(ROOT))

import cryopit.heic_conversion as heic
from cryopit.upload_staging import cleanup_staged_upload, staging_dir, sweep_staged_uploads


def test_default_conversion_limit_is_serial():
    assert heic.HEIC_CONCURRENCY == 1


def test_conversion_limiter_caps_parallel_workers():
    old_slots = heic._CONVERSION_SLOTS
    old_convert = heic._convert_heic_to_jpeg_file
    gate = threading.BoundedSemaphore(2)
    active = 0
    peak = 0
    lock = threading.Lock()

    def fake_convert(_source, **_kwargs):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.08)
        with lock:
            active -= 1
        return None

    heic._CONVERSION_SLOTS = gate
    heic._convert_heic_to_jpeg_file = fake_convert
    try:
        threads = [threading.Thread(target=heic.convert_heic_to_jpeg, args=("unused",))
                   for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=2)
            assert not thread.is_alive()
        assert peak == 2
    finally:
        heic._CONVERSION_SLOTS = old_slots
        heic._convert_heic_to_jpeg_file = old_convert


def test_conversion_failure_releases_permit():
    old_slots = heic._CONVERSION_SLOTS
    old_convert = heic._convert_heic_to_jpeg_file
    gate = threading.BoundedSemaphore(1)

    def fail(_source, **_kwargs):
        raise RuntimeError("synthetic decode failure")

    heic._CONVERSION_SLOTS = gate
    heic._convert_heic_to_jpeg_file = fail
    try:
        assert heic.convert_heic_to_jpeg("unused") is None
        assert gate.acquire(blocking=False), "failed conversion leaked its HEIC permit"
        gate.release()
    finally:
        heic._CONVERSION_SLOTS = old_slots
        heic._convert_heic_to_jpeg_file = old_convert


def test_converter_writes_and_hashes_disk_backed_output_without_bytesio():
    # pillow-heif is optional in this test environment. A no-op registration
    # module lets Pillow open a JPEG source so we can exercise the exact disk-backed
    # file-to-file encoder/digest/cleanup path without pretending this is a HEIC
    # codec correctness test.
    from PIL import Image

    fake = types.SimpleNamespace(register_heif_opener=lambda: None)
    previous = sys.modules.get("pillow_heif")
    sys.modules["pillow_heif"] = fake
    source = TMP / "source.jpg"
    Image.new("RGB", (128, 96), (120, 140, 200)).save(source, "JPEG")
    converted = None
    try:
        converted = heic._convert_heic_to_jpeg_file(source)
        assert converted.path.parent == staging_dir()
        assert converted.path.name.endswith(".upload.part")
        data_digest = hashlib.sha256(converted.path.read_bytes()).hexdigest()
        assert converted.sha256 == data_digest
        assert converted.size_bytes == converted.path.stat().st_size
        with Image.open(converted.path) as got:
            assert got.size == (128, 96)
        source_text = (ROOT / "cryopit" / "heic_conversion.py").read_text()
        assert "BytesIO" not in source_text
        assert ".read_bytes()" not in source_text
    finally:
        if converted is not None:
            cleanup_staged_upload(converted.path)
        if previous is None:
            sys.modules.pop("pillow_heif", None)
        else:
            sys.modules["pillow_heif"] = previous


def test_startup_upload_sweep_removes_crashed_conversion_output():
    root = staging_dir()
    orphan = root / "heic-jpeg-crashed.upload.part"
    orphan.write_bytes(b"partial-jpeg")
    assert sweep_staged_uploads() >= 1
    assert not orphan.exists()


def test_web_converts_before_storage_lock_and_adopts_prepared_file():
    source = (ROOT / "cryopit" / "web.py").read_text()
    start = source.index("def _api_attach(site_id):")
    end = source.index("def _api_attach_prepared_locked", start)
    intake = source[start:end]
    conversion_at = intake.index("convert_heic_to_jpeg(inbound.path)")
    lock_at = intake.index("with attachment_lock():")
    assert conversion_at < lock_at
    assert "read_bytes()" not in intake

    prepared_start = source.index("def _api_attach_prepared_locked")
    prepared_end = source.index("\ndef ", prepared_start + 5) if "\ndef " in source[prepared_start + 5:] else len(source)
    prepared = source[prepared_start:prepared_end]
    assert "adopt_staged_file(" in prepared
    assert "write_staged_file(" not in prepared


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
        raise SystemExit(f"{failures} resource HEIC tests failed")
    print(f"{len(TESTS)} resource HEIC tests passed")
