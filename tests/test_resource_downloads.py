"""Disk-backed download ZIP lifecycle resource tests."""
from __future__ import annotations

import io
import os
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TMP = Path(tempfile.mkdtemp(prefix="cryopit-resource-stage1-"))
os.environ["CRYOPIT_EXPORT_DIR"] = str(TMP / "exports")
sys.path.insert(0, str(ROOT))

from cryopit.download_staging import (cleanup_staged_zip, create_staged_zip_path,
                                      staging_dir, stream_staged_zip,
                                      sweep_staged_downloads)
from cryopit.export import write_zip_to_path


def _fixture():
    upload = TMP / "field-photo.jpg"
    upload.write_bytes(b"\xff\xd8\xff\xe0" + b"field-photo" * 100)
    csvs = {
        "WY2026_TEST_20260210_siteDetails_v01_0.csv": "# PitID,TEST\r\n",
        "WY2026_TEST_20260210_temperature_v01_0.csv": "# Height (cm),Temperature (C)\r\n",
    }
    meta = {"campaign": "WY2026", "pit_id": "TEST"}
    extras = {
        "figures/WY2026_TEST_profile.png": b"PNG-BYTES",
        "figures/WY2026_TEST_profile.pdf": b"%PDF-1.4\nPDF-BYTES",
    }
    uploads = {"uploads/field/field-photo.jpg": str(upload)}
    return csvs, meta, extras, uploads


def test_zip_is_written_to_private_staging_and_has_expected_members():
    csvs, meta, extras, uploads = _fixture()
    path = create_staged_zip_path()
    assert path.parent == staging_dir()
    assert path.name.endswith(".zip.part")
    zipname, size = write_zip_to_path(csvs, meta, path, extras=extras, uploads=uploads)
    assert zipname == "WY2026_TEST.zip"
    assert size == path.stat().st_size > 0
    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
        assert "csv/WY2026_TEST_20260210_siteDetails_v01_0.csv" in names
        assert "csv/WY2026_TEST_20260210_temperature_v01_0.csv" in names
        assert zf.read("figures/WY2026_TEST_profile.png") == b"PNG-BYTES"
        assert zf.read("figures/WY2026_TEST_profile.pdf").startswith(b"%PDF")
        assert zf.read("uploads/field/field-photo.jpg").startswith(b"\xff\xd8\xff\xe0")
    cleanup_staged_zip(path)
    assert not path.exists()


def test_stream_removes_zip_after_normal_completion():
    csvs, meta, extras, uploads = _fixture()
    path = create_staged_zip_path()
    _, expected_size = write_zip_to_path(csvs, meta, path, extras=extras, uploads=uploads)
    payload = b"".join(stream_staged_zip(path, chunk_size=97))
    assert len(payload) == expected_size
    assert payload.startswith(b"PK")
    assert not path.exists()


def test_stream_close_removes_zip_after_interrupted_download():
    csvs, meta, extras, uploads = _fixture()
    path = create_staged_zip_path()
    write_zip_to_path(csvs, meta, path, extras=extras, uploads=uploads)
    stream = stream_staged_zip(path, chunk_size=64)
    first = next(stream)
    assert first
    assert path.exists()
    stream.close()  # models a WSGI server closing the iterable on disconnect
    assert not path.exists()


def test_startup_sweep_removes_only_cryopit_download_parts():
    root = staging_dir()
    stale_a = root / "download-old-a.zip.part"
    stale_b = root / "download-old-b.zip.part"
    unrelated = root / "keep-me.txt"
    stale_a.write_bytes(b"a")
    stale_b.write_bytes(b"b")
    unrelated.write_bytes(b"keep")
    removed = sweep_staged_downloads()
    assert removed == 2
    assert not stale_a.exists() and not stale_b.exists()
    assert unrelated.read_bytes() == b"keep"


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
        raise SystemExit(f"{failures} resource download tests failed")
    print(f"{len(TESTS)} resource download tests passed")
