"""HTTP layer: the form page and the JSON API.

The page and the API share one origin (one Flask process), so the client uses
relative paths and no CORS is ever needed.
"""
import base64
import hashlib
import json
import logging
import os
import re
from datetime import date as _date

from flask import Blueprint, Response, abort, jsonify, request

from .auth import current_user
from .config import (ATTACHMENT_MAX_MB, AUTH_HEADER, CAMPAIGN, CSRF_TTL_SECONDS, ENABLE_EDIT,
                     INSTITUTION, RESEARCH_GROUP, SAVED_PITS_LIMIT, SECRET_KEY,
                     SHOW_EXAMPLE_PLACEHOLDERS)
from .config import EXPORT_DIR, DB_PATH, FIGURE_DPI
from .db import get_conn
from .density import DensityValidationError
from .download_staging import (cleanup_staged_zip, create_staged_zip_path,
                               stream_staged_zip)
from .export import export_from_payload, write_zip_to_path, _fname, _safe_name
from .archive_lifecycle import archive_payload, recover_pending
from .attachment_storage import (adopt_staged_file, attachment_lock, begin_delete, reconcile_site,
                                 recover_upload, remove_relpath, target_relpath)
from .security import issue_csrf_token
from .upload_staging import (EmptyUpload, UploadTooLarge, cleanup_staged_upload,
                             stage_upload_stream)
from .heic_conversion import convert_heic_to_jpeg
from .profile_rendering import profile_render_slot
from .repository import (attachment_upload_summary, cancel_attachment_upload,
                         get_site_state, list_attachment_uploads,
                         list_owner_campaigns, list_pending_pits, load_pit,
                         normalize_queue_id, search_pits, workspace_summary)

bp = Blueprint("cryopit", __name__)

_HERE = os.path.dirname(os.path.abspath(__file__))
_TEMPLATES = os.path.join(_HERE, "templates")
_SECTIONS_DIR = os.path.join(_TEMPLATES, "sections")
_JS_DIR = os.path.join(_HERE, "static", "js")
_CSS_DIR = os.path.join(_HERE, "static", "css")
_LOGO = os.path.join(_HERE, "static", "logo.svg")


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


_EXAMPLE_PLACEHOLDER_ATTR = re.compile(r'data-example-placeholder="([^"]*)"')

def _render_example_placeholders(html):
    """Materialize only placeholders explicitly classified as example data.

    The data-example-placeholder marker stays in the HTML so the distinction
    remains inspectable/testable. When examples are disabled, the field simply
    has no visible placeholder. Instructional placeholders never use this marker.
    """
    if not SHOW_EXAMPLE_PLACEHOLDERS:
        return html
    return _EXAMPLE_PLACEHOLDER_ATTR.sub(
        lambda m: f'{m.group(0)} placeholder="{m.group(1)}"', html
    )


# ---------------------------------------------------------------------------
# Page cache. _assemble_page() reads ~27 files (sections, JS modules, CSS) and
# _render_form() re-does every substitution — on EVERY GET /. The result is
# identical between requests unless a source file changes on disk, so it is
# cached and invalidated by a cheap stat() fingerprint of the parts. Editing a
# section/JS/CSS file still shows up on the next reload with no restart, which
# is the property the no-build-tooling design depends on.
# ---------------------------------------------------------------------------
_page_cache = {"stamp": None, "html": None}


def _source_files():
    yield os.path.join(_TEMPLATES, "base.html")
    yield os.path.join(_TEMPLATES, "rail.html")
    yield os.path.join(_TEMPLATES, "workspace.html")
    yield _LOGO
    for d, ext in ((_SECTIONS_DIR, ".html"), (_JS_DIR, ".js"), (_CSS_DIR, ".css")):
        for f in sorted(os.listdir(d)):
            if f.endswith(ext):
                yield os.path.join(d, f)


def _sources_stamp():
    """(path, mtime_ns, size) for every file the page is built from. Catches
    edits, additions and deletions — adding a section file changes the tuple."""
    try:
        return tuple((p, os.stat(p).st_mtime_ns, os.stat(p).st_size)
                     for p in _source_files())
    except OSError:
        return None   # something moved mid-read: skip the cache this time


def _assemble_page():
    """MODULAR FRONTEND: the page is assembled from parts so each form
    section, and each JS concern, lives in its own file.
      templates/base.html            shell (head/topbar/nav/main)
      templates/sections/NN_*.html   one file per form section, included in
                                     filename order; each <section> carries
                                     data-nav="Label", and the sidebar nav is
                                     GENERATED from the files — so adding a
                                     section really is just adding a file
      templates/rail.html            the live profile rail
      static/css/NN_*.css            style modules, concatenated in order
      static/js/NN_*.js              JS modules, concatenated in order
    Everything is inlined into one response: a single request, no build
    tooling — this app works offline in the field."""
    base = _read(os.path.join(_TEMPLATES, "base.html"))
    parts = [_read(os.path.join(_SECTIONS_DIR, f))
             for f in sorted(os.listdir(_SECTIONS_DIR)) if f.endswith(".html")]
    sections = "\n".join(parts)
    rail = _read(os.path.join(_TEMPLATES, "rail.html"))
    workspace = _read(os.path.join(_TEMPLATES, "workspace.html"))
    js = "\n".join(_read(os.path.join(_JS_DIR, f))
                    for f in sorted(os.listdir(_JS_DIR)) if f.endswith(".js"))
    css = "\n".join(_read(os.path.join(_CSS_DIR, f))
                     for f in sorted(os.listdir(_CSS_DIR)) if f.endswith(".css"))
    return (base.replace("__CSS__", css)
                .replace("__NAV__", _build_nav(parts))
                .replace("__SECTIONS__", sections)
                .replace("__WORKSPACE__", workspace)
                .replace("__RAIL__", rail)
                .replace("__APP_JS__", js))


