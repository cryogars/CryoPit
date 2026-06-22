# CryoPit container image
# ---------------------------------------------------------------------------
# Build:  docker build -t cryopit .
# Run:    docker run -p 8502:8502 \
#                    -v cryopit_data:/data \
#                    -e CRYOPIT_DB_PATH=/data/cryopit.db \
#                    -e CRYOPIT_EXPORT_DIR=/data/exports \
#                    -e CRYOPIT_HOST=0.0.0.0 \
#                    cryopit
# Then open http://localhost:8502
#
# WHY THESE CHOICES:
#  * python:3.11-slim — small Debian-based Linux image with Python 3.11. Docker
#    containers are Linux; this supplies Ubuntu/Debian-family userland while the
#    kernel is shared from the host (a background Linux VM on Windows/Mac).
#  * A named volume mounted at /data holds the SQLite DB and the archive folder,
#    so data PERSISTS across container restarts. Without this, anything written
#    inside the container vanishes when it stops — the DB and archives must live
#    on a mounted volume, never the container's ephemeral filesystem.
#  * CRYOPIT_HOST=0.0.0.0 so the app is reachable from outside the container
#    (the port mapping -p exposes it). Inside a container, 127.0.0.1 would only
#    be reachable from within the container itself.
#  * Runs as a non-root user for safety.
# ---------------------------------------------------------------------------

FROM python:3.11-slim

# Don't write .pyc files; flush stdout/stderr immediately (better container logs)
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies first (layer caching: deps rarely change, code changes
# often; keeping them separate means rebuilds don't re-install packages).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application
COPY cryopit.py .

# Data directory for the SQLite DB and CSV archive (mount a volume here).
RUN mkdir -p /data

# Sensible in-container defaults (override with -e at run time).
ENV CRYOPIT_DB_PATH=/data/cryopit.db \
    CRYOPIT_EXPORT_DIR=/data/exports \
    CRYOPIT_HOST=0.0.0.0 \
    CRYOPIT_PORT=8502

# Run as a non-root user, and give it ownership of the app + data dirs.
RUN useradd --create-home --uid 10001 cryo && chown -R cryo /app /data
USER cryo

EXPOSE 8502

# make_app() initializes the DB and returns the WSGI app; waitress serves it.
CMD ["waitress-serve", "--host=0.0.0.0", "--port=8502", "--call", "cryopit:make_app"]
