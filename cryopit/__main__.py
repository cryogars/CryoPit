"""`python -m cryopit` — start the server."""
import errno
import os
import socket

from . import __version__, make_app
from .config import (AUTH_HEADER, CAMPAIGN, DB_PATH, DEV_USER, ENABLE_EDIT,
                     ENABLE_HSTS, EXPORT_DIR, HEIC_CONCURRENCY, HOST, PORT,
                     PROFILE_CONCURRENCY, RESEARCH_GROUP, THREADS,
                     TRUST_PROXY_AUTH)


def _preflight_bind():
    """Try binding HOST:PORT before handing off to the real server, so a
    port conflict produces one clear CryoPit message on BOTH serving paths.
    (Flask's dev server prints its own generic text and exits internally,
    which would bypass the OSError handler below.) Any error other than
    in-use/permission is ignored here and left to the real server to report.
    """
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind((HOST, PORT))
    except OSError as e:
        if e.errno == errno.EADDRINUSE:
            raise SystemExit(
                f"\nPort {PORT} is already in use — is another CryoPit instance "
                f"running?\nEither stop it (Ctrl-C in its terminal) or launch "
                f"this one on a different port, e.g.:\n"
                f"  CRYOPIT_PORT={PORT + 1} python -m cryopit"
            )
        if e.errno == errno.EACCES and PORT < 1024:
            raise SystemExit(
                f"\nPermission denied binding port {PORT} — ports below 1024 "
                f"need administrator rights.\nUse an unprivileged port instead, "
                f"e.g.:\n  CRYOPIT_PORT=8502 python -m cryopit"
            )
    finally:
        probe.close()


def main():
    app = make_app()
    _preflight_bind()
    print(f"CryoPit v{__version__} · {RESEARCH_GROUP} · campaign {CAMPAIGN}")
    print(f"  database : {os.path.abspath(DB_PATH)}")
    print(f"  exports  : {os.path.abspath(EXPORT_DIR)}")
    print(f"  edit     : {'enabled' if ENABLE_EDIT else 'disabled'}")
    if TRUST_PROXY_AUTH:
        print(f"  auth     : trusted proxy header {AUTH_HEADER}")
    else:
        print(f"  auth     : shared local identity {DEV_USER!r}")
        if HOST not in {"127.0.0.1", "localhost", "::1"}:
            print("  WARNING  : authentication is disabled on a non-loopback address;")
            print("             every visitor is the same owner and can access the same pits.")
    if ENABLE_HSTS:
        print("  HTTPS    : HSTS enabled; expose CryoPit only through HTTPS")
    print(f"  serving  : http://{HOST}:{PORT}")
    print(
        f"  workers  : {THREADS} HTTP threads · "
        f"HEIC {HEIC_CONCURRENCY} · profiles {PROFILE_CONCURRENCY}"
    )
    try:
        try:
            from waitress import serve
        except ImportError:
            serve = None
        if serve is not None:
            serve(app, host=HOST, port=PORT, threads=THREADS)
        else:
            print("  (waitress not installed — falling back to Flask's dev server;"
                  " pip install waitress for field use)")
            app.run(host=HOST, port=PORT)
    except OSError as e:
        # Bind failures get a human answer instead of a traceback.
        if e.errno == errno.EADDRINUSE:
            raise SystemExit(
                f"\nPort {PORT} is already in use — is another CryoPit instance "
                f"running?\nEither stop it (Ctrl-C in its terminal) or launch "
                f"this one on a different port, e.g.:\n"
                f"  CRYOPIT_PORT={PORT + 1} python -m cryopit"
            )
        if e.errno == errno.EACCES and PORT < 1024:
            raise SystemExit(
                f"\nPermission denied binding port {PORT} — ports below 1024 "
                f"need administrator rights.\nUse an unprivileged port instead, "
                f"e.g.:\n  CRYOPIT_PORT=8502 python -m cryopit"
            )
        raise


if __name__ == "__main__":
    main()
