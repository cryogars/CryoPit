"""CryoPit — snow pit field data logger.

Run it:
    python -m cryopit                      # waitress (or Flask dev fallback)
    waitress-serve --call cryopit:make_app # any WSGI server, via the factory

Configuration is env-driven (CRYOPIT_*); see CONFIGURATION.md.
"""
from __future__ import annotations

__version__ = "3.7.0rc6"


def make_app():
    """Initialize the database and return the production-configured Flask app."""
    import logging
    import os
    import re
    import uuid
    from pathlib import Path

    from flask import Flask, Response, g, jsonify, request
    from werkzeug.exceptions import HTTPException

    from .auth import current_user
    from .config import (CSRF_TTL_SECONDS, DB_PATH, ENABLE_HSTS, EXPORT_DIR,
                         LOG_LEVEL, MAX_BODY_MB,
                         RATE_LIMIT_EXPORTS_PER_MINUTE,
                         RATE_LIMIT_UPLOADS_PER_MINUTE,
                         RATE_LIMIT_WRITES_PER_MINUTE, SECRET_KEY,
                         validate_config)
    from .db import get_conn, init_db
    from .security import SlidingWindowLimiter, validate_csrf_token
    from .web import bp

    validate_config()
    init_db()

    app = Flask(__name__)
    app.config.update(
        MAX_CONTENT_LENGTH=MAX_BODY_MB * 1024 * 1024,
        SECRET_KEY=SECRET_KEY,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict",
        SESSION_COOKIE_SECURE=ENABLE_HSTS,
    )
    app.logger.setLevel(getattr(logging, LOG_LEVEL))
    app.register_blueprint(bp)
    limiter = SlidingWindowLimiter()
    request_id_re = re.compile(r"^[A-Za-z0-9._-]{8,96}$")
    public_health = {"/healthz", "/readyz"}
    safe_methods = {"GET", "HEAD", "OPTIONS"}

    # Finish journaled attachment operations left by a process crash. A
    # reconciliation error must not make the whole application unavailable;
    # the affected pit remains recoverable through its attachment UI/API.
    try:
        from .attachment_storage import reconcile_all
        reconcile_all(full=False)
    except Exception:
        app.logger.exception("startup attachment reconciliation failed")

    # Download ZIPs are scratch files, not archive products. Normally the WSGI
    # response closes and removes them; this startup sweep covers process kills,
    # host restarts, and power failures where request cleanup never ran.
    try:
        from .download_staging import sweep_staged_downloads
        swept = sweep_staged_downloads()
        if swept:
            app.logger.info("removed %d stale staged download(s)", swept)
    except Exception:
        app.logger.exception("startup download-staging cleanup failed")

    # Inbound upload scratch is likewise non-scientific state. Normal request
    # cleanup removes it; this sweep covers interruption before the handler can
    # reject or atomically adopt the staged bytes.
    try:
        from .upload_staging import sweep_staged_uploads
        swept = sweep_staged_uploads()
        if swept:
            app.logger.info("removed %d stale staged upload(s)", swept)
    except Exception:
        app.logger.exception("startup upload-staging cleanup failed")

    @app.before_request
    def _security_boundary():
        supplied_request_id = request.headers.get("X-Request-ID", "")
        g.request_id = (supplied_request_id if request_id_re.fullmatch(supplied_request_id)
                        else uuid.uuid4().hex)
        if request.path in public_health:
            return None

        user = current_user()  # fail closed in trusted-proxy mode
        g.cryopit_user = user

        if request.path.startswith("/api/"):
            if request.method not in safe_methods and (Path(EXPORT_DIR) / ".cryopit-maintenance").exists():
                from flask import abort
                abort(503, description="CryoPit is temporarily in maintenance mode; retry shortly.")
            fetch_site = request.headers.get("Sec-Fetch-Site")
            if fetch_site and fetch_site not in {"same-origin", "none"}:
                from flask import abort
                abort(403, description="Cross-site API requests are not permitted.")

            if request.method not in safe_methods:
                token = request.headers.get("X-CryoPit-CSRF", "")
                if not validate_csrf_token(token, user, SECRET_KEY,
                                           ttl_seconds=CSRF_TTL_SECONDS):
                    from flask import abort
                    abort(403, description="Missing or invalid CryoPit CSRF token.")

                if request.path.startswith("/api/attach/"):
                    bucket, limit = "upload", RATE_LIMIT_UPLOADS_PER_MINUTE
                elif request.path in {"/api/download", "/api/profile"}:
                    bucket, limit = "export", RATE_LIMIT_EXPORTS_PER_MINUTE
                else:
                    bucket, limit = "write", RATE_LIMIT_WRITES_PER_MINUTE
                allowed, retry_after = limiter.check(f"{user}\0{bucket}", limit)
                if not allowed:
                    g.retry_after = retry_after
                    from flask import abort
                    abort(429, description="Too many requests; retry shortly.")
        return None

    @app.after_request
    def _security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
        )
        # The current no-build UI uses inline scripts, styles, and event
        # handlers. CSP still closes every unneeded source and prevents framing,
        # plugins, external connections, and arbitrary image/font origins.
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; base-uri 'none'; object-src 'none'; "
            "frame-ancestors 'none'; form-action 'self'; connect-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            "img-src 'self' data: blob:",
        )
        response.headers.setdefault("X-Robots-Tag", "noindex, nofollow, noarchive")
        if request.path.startswith("/api/") or request.path in public_health:
            response.headers.setdefault("Cache-Control", "no-store")
        if ENABLE_HSTS:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        response.headers["X-Request-ID"] = getattr(g, "request_id", uuid.uuid4().hex)
        retry_after = getattr(g, "retry_after", None)
        if retry_after:
            response.headers["Retry-After"] = str(retry_after)
        return response

    @app.errorhandler(HTTPException)
    def _http_error(exc):
        description = exc.description if isinstance(exc.description, str) else exc.name
        if request.path.startswith("/api/"):
            response = jsonify({"ok": False, "msg": description,
                                "request_id": getattr(g, "request_id", None)})
            response.status_code = exc.code or 500
            return response
        return Response(f"{exc.code} {exc.name}\n{description}\n",
                        status=exc.code, mimetype="text/plain")

    @app.errorhandler(Exception)
    def _unexpected_error(exc):
        app.logger.exception("unhandled request error request_id=%s path=%s",
                             getattr(g, "request_id", "unknown"), request.path)
        if request.path.startswith("/api/"):
            return jsonify({
                "ok": False,
                "msg": "The request could not be completed. Use the request ID when contacting support.",
                "request_id": getattr(g, "request_id", None),
            }), 500
        return Response("500 Internal Server Error\n", status=500, mimetype="text/plain")

    @app.get("/healthz")
    def healthz():
        return jsonify({"ok": True, "service": "cryopit", "version": __version__})

    @app.get("/readyz")
    def readyz():
        try:
            conn = get_conn()
            try:
                conn.execute("SELECT 1").fetchone()
            finally:
                conn.close()
            export = Path(EXPORT_DIR)
            export.mkdir(parents=True, exist_ok=True)
            if (export / ".cryopit-maintenance").exists():
                return jsonify({"ok": False, "service": "cryopit", "maintenance": True}), 503
            if not os.access(export, os.R_OK | os.W_OK | os.X_OK):
                raise PermissionError("export directory is not readable and writable")
        except Exception:
            app.logger.exception("readiness check failed")
            return jsonify({"ok": False, "service": "cryopit"}), 503
        return jsonify({"ok": True, "service": "cryopit"})

    return app


create_app = make_app
