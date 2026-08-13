# Deploying CryoPit

How to run CryoPit beyond a personal laptop. For plain local use, the README covers everything. Production and release-candidate builds should install `requirements.lock`, not resolve broad dependency ranges at deployment time.

## 1. Docker

```bash
docker build -t cryopit .
```

**Default — one visible data folder (bind mount).** Best for a personal or
field-team machine: the database and exports live in an ordinary folder you
can browse and back up.

```bash
docker run -d --name cryopit -p 8502:8502 \
  -v "$HOME/cryopit-data:/data" cryopit
# Windows (PowerShell):  -v "$env:USERPROFILE\cryopit-data:/data"
```

**Shared server — split storage.** The database lives in a Docker-managed
named volume (safe from sync clients, accidental deletion, and permission
drift); the export tree — CSVs, figures, uploaded photos, recovery journals,
and lifecycle locks — is bind-mounted somewhere operators can inspect:

```bash
docker run -d --name cryopit -p 8502:8502 \
  -v cryopit-db:/data \
  -v /srv/snow/exports:/data/exports \
  cryopit
```

Named volumes live inside Docker's storage area (on Docker Desktop, inside
its VM), which is exactly why they suit the database and don't suit exports.

**Rules that always apply:** `/data` must be real local disk — never a
Drive/Dropbox-synced folder or a network filesystem (WAL-mode SQLite, see the
README warning). On Linux hosts, a bind-mounted folder must be writable by
the container's unprivileged user (UID of `cryopit`, typically 1000):
`sudo chown -R 1000 ~/cryopit-data` if you hit permission errors.

**Configuration** via `-e` flags or an env file:

```bash
docker run -d -p 8502:8502 -v "$HOME/cryopit-data:/data" \
  --env-file cryopit.env cryopit
```

**Updating** to a new image build without losing data:

```bash
docker build -t cryopit . && docker stop cryopit && docker rm cryopit
# then re-run the same `docker run` — data lives in the mount, not the container
```

## 2. LAN deployment without Docker

On the host that will serve the team:

```bash
pip install -r requirements.lock
CRYOPIT_HOST=0.0.0.0 CRYOPIT_DB_PATH=/var/lib/cryopit/cryopit.db \
CRYOPIT_EXPORT_DIR=/srv/snow/exports python -m cryopit
```

Leave `CRYOPIT_ENABLE_EDIT` **off** on a shared instance without
authentication — otherwise everyone on the network shares the same development owner and can load or update the same saved pits.

A minimal systemd unit (`/etc/systemd/system/cryopit.service`):

```ini
[Unit]
Description=CryoPit snow pit logger
After=network.target

[Service]
User=cryopit
WorkingDirectory=/opt/cryopit
Environment=CRYOPIT_HOST=0.0.0.0
Environment=CRYOPIT_DB_PATH=/var/lib/cryopit/cryopit.db
Environment=CRYOPIT_EXPORT_DIR=/srv/snow/exports
ExecStart=/usr/bin/python3 -m cryopit
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now cryopit
```

## 3. Multi-user with institutional SSO

CryoPit never handles credentials. Put an authenticating reverse proxy in
front, have it inject the username header, and only then set
`CRYOPIT_TRUST_PROXY_AUTH=true`. Two hard requirements:

1. the proxy is the **only** network path to the app (bind CryoPit to
   127.0.0.1 or a private interface), and
2. the proxy **strips any client-supplied copy** of the header before
   injecting its own — otherwise anyone can impersonate anyone.

nginx sketch (behind your SSO module of choice — oauth2-proxy, Authelia,
Shibboleth, etc.):

```nginx
server {
    listen 443 ssl;
    server_name cryopit.example.edu;
    # ... ssl certs, auth_request / SSO wiring ...

    location / {
        proxy_set_header X-Remote-User "";          # strip client copies
        proxy_set_header X-Remote-User $authenticated_user;
        proxy_pass http://127.0.0.1:8502;
    }
}
```

Caddy sketch:

```caddy
cryopit.example.edu {
    forward_auth sso:4180 { ... }
    reverse_proxy 127.0.0.1:8502 {
        header_up -X-Remote-User
        header_up X-Remote-User {http.auth.user.id}
    }
}
```

Set `CRYOPIT_TRUST_PROXY_AUTH=true`, provide a stable random `CRYOPIT_SECRET_KEY` of at least 32 characters, and keep the CryoPit listener private. With those controls, each verified identity sees and edits only its own pits. Missing identity fails closed. Use an immutable institutional subject rather than a display name or mutable email.

A fuller Nginx example is provided in `deploy/nginx-auth-request.conf`, and the acceptance boundary is documented in `docs/SECURITY.md`. The first-generation product intentionally stops at owner-scoped access; supervisor roles, campaign memberships, and cross-owner UI access remain future work.

