# CryoPit — Deployment Notes

CryoPit runs in three modes depending on who's using it and where. Pick the one that matches your situation.

---

<!-- ## 1. Local use (one person, their own laptop) — simplest

No Docker, no server setup. Just Python.

```
pip install -r requirements.txt
python cryopit.py
```

Open http://127.0.0.1:8502

- Uses waitress automatically if installed (it's in requirements.txt); falls back
  to Flask's dev server if not.
- Binds to 127.0.0.1 (localhost only) — nobody else on the network can reach it.
- The database (`cryopit.db`) and archive folder (`exports/`) are created in the
  directory you launch from.

**Individuals do not need Docker.** Docker is for the shared/server deployment
below. Running the Python directly is the right path for a single user.

### Port already in use?
If port 8502 is occupied (almost always a previous CryoPit you didn't fully
close), the app won't start and will say so clearly — no silent failure. Either:
- stop the old instance (Ctrl-C in its terminal), or
- use another port: `CRYOPIT_PORT=8503 python cryopit.py`

Stopping CryoPit with **Ctrl-C** (rather than closing the terminal) releases the
port cleanly, so you'll rarely hit this.

---

## 2. Shared server (multiple people over a URL) — with Docker

This is the path to hand to IT. The Dockerfile packages CryoPit with its exact
Python version and dependencies, so it runs identically on any host with Docker.

```
docker build -t cryopit .
docker run -p 8502:8502 \
           -v cryopit_data:/data \
           -e CRYOPIT_HOST=0.0.0.0 \
           cryopit
```

- `-v cryopit_data:/data` mounts a persistent volume for the database and
  archives, so **data survives container restarts**. (In-container defaults
  already point CRYOPIT_DB_PATH and CRYOPIT_EXPORT_DIR at `/data`.)
- `CRYOPIT_HOST=0.0.0.0` makes it reachable from outside the container.

### What IT still needs to add (not CryoPit's job)
CryoPit deliberately does **not** handle HTTPS or authentication itself. For a
real shared deployment, IT puts it behind a reverse proxy that provides:
- **HTTPS** (a TLS certificate; the proxy terminates it).
- **Authentication** — ideally institutional SSO. The proxy authenticates the
  user and injects their username in a request header (default `X-Remote-User`),
  which CryoPit reads. CryoPit never sees passwords.
- A clean URL on the institution's domain.

With per-user auth in place, set `CRYOPIT_ENABLE_EDIT=true` to let people load and
edit their **own** pits safely (ownership scopes each user to their own data).
Without auth, leave editing disabled (`CRYOPIT_ENABLE_EDIT=false`) so the shared
instance is capture-and-archive only.

---

## 3. Quick demo over a URL (before talking to IT) — PythonAnywhere

To show a working, clickable CryoPit before the IT conversation, a free
PythonAnywhere account works. It supplies its own WSGI server, so you don't run
waitress there — you point its WSGI config at CryoPit's `make_app()`.

Rough steps:
1. Upload `cryopit.py` and `requirements.txt`.
2. Create a web app (Manual config, Python 3.11), install requirements in its
   virtualenv.
3. In the WSGI config file, set the application:
   ```python
   from cryopit import make_app
   application = make_app()
   ```
4. Reload the web app; it's live at your-username.pythonanywhere.com.

**Important — demo with sample data only.** A demo has no institutional auth in
front of it, so do **not** put real field data there. Use throwaway/fake pits to
demonstrate the workflow. Treat it as a disposable showcase, not a real instance.

PythonAnywhere's filesystem is networked, which can make SQLite locking cranky
under heavy concurrency — fine for a demo, but another reason it's not a
production deployment.

---

## Configuration reference (environment variables)

| Variable                  | Default                  | Purpose                                        |
|---------------------------|--------------------------|------------------------------------------------|
| `CRYOPIT_HOST`            | `127.0.0.1`              | Bind address; set `0.0.0.0` to accept network  |
| `CRYOPIT_PORT`            | `8502`                   | Port to serve on                               |
| `CRYOPIT_DB_PATH`         | `cryopit.db`             | SQLite database file                           |
| `CRYOPIT_EXPORT_DIR`      | `exports`                | Folder Archive writes CSVs to                  |
| `CRYOPIT_ENABLE_EDIT`     | `true`                   | Saved-pits sidebar + load-for-edit on/off      |
| `CRYOPIT_SAVED_PITS_LIMIT`| `10`                     | How many recent pits the sidebar shows         |
| `CRYOPIT_AUTH_HEADER`     | `X-Remote-User`          | Header the proxy injects with the username     |
| `CRYOPIT_DEV_USER`        | `local`                  | Owner used when no auth header is present       |
| `CRYOPIT_THREADS`         | `8`                      | waitress worker threads                        |
| `CRYOPIT_CAMPAIGN`        | `SNEX25`                 | Default campaign name                          |
| `CRYOPIT_EXPORT_DIR` and `CRYOPIT_DB_PATH` should be absolute paths (or a mounted volume) in any server/Docker deployment, so data doesn't land in an unexpected working directory. |
 -->
