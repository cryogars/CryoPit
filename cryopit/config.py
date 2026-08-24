"""CryoPit configuration.

Every setting is env-driven with a sensible local-use default. A `.env` file
next to the process is honored when python-dotenv is installed.
"""
import os
import re
import secrets
from datetime import date

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def current_water_year(today=None):
    """US hydrologic water year (USGS convention): Oct 1 – Sep 30, named for the
    calendar year in which it ENDS. Oct/Nov/Dec belong to the next year's WY.
    e.g. 2025-11-15 -> 2026; 2026-06-25 -> 2026."""
    d = today or date.today()
    return d.year + 1 if d.month >= 10 else d.year


def _bool(name, default):
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


def _int(name, default, minimum=None, maximum=None):
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise SystemExit(f"{name} must be an integer (got {raw!r}).") from exc
    if minimum is not None and value < minimum:
        raise SystemExit(f"{name} must be at least {minimum}.")
    if maximum is not None and value > maximum:
        raise SystemExit(f"{name} must not exceed {maximum}.")
    return value


DB_PATH        = os.getenv("CRYOPIT_DB_PATH", "cryopit.db")
# The research group (shown in the in-app topbar badge)
RESEARCH_GROUP = os.getenv("CRYOPIT_RESEARCH_GROUP", "CryoGARS")
# Institution is shown in the browser tab
INSTITUTION    = os.getenv("CRYOPIT_INSTITUTION", "Boise State University")
# Campaign code defaults to the current water year (e.g. WY2026), recomputed at
# startup so it rolls over automatically each October 1. Set CRYOPIT_CAMPAIGN in
# .env to override.
CAMPAIGN       = os.getenv("CRYOPIT_CAMPAIGN") or f"WY{current_water_year()}"
PORT           = _int("CRYOPIT_PORT", os.getenv("CRYOPIT_API_PORT", "8502"), 1, 65535)
# Bind address. Default 127.0.0.1 = local only (safe). Set 0.0.0.0 to accept
# connections from other machines within network.
HOST           = os.getenv("CRYOPIT_HOST", "127.0.0.1")
# Number of concurrent requests Waitress will serve. Stage 5 deliberately keeps
# eight as the default: expensive HEIC/profile work is bounded independently,
# while those requests still occupy HTTP threads while active (and while waiting
# for their operation-specific semaphore). A shared deployment should therefore
# lower this only after representative load testing rather than using thread
# count as the primary memory control.
THREADS        = _int("CRYOPIT_THREADS", 8, 1, 256)
# Default destination for server-side CSV writes. Point it at a mounted Drive,
# an S3-backed mount, or a synced repo directory.
EXPORT_DIR     = os.getenv("CRYOPIT_EXPORT_DIR", "exports")
# Saved-pits / edit workflow. When disabled, the sidebar list and load route are
# off. A deployer can set this false to disallow editing.
ENABLE_EDIT    = _bool("CRYOPIT_ENABLE_EDIT", "true")
# Field-entry example placeholders are presentation-only. Disabled by default for
# production/field use; instructional placeholders, configured defaults, derived
# values, and user-triggered autofill/copy features are unaffected.
SHOW_EXAMPLE_PLACEHOLDERS = _bool("CRYOPIT_SHOW_EXAMPLE_PLACEHOLDERS", "false")
# Saved Pits result-page size (per user); the API caps each request at 50.
SAVED_PITS_LIMIT = _int("CRYOPIT_SAVED_PITS_LIMIT", 10, 1, 50)

# ---------------------------------------------------------------------------
# Identity.
#
# Real deployments put CryoPit behind an SSO reverse proxy that injects an
# authenticated-username header; CryoPit only READS it, never handles
# credentials. Because ANY client can forge a header, the header is honored
# ONLY when CRYOPIT_TRUST_PROXY_AUTH is explicitly enabled — i.e. when the
# deployer asserts "a proxy in front of me strips/sets this header and nothing
# else can reach the app directly". With the flag off (the default, and the
# right setting for local use), every request is DEV_USER regardless of any
# headers, so a spoofed X-Remote-User can't impersonate anyone.
# ---------------------------------------------------------------------------
DEV_USER         = os.getenv("CRYOPIT_DEV_USER", "local")