### Worker-process and export-filesystem requirement

Archive, re-archive, attachment, reconciliation, and backup operations share one
storage lifecycle lock. Waitress's threads are one process and are safe. POSIX
local filesystems also receive cross-process exclusion through `flock`. If the
application logs that process locking is unavailable (notably on Windows or
some network filesystems), run only one CryoPit application process against
that database/export pair. Gunicorn-style multiple workers are unsupported in
that degraded mode.

CryoPit flushes completed files and directory renames when the platform permits.
A warning about unavailable `fsync` means operations remain journaled and
recoverable, but power-loss durability is weaker. Prefer local server storage
and send verified backup bundles to network or institutional archival storage.

### HTTP concurrency on a shared server

Start with `CRYOPIT_THREADS=8`. CryoPit separately bounds HEIC conversion
(default 1) and profile rendering (default 2), so lowering the whole HTTP pool
is not the preferred way to control those memory-intensive operations. At the
default limits, three heavy requests can be actively working at once; an
eight-thread server still has five request slots available for routine work,
whereas a four-thread server has only one. Requests waiting for a heavy-path
semaphore still occupy a Waitress thread, so treat 8 as a tested starting
configuration rather than a guarantee. Stage 6 load/soak testing on the actual
host should determine whether the deployment should stay at 8 or move higher
or lower.

CryoPit prints the configured HTTP, HEIC, and profile concurrency values at
startup so operators can confirm the effective settings from service logs.

## 4. Hosted testing (PythonAnywhere)

The fastest way to hand teammates a URL. Free tier: always-on HTTPS at
`yourname.pythonanywhere.com`; click "Run until 3 months from today" when
reminded.

1. Upload `cryopit.zip`, then in a Bash console: `unzip cryopit.zip -d cryopit`
   and `pip install --user -r cryopit/requirements.lock`.
2. Web tab → Add a new web app → **Manual configuration** (your Python 3.x).
3. Edit the WSGI file to exactly:

```python
import os, sys
sys.path.insert(0, "/home/YOURNAME/cryopit")

os.environ["CRYOPIT_DB_PATH"] = "/home/YOURNAME/cryopit-data/cryopit.db"
os.environ["CRYOPIT_EXPORT_DIR"] = "/home/YOURNAME/cryopit-data/exports"
os.environ["CRYOPIT_SQLITE_JOURNAL"] = "DELETE"   # network-backed disk: not WAL

from cryopit import make_app
application = make_app()
```

4. Reload the web app.

Why `DELETE`: PythonAnywhere's storage is network-backed, which is exactly
where WAL's shared-memory coordination is unreliable (CONFIGURATION.md has
the full journal-mode table). Also note everyone shares one identity without
SSO (fine for group testing — no privacy between testers), and the free
tier's 512 MB disk is plenty for CSVs but finite once photos accumulate.

## 5. Backups in production

Use CryoPit's consistency-aware bundle command rather than copying a live WAL database:

```bash
python -m cryopit.ops backup --output /backups/cryopit-$(date +%F-%H%M).zip
python -m cryopit.ops verify /backups/cryopit-2026-08-05-0130.zip
```

The bundle contains the SQLite database and complete export tree with a checksummed manifest. The maintenance marker temporarily rejects writes, `/readyz` returns `503`, and backup waits on the same lifecycle lock as archive and attachment publication. Restore procedures and rollback copies are documented in `docs/BACKUP_RESTORE.md`.

## 6. Server resource qualification

Before fixing RAM or worker settings for a shared deployment, install the locked
dependencies and run the Stage 6 qualification harness on the actual host:

```bash
pip install -r requirements.lock
python tests/benchmark_resource_stage6.py --qualification --output stage6.json
```

For the campaign-style stability check, add a multi-hour soak:

```bash
python tests/benchmark_resource_stage6.py --qualification --soak-minutes 180 --output stage6-soak.json
```

The qualification load runs two complex profile renders, one high-resolution
image conversion, a large disk-backed download build, an attachment upload, and
routine database/export work together under an eight-worker pool. The JSON
report records peak/current RSS, routine-job latency, staging-disk high-water
marks, file-descriptor use, and leftover scratch files. Confirm that
`heic_mode` reports `real HEIC` on the deployment host before using the result
for final RAM sizing.

The normal regression suite also includes a live Waitress check that starts the
real eight-thread service, verifies startup cleanup, launches two profile
requests concurrently, and confirms health traffic still completes. Run the
full suite before production:

```bash
./tests/run_all.sh
```

Stage 6 measurements are host-specific. Do not treat measurements from a
development laptop or CI runner as the final RAM requirement for the
institutional VM.

