"""Bound server-side profile rendering independently from HTTP concurrency.

Matplotlib/Agg rasterization is one of CryoPit's genuinely memory-intensive
operations.  A complex snow profile can use hundreds of MiB transiently even
at the normal 150-DPI preview resolution.  The application should therefore
keep serving inexpensive form/database requests while limiting how many full
profile figures may be constructed at once.

This process-local semaphore is deliberately independent from the storage
lifecycle lock and the HEIC conversion semaphore.  It controls only profile
rendering; Waitress may continue to serve other requests on its remaining
threads.
"""
from __future__ import annotations

import contextlib
import threading

from .config import PROFILE_CONCURRENCY

_PROFILE_SLOTS = threading.BoundedSemaphore(PROFILE_CONCURRENCY)


@contextlib.contextmanager
def profile_render_slot():
    """Acquire one profile-render permit and always return it on exit."""
    _PROFILE_SLOTS.acquire()
    try:
        yield
    finally:
        _PROFILE_SLOTS.release()