def _build_nav(section_parts):
    """Sidebar nav generated from the section files themselves: each
    <section id="sN" data-nav="Label"> becomes an index entry (the two-digit
    number comes from N). One file = one section = one nav item."""
    items = []
    for part in section_parts:
        mid = re.search(r'<section class="sec" id="s(\d+)"[^>]*data-nav="([^"]+)"', part)
        if not mid:
            continue
        n, label = int(mid.group(1)), mid.group(2)
        active = " active" if not items else ""
        # A nav entry is a control, so it is a real <button>: focusable, and
        # reachable by keyboard. As a <div onclick> it was mouse-only.
        # aria-current marks the section the index is pointing at.
        cur = ' aria-current="true"' if not items else ""
        items.append(
            f'<button type="button" class="idx-item{active}" data-t="s{n}"{cur} '
            f'aria-controls="s{n}" onclick="nav(this)">'
            f'<span class="idx-num">{n:02d}</span>'
            f'<span class="idx-lbl">{label}</span>'
            f'<span class="idx-pip" id="p{n}" aria-hidden="true"></span></button>')
    return "\n  ".join(items)

_SAVED_PITS_SECTION = """
  <div class="nav-foot" id="saved-pits-panel">
    <div class="saved-pits-heading">
      <div class="nav-foot-label">Saved pits</div>
      <span id="saved-pits-count" class="saved-pits-count" aria-live="polite"></span>
    </div>
    <form id="saved-pits-filters" class="saved-pits-filters" role="search">
      <label class="sr-only" for="saved-pits-search">Search your saved pits</label>
      <div class="saved-pits-search-row">
        <input id="saved-pits-search" type="search" maxlength="200"
               placeholder="Pit, site, campaign, observer…" autocomplete="off">
        <button id="saved-pits-clear" type="reset" title="Clear saved-pit filters"
                aria-label="Clear saved-pit filters">×</button>
      </div>
      <details class="saved-pits-filter-more">
        <summary>Filters and sort</summary>
        <label>Campaign
          <select id="saved-pits-campaign"><option value="">All campaigns</option></select>
        </label>
        <div class="saved-pits-date-row">
          <label>From<input id="saved-pits-date-from" type="date"></label>
          <label>To<input id="saved-pits-date-to" type="date"></label>
        </div>
        <label>Sort
          <select id="saved-pits-sort">
            <option value="date" selected>Observation date — newest first</option>
            <option value="updated">Recently updated</option>
            <option value="pit_id">Pit ID</option>
          </select>
        </label>
      </details>
    </form>
    <div id="recovery-pits" class="recovery-pits" hidden></div>
    <div id="saved-pits-list" aria-live="polite" aria-busy="true">
      <span class="nav-foot-empty">loading…</span>
    </div>
    <button id="saved-pits-more" class="saved-pits-more" type="button" hidden>Load more</button>
  </div>
"""


