"""Bounded-memory inbound upload staging resource tests."""
from __future__ import annotations

import hashlib
import io
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TMP = Path(tempfile.mkdtemp(prefix="cryopit-resource-stage2-"))
os.environ["CRYOPIT_EXPORT_DIR"] = str(TMP / "exports")
sys.path.insert(0, str(ROOT))

from cryopit.upload_staging import (EmptyUpload, UploadTooLarge,
                                    cleanup_staged_upload, stage_upload_stream,
                                    staging_dir, sweep_staged_uploads)


class TrackingStream(io.BytesIO):
    def __init__(self, data: bytes):
        super().__init__(data)
        self.requested_sizes = []

    def read(self, size=-1):
        self.requested_sizes.append(size)
        return super().read(size)


def _parts():
    root = staging_dir()
    return list(root.glob("*.upload.part")) if root.exists() else []


def test_streams_in_bounded_chunks_and_computes_digest_and_header():
    payload = b"\xff\xd8\xff" + os.urandom(3 * 1024 * 1024 + 321)
    stream = TrackingStream(payload)
    staged = stage_upload_stream(stream, max_bytes=len(payload) + 1, chunk_size=128 * 1024)
    try:
        assert staged.size_bytes == len(payload)
        assert staged.sha256 == hashlib.sha256(payload).hexdigest()
        assert staged.head == payload[:32]
        assert staged.path.read_bytes() == payload
        assert max(size for size in stream.requested_sizes if size >= 0) == 128 * 1024
        assert -1 not in stream.requested_sizes
    finally:
        cleanup_staged_upload(staged.path)
    assert not _parts()


def test_oversized_upload_removes_partial_file():
    payload = os.urandom(2 * 1024 * 1024)
    try:
        stage_upload_stream(io.BytesIO(payload), max_bytes=1024 * 1024,
                            chunk_size=256 * 1024)
    except UploadTooLarge:
        pass
    else:
        raise AssertionError("oversized upload was accepted")
    assert not _parts()


def test_empty_upload_removes_partial_file():
    try:
        stage_upload_stream(io.BytesIO(b""), max_bytes=1024)
    except EmptyUpload:
        pass
    else:
        raise AssertionError("empty upload was accepted")
    assert not _parts()


def test_startup_sweep_removes_only_cryopit_upload_parts():
    root = staging_dir()
    stale_a = root / "upload-old-a.upload.part"
    stale_b = root / "upload-old-b.upload.part"
    unrelated = root / "keep-me.txt"
    stale_a.write_bytes(b"a")
    stale_b.write_bytes(b"b")
    unrelated.write_bytes(b"keep")
    removed = sweep_staged_uploads()
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
        raise SystemExit(f"{failures} resource upload tests failed")
    print(f"{len(TESTS)} resource upload tests passed")
