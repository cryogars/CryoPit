# CryoPit — snow pit field data logger
#
#   docker build -t cryopit .
#
# DEFAULT (personal / field laptop) — bind-mount an ordinary folder so the
# database and exports are visible in your file browser and your backup
# scripts work on them directly:
#
#   docker run -p 8502:8502 -v "$HOME/cryopit-data:/data" cryopit
#   (Windows PowerShell:  -v "$env:USERPROFILE\cryopit-data:/data")
#
# SHARED SERVER variant — keep the DATABASE in a Docker-managed named volume
# (protected from sync clients and accidental deletion) and bind-mount only
# the exports where people browse them:
#
#   docker run -p 8502:8502 \
#     -v cryopit-db:/data \
#     -v /srv/snow/exports:/data/exports \
#     cryopit
#
# Either way, /data must be REAL LOCAL DISK (see the WAL warning in the
# README): never a Drive/Dropbox-synced folder or network filesystem. All
# CRYOPIT_* settings can be overridden with -e flags or an --env-file. Full
# walkthrough: DEPLOYMENT.md.

FROM python:3.12-slim

# Run as an unprivileged user; /data is the only writable state.
RUN useradd --create-home --shell /usr/sbin/nologin cryopit \
    && mkdir -p /data/exports \
    && chown -R cryopit:cryopit /data

WORKDIR /app

COPY requirements.txt requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock

COPY cryopit/ ./cryopit/

# Inside a container the app must bind 0.0.0.0 to be reachable through the
# published port; the container boundary is what limits exposure. Identity
# stays off by default — set CRYOPIT_TRUST_PROXY_AUTH=true only when an
# authenticating reverse proxy fronts this container (see README).
ENV CRYOPIT_HOST=0.0.0.0 \
    CRYOPIT_PORT=8502 \
    CRYOPIT_DB_PATH=/data/cryopit.db \
    CRYOPIT_EXPORT_DIR=/data/exports

USER cryopit
EXPOSE 8502
VOLUME /data

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8502/readyz', timeout=3).read()" || exit 1

CMD ["python", "-m", "cryopit"]