def _render_form(user=None):
    stamp = _sources_stamp()
    if stamp is not None and _page_cache["stamp"] == stamp:
        html = _page_cache["html"]
    else:
        html = _assemble_page()
        # The logo is a standalone asset (static/logo.svg — reusable in docs,
        # posters, and as the favicon) inlined here so the topbar needs no extra
        # request and can force it white over the dark bar.
        html = html.replace("__LOGO_SVG__", _read(_LOGO))
        html = (html
                .replace("__PAGE_TITLE__", f"CryoPit · Snow Pit Logger · {INSTITUTION}")
                .replace("__RESEARCH_GROUP__", RESEARCH_GROUP)
                .replace("__CAMPAIGN__", CAMPAIGN)
                .replace("__ENABLE_EDIT__", "true" if ENABLE_EDIT else "false")
                .replace("__SHOW_EXAMPLE_PLACEHOLDERS__", "true" if SHOW_EXAMPLE_PLACEHOLDERS else "false")
                # Limits are written into the page from _ATTACH_LIMITS rather than
                # typed into the template. They were hardcoded in three places
                # (server, client default, and this label) and the labels still read
                # "max 5" after the caps moved to 3/6/20 — the exact drift this
                # substitution removes.
                .replace("__LIM_JSON__", json.dumps(_ATTACH_LIMITS, sort_keys=True))
                .replace("__LIM_SHEET__", str(_ATTACH_LIMITS["sheet"]))
                .replace("__LIM_PITWALL__", str(_ATTACH_LIMITS["pitwall"]))
                .replace("__LIM_STRAT__", str(_ATTACH_LIMITS["stratigraphy"]))
                .replace("__LIM_TOTAL__", str(_ATTACH_TOTAL))
                .replace("__LIM_MB__", str(_ATTACH_MAX_BYTES // (1024 * 1024)))
                .replace("__LIM_BYTES__", str(_ATTACH_MAX_BYTES))
                .replace("__SAVED_PITS_SECTION__", _SAVED_PITS_SECTION if ENABLE_EDIT else ""))
        html = _render_example_placeholders(html)
        if stamp is not None:
            # Cache the owner-independent page with the CSRF placeholder intact.
            # The per-owner token is applied only to the returned copy below.
            _page_cache["stamp"], _page_cache["html"] = stamp, html

    # _render_form() is the single complete-page renderer used by both the
    # route and the DOM harness. Keep request-specific CSRF substitution here
    # so tests cannot accidentally exercise a less-complete page than users.
    # Outside a request, use the configured local identity (or an explicit
    # test identity) to produce a syntactically complete page.
    if user is None:
        try:
            user = current_user()
        except RuntimeError:
            from .config import DEV_USER
            user = DEV_USER
    token = issue_csrf_token(user, SECRET_KEY, ttl_seconds=CSRF_TTL_SECONDS)
    return html.replace("__CSRF_TOKEN__", token)


def _json_or_400():
    """Reject non-JSON bodies. Browsers can't send Content-Type:
    application/json cross-origin without a CORS preflight (which we never
    grant), so requiring it blocks simple cross-site form POSTs."""
    data = request.get_json(silent=True)
    if data is None or not isinstance(data, dict):
        abort(400, description="Expected a JSON object body")
    return data


@bp.get("/")
def index():
    """The page is one self-contained 110 KB document (HTML + CSS + JS inlined),
    so a reload re-sends all of it. An ETag lets the browser skip the body when
    nothing changed — which matters most on the field-tablet case this app is
    built for: a tethered or flaky connection, and reloads that are usually
    'did my draft survive?' rather than 'did the app change?'."""
    user = current_user()
    html = _render_form(user)
    etag = hashlib.sha256(html.encode("utf-8")).hexdigest()[:32]
    if request.headers.get("If-None-Match") == etag:
        return "", 304, {"ETag": etag, "Cache-Control": "private, no-cache", "Vary": AUTH_HEADER}
    return html, 200, {
        "ETag": etag,
        # no-cache = revalidate every time (so an update is never missed),
        # but a 304 still saves the payload.
        "Cache-Control": "private, no-cache",
        "Vary": AUTH_HEADER,
        "Content-Type": "text/html; charset=utf-8",
    }


def _pit_date_arg(name):
    value = (request.args.get(name) or "").strip()
    if not value:
        return ""
    try:
        return _date.fromisoformat(value).isoformat()
    except ValueError:
        abort(400, description=f"{name} must be an ISO date (YYYY-MM-DD)")


@bp.get("/api/pits")
def api_pits():
    if not ENABLE_EDIT:
        return jsonify({"pits": [], "pending": [], "campaigns": [],
                        "total": 0, "has_more": False, "offset": 0,
                        "limit": SAVED_PITS_LIMIT})
    try:
        limit = int(request.args.get("limit", SAVED_PITS_LIMIT))
        offset = int(request.args.get("offset", 0))
    except (TypeError, ValueError):
        abort(400, description="limit and offset must be integers")
    if offset < 0:
        abort(400, description="offset must not be negative")
    sort = (request.args.get("sort") or "date").strip()
    if sort not in {"updated", "date", "pit_id"}:
        abort(400, description="sort must be updated, date, or pit_id")
    date_from = _pit_date_arg("date_from")
    date_to = _pit_date_arg("date_to")
    if date_from and date_to and date_from > date_to:
        abort(400, description="date_from must not be later than date_to")
    result = search_pits(
        limit=limit, offset=offset,
        query=(request.args.get("q") or "").strip(),
        campaign=(request.args.get("campaign") or "").strip(),
        date_from=date_from, date_to=date_to, sort=sort,
    )
    if result["offset"] and result["offset"] >= result["total"]:
        # A concurrent deletion or a newly narrowed filter can make a stale
        # load-more offset land beyond the result set. Return a valid empty page
        # rather than treating that normal race as an error.
        result["has_more"] = False
    return jsonify({**result, "pending": list_pending_pits(),
                    "campaigns": list_owner_campaigns()})


@bp.get("/api/workspace")
def api_workspace():
    """Owner-scoped landing-page summary.

    Identity is derived from the trusted authentication layer, never from a
    query parameter or browser-supplied owner field.
    """
    if not ENABLE_EDIT:
        return jsonify({"recent": [], "total_pits": 0, "recovery": [],
                        "recovery_count": 0, "expected_photos": 0,
                        "missing_attachments": 0})
    return jsonify(workspace_summary(recent_limit=3))


@bp.get("/api/load/<site_id>")
def api_load(site_id):
    if not ENABLE_EDIT:
        return jsonify({"ok": False, "msg": "Loading is disabled on this deployment."})
    pit, err = load_pit(site_id)
    if err:
        return jsonify({"ok": False, "msg": err})
    state = get_site_state(site_id)
    return jsonify({"ok": True, "pit": pit, "site_id": site_id,
                    "pit_id": state["pit_id"]})


@bp.post("/api/recover/<site_id>")
def api_recover(site_id):
    if not ENABLE_EDIT:
        return jsonify({"ok": False, "msg": "Recovery is disabled on this deployment."}), 403
    try:
        result = recover_pending(site_id, _render_figures)
        return jsonify(result)
    except Exception:
        logging.getLogger(__name__).exception("archive recovery failed")
        return jsonify({"ok": False, "msg": "Recovery could not be completed. Retry or review the server log."}), 409


@bp.post("/api/download")
def api_download():
    """Build the seven CSVs and stream them as a disk-backed ZIP.

    Downloads are pure file delivery: they do not archive the pit or modify the
    database. The ZIP is assembled beneath ``EXPORT_DIR/.download-staging``
    instead of in ``BytesIO``, so a photo-heavy export does not require a
    ZIP-sized Python memory buffer. The response iterator removes the scratch
    file on completion/close; startup reconciliation removes leftovers from a
    killed process or host restart.

    Errors still come back as JSON, so the client distinguishes them by
    Content-Type rather than by parsing a body it may not be able to hold.
    """
    payload = _json_or_400()
    m = payload.get("meta") or {}
    staged_zip = None
    try:
        csvs = export_from_payload(payload)
        png, pdf = _render_figures(payload)
        stem = _fname(m, "profile").replace(".csv", "")
        extras = {}
        if png:
            extras[f"figures/{stem}.png"] = png
        if pdf:
            extras[f"figures/{stem}.pdf"] = pdf
        uploads = _existing_uploads(payload.get("site_id"))

        staged_zip = create_staged_zip_path()
        zipname, zip_size = write_zip_to_path(
            csvs, m, staged_zip, extras=extras, uploads=uploads
        )
        response = Response(
            stream_staged_zip(staged_zip),
            mimetype="application/zip",
            direct_passthrough=True,
            headers={
                "Content-Disposition": f'attachment; filename="{zipname}"',
                "Content-Length": str(zip_size),
                # the filename travels in its own header so the client does not
                # have to parse Content-Disposition
                "X-CryoPit-Zipname": zipname,
            },
        )
        # Defense in depth: Response.close() runs callbacks even if a server
        # closes the iterable before exhausting it. The generator also cleans
        # up in ``finally``; cleanup is idempotent.
        response.call_on_close(lambda path=staged_zip: cleanup_staged_zip(path))
        return response
    except DensityValidationError as e:
        if staged_zip is not None:
            cleanup_staged_zip(staged_zip)
        return jsonify({"ok": False, "msg": str(e)}), 400
    except Exception:
        if staged_zip is not None:
            cleanup_staged_zip(staged_zip)
        logging.getLogger(__name__).exception("download export failed")
        return jsonify({"ok": False, "msg": "Download generation failed. Use the request ID when contacting support."}), 500


@bp.post("/api/archive")
def api_archive():
    """Create or update a pit through the recoverable archive lifecycle."""
    payload = _json_or_400()
    site_id = (payload.get("site_id") or "").strip() or None
    result = archive_payload(payload, site_id, _render_figures)
    code = 200 if result.get("ok") else (409 if result.get("pending") or result.get("exists") else 400)
    return jsonify(result), code

def _num_or_none(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _layer_key(top_cm, bottom_cm):
    """Stable key for one layer's photos. Same rule as the folder name, so the
    UI count and the folder on disk can never disagree."""
    return _layer_folder(top_cm, bottom_cm)


def _layer_folder(top_cm, bottom_cm):
    """Depth-interval folder name, zero-padded so it sorts sensibly.

    `062-045cm` rather than `layer2`. The ordinal shifts the moment anyone
    inserts a layer above it; the interval is a measurement and does not.
    """
    if top_cm is None:
        return ""
    if bottom_cm is None:
        return f"{int(round(top_cm)):03d}cm"
    return f"{int(round(top_cm)):03d}-{int(round(bottom_cm)):03d}cm"


def _pit_subfolder(m):
    dstr = (m.get("date") or "").replace("-", "")
    return f"{m.get('campaign') or CAMPAIGN}_{m.get('pit_id')}_{dstr}"


def _render_figures(payload):
    """Render the archived PNG + vector PDF under one bounded render slot.

    The Matplotlib figure is constructed once and serialized to both formats.
    PNG remains required; PDF remains best-effort. The archived PNG honors
    ``CRYOPIT_FIGURE_DPI`` while the screen preview remains fixed at 150 DPI.
    """
    try:
        from .plot import render_profile
        with profile_render_slot():
            return render_profile(payload, dpi=FIGURE_DPI, fmt="both")
    except DensityValidationError:
        raise
    except Exception:
        return None, None


def _existing_uploads(site_id):
    """Database-backed attachment files for a completed pit.

    Walking uploads/ used to include orphan files while silently omitting a
    missing-file DB row. The database is authoritative; reconciliation reports
    discrepancies separately.
    """
    if not site_id:
        return {}
    state = get_site_state(site_id)
    if not state or state.get("pending_export_folder") or not state.get("export_folder"):
        return {}
    conn = get_conn()
    try:
        rows = conn.execute(
            """SELECT category,filename,top_cm,bottom_cm FROM attachments a
               JOIN sites s ON s.site_id=a.site_id
               WHERE a.site_id=? AND s.owner=? AND a.pending_delete=0""",
            (site_id, current_user())).fetchall()
    finally:
        conn.close()
    out = {}
    pit_root = os.path.join(EXPORT_DIR, state["export_folder"])
    for category, filename, top_cm, bottom_cm in rows:
        rel = target_relpath(category, top_cm, bottom_cm, filename)
        path = os.path.join(pit_root, *rel.split("/"))
        if os.path.isfile(path):
            out[rel] = path
    return out


@bp.post("/api/profile")
def api_profile():
    """Render the reference profile figure for the CURRENT form state and
    return it as a PNG. Read-only: no database write, no files written."""
    payload = _json_or_400()
    try:
        from .plot import render_profile
        with profile_render_slot():
            png = render_profile(payload)   # screen preview: always 150, see FIGURE_DPI
    except DensityValidationError as e:
        return jsonify({"ok": False, "msg": str(e)}), 400
    except Exception:
        logging.getLogger(__name__).exception("profile rendering failed")
        return jsonify({"ok": False, "msg": "Profile rendering failed. Use the request ID when contacting support."}), 500
    from flask import Response
    return Response(png, mimetype="image/png")


# ---------------------------------------------------------------------------
# Attachments: pit sheet scans + pit-wall / stratigraphy photos.
# Files live in the pit's export folder under uploads/; the DB stores
# metadata + sha256 only. Uploads are decoupled from archiving on purpose —
# a stalled photo upload can never fail the pit's data.
# ---------------------------------------------------------------------------
# Per-category caps. The sheet's cap counts IMAGES; a PDF is presumed to be the
# whole scanned sheet, so it is always exactly one and excludes images entirely.
_ATTACH_LIMITS = {"sheet": 3, "pitwall": 6, "stratigraphy": 20}
# Stratigraphy is counted PER LAYER, not per pit: a 15-layer pit under a
# 20-photo pit-wide cap averages 1.3 photos a layer, which is no budget at all
# when you want a wide shot and a crystal-card shot of each.
_STRAT_PER_LAYER = 20
# Whole-pit cap. Kept at the sum of the per-category caps so it stays a pure
# abuse guard and never silently blocks a category that is still under its own
# limit — with 3 + 6 + 20 a hardcoded 12 would have cut stratigraphy off at
# roughly its sixth photo.
# Whole-pit ceiling. Not rationing — it is the point beyond which the download
# path cannot assemble the result. Sized so normal work never meets it.
_ATTACH_TOTAL = 150
_ATTACH_MAX_BYTES = ATTACHMENT_MAX_MB * 1024 * 1024
_MAGIC = ((b"\xff\xd8\xff", "jpg"), (b"\x89PNG", "png"), (b"%PDF", "pdf"))


# HEIC/HEIF: an ISO-BMFF container, identified by the `ftyp` box at offset 4
# followed by a brand. iPhones have shot HEIC by default since iOS 11, so this
# is the single most likely format a field crew will actually produce.
_HEIF_BRANDS = {b"heic", b"heix", b"hevc", b"heim", b"heis", b"hevm", b"hevs",
                b"mif1", b"msf1", b"heif"}


def _is_heif(data):
    return data[4:8] == b"ftyp" and data[8:12] in _HEIF_BRANDS



def _sniff(data):
    for magic, ext in _MAGIC:
        if data.startswith(magic):
            return ext
    # WebP is a RIFF container: require the 'WEBP' fourcc at bytes 8-12 so a
    # WAV (also RIFF) can't slip through mislabeled.
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "webp"
    if _is_heif(data):
        return "heic"
    return None


@bp.get("/api/attachments/<site_id>")
def api_attachments(site_id):
    recovery = reconcile_site(site_id, current_user(), full=False)
    conn = get_conn()
    try:
        rows = conn.execute(
            """SELECT a.attachment_id,a.category,a.filename,a.uploaded_at,
                      a.top_cm,a.bottom_cm,a.storage_status,a.storage_error
               FROM attachments a
               JOIN sites s ON s.site_id = a.site_id
               WHERE a.site_id = ? AND s.owner = ? AND a.pending_delete=0
               ORDER BY a.attachment_id""",
            (site_id, current_user())).fetchall()
    finally:
        conn.close()
    counts = {}
    per_layer = {}
    for _aid, c, _f, _u, top, bot, _status, _error in rows:
        counts[c] = counts.get(c, 0) + 1
        if c == "stratigraphy" and top is not None:
            per_layer[_layer_key(top, bot)] = per_layer.get(_layer_key(top, bot), 0) + 1
    uploads = list_attachment_uploads(site_id)
    return jsonify({"attachments": [
        {"attachment_id": aid, "category": c, "filename": f, "uploaded_at": u,
         "top_cm": top, "bottom_cm": bot, "storage_status": status,
         "storage_error": error}
        for aid, c, f, u, top, bot, status, error in rows],
        "counts": counts, "per_layer": per_layer,
        "uploads": uploads, "upload_summary": attachment_upload_summary(site_id),
        "recovery": recovery, "limits": _ATTACH_LIMITS, "strat_per_layer": _STRAT_PER_LAYER,
        "total_limit": _ATTACH_TOTAL})


@bp.post("/api/attachment-queue/<site_id>/<queue_id>/cancel")
def api_cancel_attachment_queue(site_id, queue_id):
    try:
        result = cancel_attachment_upload(site_id, queue_id)
    except ValueError as exc:
        return jsonify({"ok": False, "msg": str(exc)}), 400
    if not result.get("ok"):
        return jsonify(result), 409
    return jsonify(result)


@bp.post("/api/attachments/<site_id>/reconcile")
def api_reconcile_attachments(site_id):
    result = reconcile_site(site_id, current_user(), full=True)
    code = 200 if result.get("ok") else (404 if result.get("missing_site") else 409)
    return jsonify(result), code


@bp.post("/api/attachment/<site_id>/<int:attachment_id>/delete")
def api_delete_attachment(site_id, attachment_id):
    try:
        result = begin_delete(site_id, attachment_id, current_user())
    except Exception:
        logging.getLogger(__name__).exception("attachment deletion failed")
        return jsonify({"ok": False, "recoverable": True,
                        "msg": "Attachment deletion needs recovery. Retry or review the server log."}), 500
    if not result.get("ok"):
        return jsonify(result), (404 if result.get("missing") else 409)
    return jsonify(result)


@bp.post("/api/attach/<site_id>")
def api_attach(site_id):
    """Stage/convert one outbox item, then serialize only publication."""
    return _api_attach(site_id)


def _api_attach(site_id):
    """Bounded-memory intake and HEIC preparation outside the storage lock."""
    category = (request.form.get("category") or "").strip()
    top_cm = _num_or_none(request.form.get("top_cm"))
    bottom_cm = _num_or_none(request.form.get("bottom_cm"))
    try:
        queue_id = normalize_queue_id(request.form.get("queue_id"))
    except ValueError as exc:
        return jsonify({"ok": False, "msg": str(exc)}), 400
    if category not in _ATTACH_LIMITS:
        return jsonify({"ok": False, "msg": "Unknown attachment category."}), 400
    f = request.files.get("file")
    if f is None:
        return jsonify({"ok": False, "msg": "No file in request."}), 400
    try:
        inbound = stage_upload_stream(f.stream, max_bytes=_ATTACH_MAX_BYTES)
    except EmptyUpload:
        return jsonify({"ok": False, "msg": "Empty file."}), 400
    except UploadTooLarge:
        return jsonify({"ok": False, "msg": f"File exceeds the {ATTACHMENT_MAX_MB} MB limit."}), 413

    converted = None
    try:
        original_filename = os.path.basename(f.filename or "")
        ext = _sniff(inbound.head)
        if ext is None:
            return jsonify({"ok": False,
                            "msg": "Unsupported file type (JPEG, PNG, WebP, HEIC or PDF)."}), 415
        if category != "sheet" and ext == "pdf":
            return jsonify({"ok": False, "msg": "PDF is accepted for the pit sheet only."}), 415

        # HEIC decoding is deliberately outside the global storage lifecycle
        # lock. Its own bounded semaphore limits decoded-pixel RAM while other
        # users can continue archive/attachment filesystem operations. Input
        # and JPEG output both stay disk-backed beneath .upload-staging.
        source_path = inbound.path
        digest = inbound.sha256
        converted_from = None
        if ext == "heic":
            converted = convert_heic_to_jpeg(inbound.path)
            if converted is not None:
                source_path = converted.path
                digest = converted.sha256
                ext = "jpg"
                converted_from = "heic"

        # Acquire the shared lifecycle lock only after conversion has released
        # its HEIC permit. The authoritative pit/manifest state is re-read below
        # before any bytes are adopted into the scientific archive.
        with attachment_lock():
            return _api_attach_prepared_locked(
                site_id, category, top_cm, bottom_cm, queue_id,
                original_filename, inbound, source_path, digest, ext, converted_from
            )
    finally:
        # Conversion output is either atomically adopted below or remains a
        # scratch file after rejection/failure. In both cases cleanup is safe.
        if converted is not None:
            cleanup_staged_upload(converted.path)
        cleanup_staged_upload(inbound.path)


def _api_attach_prepared_locked(site_id, category, top_cm, bottom_cm, queue_id,
                                original_filename, inbound, source_path, digest, ext,
                                converted_from):
    """Validate/publish one prepared disk-staged upload; caller holds lifecycle lock."""
    client_digest = inbound.sha256
    original_size = inbound.size_bytes

    # Finish any publication left by a lost response or process crash before
    # deciding whether this retry still needs to write bytes.
    reconcile_site(site_id, current_user(), full=False)

    conn = get_conn()
    reserved = False
    export_folder = None
    target_rel = None
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT s.raw_json,s.export_folder,s.pending_export_folder FROM sites s "
            "WHERE s.site_id=? AND s.owner=?", (site_id, current_user())).fetchone()
        if row is None:
            conn.execute("ROLLBACK")
            return jsonify({"ok": False, "msg": "Archive the pit before attaching files."}), 404
        if row[2] is not None or not row[1]:
            conn.execute("ROLLBACK")
            return jsonify({"ok": False, "msg": "Finish archive recovery before attaching files."}), 409
        export_folder = row[1]

        expected = conn.execute(
            """SELECT u.site_id,u.status,u.category,u.original_filename,
                      u.size_bytes,u.client_sha256,u.top_cm,u.bottom_cm,
                      u.attachment_id,a.filename,
                      COALESCE(a.storage_status,'stored'),u.publication_state
               FROM attachment_uploads u
               LEFT JOIN attachments a ON a.attachment_id=u.attachment_id
               WHERE u.queue_id=?""", (queue_id,)).fetchone()
        if expected is None:
            conn.execute("ROLLBACK")
            return jsonify({"ok": False, "expected": False, "queue_id": queue_id,
                            "msg": "This photograph is not registered. Archive Changes first, then retry."}), 409
        if expected[0] != site_id:
            conn.execute("ROLLBACK")
            return jsonify({"ok": False, "msg": "This photo queue item belongs to another pit."}), 409
        if expected[1] == "cancelled":
            conn.execute("ROLLBACK")
            return jsonify({"ok": False, "cancelled": True, "queue_id": queue_id,
                            "msg": "This queued photograph was cancelled."}), 409
        if expected[1] == "stored":
            if expected[8] is None or expected[9] is None:
                conn.execute("ROLLBACK")
                return jsonify({"ok": False, "queue_id": queue_id,
                                "msg": "Stored upload metadata is missing its attachment; recovery is required."}), 409
            if expected[10] != "missing":
                conn.execute("ROLLBACK")
                return jsonify({"ok": True, "duplicate": True, "idempotent": True,
                                "queue_id": queue_id, "attachment_id": expected[8],
                                "filename": expected[9], "category": expected[2],
                                "msg": "This queued photograph was already stored."})
            # Reconciliation found that the DB row survived but its file did
            # not. The same queue ID may now repair that attachment in place.
            conn.execute(
                """UPDATE attachment_uploads SET status='pending',last_error=NULL,
                   publication_state=NULL,staged_relpath=NULL,target_relpath=NULL,
                   server_sha256=NULL,updated_at=datetime('now') WHERE queue_id=?""",
                (queue_id,))
            expected = list(expected)
            expected[1] = "pending"
        if expected[11] is not None:
            conn.execute("ROLLBACK")
            return jsonify({"ok": False, "queue_id": queue_id,
                            "msg": "This upload has an unresolved publication operation; retry recovery first."}), 409

        def same_num(a, b):
            return a is None and b is None or (
                a is not None and b is not None and abs(float(a) - float(b)) < 1e-9)

        if expected[2] != category or not same_num(expected[6], top_cm) or not same_num(expected[7], bottom_cm):
            conn.execute("ROLLBACK")
            return jsonify({"ok": False, "queue_id": queue_id,
                            "msg": "Upload category or layer interval does not match its archive manifest."}), 409
        if expected[3] != original_filename:
            conn.execute("ROLLBACK")
            return jsonify({"ok": False, "queue_id": queue_id,
                            "msg": "Upload filename does not match its archive manifest."}), 409
        if expected[4] is not None and expected[4] != original_size:
            conn.execute("ROLLBACK")
            return jsonify({"ok": False, "queue_id": queue_id,
                            "msg": "Upload size does not match its archive manifest."}), 409
        if expected[5] and expected[5] != client_digest:
            conn.execute("ROLLBACK")
            return jsonify({"ok": False, "queue_id": queue_id,
                            "msg": "Upload checksum does not match its archive manifest."}), 409

        m = (json.loads(row[0]) or {}).get("meta") or {}
        missing_match = conn.execute(
            """SELECT attachment_id FROM attachments WHERE site_id=? AND category=?
               AND pending_delete=0 AND storage_status='missing' AND sha256=?
               AND COALESCE(top_cm,-999)=? AND COALESCE(bottom_cm,-999)=?""",
            (site_id, category, digest,
             top_cm if top_cm is not None else -999,
             bottom_cm if bottom_cm is not None else -999)).fetchone()
        repairing_missing = missing_match is not None
        counts = dict(conn.execute(
            "SELECT category,COUNT(*) FROM attachments WHERE site_id=? AND pending_delete=0 GROUP BY category",
            (site_id,)).fetchall())
        if category == "stratigraphy" and top_cm is not None:
            n_here = conn.execute(
                "SELECT COUNT(*) FROM attachments WHERE site_id=? AND category='stratigraphy' "
                "AND pending_delete=0 AND top_cm IS ? AND bottom_cm IS ?",
                (site_id, top_cm, bottom_cm)).fetchone()[0]
            if n_here - (1 if repairing_missing else 0) >= _STRAT_PER_LAYER:
                conn.execute("ROLLBACK")
                return jsonify({"ok": False,
                                "msg": f"Limit reached for this layer ({_STRAT_PER_LAYER} photos)."}), 409
        elif counts.get(category, 0) - (1 if repairing_missing else 0) >= _ATTACH_LIMITS[category]:
            conn.execute("ROLLBACK")
            return jsonify({"ok": False,
                            "msg": f"Limit reached for {category} ({_ATTACH_LIMITS[category]} files)."}), 409
        if sum(counts.values()) - (1 if repairing_missing else 0) >= _ATTACH_TOTAL:
            conn.execute("ROLLBACK")
            return jsonify({"ok": False, "msg": "Attachment limit reached for this pit."}), 409
        if category == "sheet" and counts.get("sheet", 0) >= 1:
            prev_pdf = conn.execute(
                "SELECT 1 FROM attachments WHERE site_id=? AND category='sheet' "
                "AND pending_delete=0 AND COALESCE(storage_status,'stored')='stored' AND filename LIKE '%.pdf'", (site_id,)).fetchone()
            if prev_pdf or ext == "pdf":
                conn.execute("ROLLBACK")
                return jsonify({"ok": False,
                                "msg": "The sheet is either one PDF or up to three images."}), 409

        already = conn.execute(
            "SELECT attachment_id,filename FROM attachments WHERE site_id=? AND category=? "
            "AND pending_delete=0 AND COALESCE(storage_status,'stored')='stored' AND sha256=? AND COALESCE(top_cm,-999)=? "
            "AND COALESCE(bottom_cm,-999)=?",
            (site_id, category, digest,
             top_cm if top_cm is not None else -999,
             bottom_cm if bottom_cm is not None else -999)).fetchone()
        if already:
            conn.execute(
                """UPDATE attachment_uploads SET status='stored',attachment_id=?,
                   last_error=NULL,publication_state=NULL,staged_relpath=NULL,
                   target_relpath=NULL,server_sha256=?,updated_at=datetime('now')
                   WHERE queue_id=?""", (already[0], digest, queue_id))
            conn.execute("COMMIT")
            where = _layer_folder(top_cm, bottom_cm)
            return jsonify({"ok": True, "duplicate": True, "queue_id": queue_id,
                            "attachment_id": already[0], "filename": already[1],
                            "category": category, "layer": where,
                            "msg": (f"Already attached to {where}" if where
                                    else f"Already attached as {already[1]}")
                                   + ", so it was not added again."})

        sub = _safe_name(_pit_subfolder(m), "pit")
        used = {r[0] for r in conn.execute(
            "SELECT filename FROM attachments WHERE site_id=? AND category=?",
            (site_id, category))}
        for (rel,) in conn.execute(
            """SELECT target_relpath FROM attachment_uploads
               WHERE site_id=? AND status='pending' AND target_relpath IS NOT NULL""",
            (site_id,)):
            used.add(os.path.basename(rel))
        nn = counts.get(category, 0) + 1
        while f"{sub}_{category}_{nn:02d}.{ext}" in used:
            nn += 1
        fname = f"{sub}_{category}_{nn:02d}.{ext}"
        target_rel = target_relpath(category, top_cm, bottom_cm, fname)
        cur = conn.execute(
            """UPDATE attachment_uploads SET publication_state='reserved',
               target_relpath=?,server_sha256=?,staged_relpath=NULL,last_error=NULL,
               updated_at=datetime('now')
               WHERE queue_id=? AND status='pending' AND publication_state IS NULL""",
            (target_rel, digest, queue_id))
        if cur.rowcount != 1:
            conn.execute("ROLLBACK")
            return jsonify({"ok": False, "queue_id": queue_id,
                            "msg": "Another request is already publishing this photograph."}), 409
        conn.execute("COMMIT")
        reserved = True
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        conn.close()

    staged_rel = None
    try:
        staged_rel = adopt_staged_file(
            export_folder, queue_id, source_path, site_id=site_id, owner=current_user()
        )
        conn = get_conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute(
                """UPDATE attachment_uploads SET publication_state='staged',
                   staged_relpath=?,last_error=NULL,updated_at=datetime('now')
                   WHERE queue_id=? AND status='pending' AND publication_state='reserved'""",
                (staged_rel, queue_id))
            if cur.rowcount != 1:
                raise RuntimeError("Upload reservation changed before staging completed.")
            conn.execute("COMMIT")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            remove_relpath(export_folder, staged_rel)
            raise
        finally:
            conn.close()
    except Exception as exc:
        if reserved:
            conn = get_conn()
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    """UPDATE attachment_uploads SET publication_state=NULL,
                       staged_relpath=NULL,target_relpath=NULL,server_sha256=NULL,
                       last_error=?,updated_at=datetime('now')
                       WHERE queue_id=? AND status='pending'""", ("Attachment staging failed; retry from the browser outbox.", queue_id))
                conn.execute("COMMIT")
            except Exception:
                try:
                    conn.execute("ROLLBACK")
                except Exception:
                    pass
            finally:
                conn.close()
        logging.getLogger(__name__).exception("attachment staging failed")
        return jsonify({"ok": False, "queue_id": queue_id,
                        "msg": "Could not stage attachment. The queued copy was retained for retry."}), 500

    try:
        result = recover_upload(queue_id, current_user())
    except Exception:
        logging.getLogger(__name__).exception("attachment publication recovery failed")
        return jsonify({"ok": False, "queue_id": queue_id,
                        "recoverable": True,
                        "msg": "Attachment publication needs recovery. Retry or review the server log."}), 500
    if not result.get("ok"):
        return jsonify({"ok": False, "queue_id": queue_id,
                        "recoverable": True,
                        "msg": result.get("msg") or "Attachment publication remains pending."}), 409
    response_data = {"ok": True, "queue_id": queue_id,
                     "attachment_id": result["attachment_id"],
                     "filename": result.get("filename"), "category": category}
    if result.get("duplicate"):
        response_data["duplicate"] = True
        response_data["msg"] = "These bytes were already stored for this pit."
    if converted_from and not response_data.get("duplicate"):
        response_data["converted_from"] = converted_from
        response_data["msg"] = f"Converted from {converted_from.upper()} to JPEG (full resolution)."
    return jsonify(response_data)