# Resolution of the PNG written into each pit's figures/ folder. The on-screen
# preview is always 150 and is not affected. Stage 0 resource measurements
# showed that 600+ DPI rasters can consume more than a GiB for a complex pit,
# while every archive already includes a vector PDF. Keep the raster bounded:
# 300 DPI is the highest supported PNG setting; use the PDF when a publication
# workflow needs arbitrary scaling.
FIGURE_DPI       = _int("CRYOPIT_FIGURE_DPI", 150, 72, 300)
# Full server-side Matplotlib figures are another genuinely memory-intensive
# path. Bound them separately from HTTP threads, just as HEIC conversion is
# bounded separately. Two concurrent 40-layer 150-DPI renders were ~1.16 GiB
# in the Stage 4 baseline container, leaving routine requests available while
# preventing all eight Waitress threads from rasterizing at once.
PROFILE_CONCURRENCY = _int("CRYOPIT_PROFILE_CONCURRENCY", 2, 1, 16)
AUTH_HEADER      = os.getenv("CRYOPIT_AUTH_HEADER", "X-Remote-User").strip()
TRUST_PROXY_AUTH = _bool("CRYOPIT_TRUST_PROXY_AUTH", "false")
IDENTITY_MAX_LENGTH = _int("CRYOPIT_IDENTITY_MAX_LENGTH", 255, 16, 1024)

# CSRF tokens are stateless HMACs bound to the authenticated owner. A random
# process-local secret is sufficient for a one-laptop deployment. SSO-backed
# deployments must configure a stable secret so all workers validate the same
# token and restarts do not invalidate an open form unexpectedly.
_SECRET_FROM_ENV = os.getenv("CRYOPIT_SECRET_KEY", "").strip()
SECRET_KEY = _SECRET_FROM_ENV or secrets.token_urlsafe(48)
SECRET_KEY_CONFIGURED = bool(_SECRET_FROM_ENV)
CSRF_TTL_SECONDS = _int("CRYOPIT_CSRF_TTL_SECONDS", 43200, 300, 86400)

# Reject request bodies larger than this before Flask buffers/parses them.
MAX_BODY_MB = _int("CRYOPIT_MAX_BODY_MB", 16, 2, 1024)
ATTACHMENT_MAX_MB = _int("CRYOPIT_ATTACHMENT_MAX_MB", 10, 1, 512)
# HEIC decoding expands compressed phone photos into large pixel buffers. Keep
# conversion concurrency separate from HTTP worker concurrency so routine form
# requests are not throttled merely to bound this expensive path. The default
# of one preserves CryoPit's pre-Stage-3 effective serialization; tune only
# after representative HEIC benchmarking on the deployment host.
HEIC_CONCURRENCY = _int("CRYOPIT_HEIC_CONCURRENCY", 1, 1, 32)

# App-layer abuse controls. These are per process and complement, rather than
# replace, rate limits at the institutional reverse proxy.
RATE_LIMIT_WRITES_PER_MINUTE = _int("CRYOPIT_RATE_LIMIT_WRITES_PER_MINUTE", 120, 1, 10000)
RATE_LIMIT_UPLOADS_PER_MINUTE = _int("CRYOPIT_RATE_LIMIT_UPLOADS_PER_MINUTE", 200, 1, 10000)
RATE_LIMIT_EXPORTS_PER_MINUTE = _int("CRYOPIT_RATE_LIMIT_EXPORTS_PER_MINUTE", 12, 1, 10000)

ENABLE_HSTS = _bool("CRYOPIT_ENABLE_HSTS", "false")
LOG_LEVEL = os.getenv("CRYOPIT_LOG_LEVEL", "INFO").strip().upper()

NO_DATA        = -9999

# SQLite journal mode. WAL (default) gives concurrent readers+writer on REAL
# LOCAL DISK; DELETE is the conservative classic mode for network-backed
# storage (e.g. PythonAnywhere). Full trade-offs: docs/CONFIGURATION.md.
SQLITE_JOURNAL = os.environ.get("CRYOPIT_SQLITE_JOURNAL", "WAL").upper()
if SQLITE_JOURNAL not in ("WAL", "DELETE", "TRUNCATE", "PERSIST", "MEMORY", "OFF"):
    SQLITE_JOURNAL = "WAL"


_HEADER_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")

def validate_config():
    """Fail early on production settings that would weaken the SSO boundary."""
    if not AUTH_HEADER or not _HEADER_NAME.fullmatch(AUTH_HEADER):
        raise SystemExit("CRYOPIT_AUTH_HEADER must be one valid HTTP header name.")
    if TRUST_PROXY_AUTH and not SECRET_KEY_CONFIGURED:
        raise SystemExit(
            "CRYOPIT_SECRET_KEY is required when CRYOPIT_TRUST_PROXY_AUTH=true. "
            "Set it to a stable random value of at least 32 characters."
        )
    if TRUST_PROXY_AUTH and len(SECRET_KEY) < 32:
        raise SystemExit("CRYOPIT_SECRET_KEY must be at least 32 characters.")
    if MAX_BODY_MB <= ATTACHMENT_MAX_MB:
        raise SystemExit(
            "CRYOPIT_MAX_BODY_MB must be larger than CRYOPIT_ATTACHMENT_MAX_MB "
            "to allow multipart upload overhead."
        )
    if LOG_LEVEL not in {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}:
        raise SystemExit("CRYOPIT_LOG_LEVEL must be CRITICAL, ERROR, WARNING, INFO, or DEBUG.")