### Stage 7 sizing decision

The current resource-hardening evidence supports **3.5 GiB as a reasonable
initial allocation** for one CryoPit application process with the documented
starting settings (`CRYOPIT_THREADS=8`, `CRYOPIT_HEIC_CONCURRENCY=1`, and
`CRYOPIT_PROFILE_CONCURRENCY=2`). The packaged mixed-load qualification peaked
near 1.7 GiB RSS in the available test container, so there is no present
evidence that justifies requesting more RAM before the target-host test.

That is an initial deployment decision, not a universal CryoPit minimum. Before
the winter field season, run the qualification plus the 180-minute soak on the
actual VM with the locked dependencies and real HEIC support, then evaluate it
against the 3.5 GiB policy:

```bash
python tests/benchmark_resource_stage6.py --qualification \
  --soak-minutes 180 --output stage6-soak.json
python tests/evaluate_resource_stage7.py stage6-soak.json \
  --ram-gib 3.5 --output stage7-sizing.json
```

`PASS` means the target allocation was demonstrated under the Stage 7 policy.
`PROVISIONAL` means the result is encouraging but was not measured under all
required conditions, such as real HEIC, a target-sized host, or the full soak.
`FAIL` means the measured workload exceeded one or more deployment guardrails
and RAM/concurrency should be revisited before production.

The Stage 7 policy reserves substantial headroom rather than sizing to the
observed peak: CryoPit RSS must remain at or below 70% of the planned RAM,
minimum host `MemAvailable` must remain at least 15% of planned RAM or 512 MiB
(whichever is larger), swap growth must stay within 64 MiB, routine p95 latency
must remain below 1 second, scratch files must clean up, and the 180-minute soak
must not show material post-warm-up RSS growth.

## 7. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| "Port 8502 is already in use…" on start | Another CryoPit (or other app) holds the port. Stop it, or `CRYOPIT_PORT=8503`. |
| `database is locked` errors, corrupted DB | The DB sits on a synced/network folder. Move it to local disk (WAL warning in the README). |
| Permission denied writing /data (Docker, Linux host) | Bind-mounted folder not writable by container UID: `sudo chown -R 1000 <folder>`. |
| App reachable on the host but not from other machines | Bound to 127.0.0.1. Set `CRYOPIT_HOST=0.0.0.0` (deliberately — read §2/§3 first). |
| Everyone's pits appear under one user | No SSO in front / trust flag off — that's the intended shared-dev-user mode. See §3. |
| Uploads rejected | Check `CRYOPIT_ATTACHMENT_MAX_MB` and `CRYOPIT_MAX_BODY_MB`. Current category limits are 3 sheet images (or one PDF), 6 pit-wall photos, 20 per stratigraphy layer, and 150 total; JPEG/PNG/WebP/HEIC/PDF are signature-checked. |
| Cross-process storage locking unavailable | Use one CryoPit application process for this database/export pair, or move the export tree to a verified POSIX local filesystem. Waitress threads are safe because they share one process. |

## 8. Health, readiness, and release checks

`GET /healthz` reports process liveness. `GET /readyz` checks SQLite and the export directory and returns `503` during maintenance or storage failure. Neither endpoint exposes user or pit data.

Before institutional release, complete `docs/RELEASE_CHECKLIST.md`, run `tests/run_all.sh` with Flask and jsdom installed, and perform a backup/restore drill. Unexpected server errors return a request ID; retain that ID in support reports and correlate it with application logs.

## Importing records from field laptops

Stage 14 uses one-way transfer bundles rather than attached SQLite databases or
row copying. On each field laptop, resolve **Needs recovery**, allow available
photo uploads to finish, and create a bundle outside the configured export root:

```bash
python -m cryopit.transfer export --output /transfer/field-day.zip
```

Move the ZIP through an institutionally controlled channel. On the central
host, create and verify a backup, then classify the bundle for the stable SSO
identity that should own its pits:

```bash
python -m cryopit.transfer import /transfer/field-day.zip \
  --owner 00u81abc123 --dry-run --report /transfer/field-day-plan.json
```

Review every `conflict`, then apply the safe entries:

```bash
python -m cryopit.transfer import /transfer/field-day.zip \
  --owner 00u81abc123 --report /transfer/field-day-result.json
```

A real import temporarily places CryoPit in maintenance mode, makes `/readyz`
return `503`, rejects new API writes, and holds the shared storage lock. In a
load-balanced deployment, drain the node before import and ensure all workers
sharing the database/export pair honor the same maintenance marker. Retain the
original ZIP and result report for audit. Full operating details and conflict
rules are in [docs/MERGING.md](docs/MERGING.md).
