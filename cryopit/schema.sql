-- ===========================================================================
-- CryoPit database schema
--
-- SQLite, applied idempotently at startup (every statement is IF NOT EXISTS /
-- OR IGNORE). Additive column migrations for older dev databases live in
-- cryopit/db.py:_migrate(), not here — this file is the canonical shape of a
-- fresh database.
--
-- Design: normalized tables for querying + the exact raw JSON payload per pit
-- (sites.raw_json) so the form round-trips a saved pit with zero lossy
-- reconstruction.
-- ===========================================================================

-- Field campaigns (restored from the original design): pits reference a
-- campaign row instead of repeating a text code. Only `name` is written by
-- the app today (get-or-create at save); description/dates/location are for
-- curation by hand or future UI.
CREATE TABLE IF NOT EXISTS campaigns (
    campaign_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    description TEXT, start_date TEXT, end_date TEXT, location TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

-- One row per snow pit. The hub everything else hangs off.
CREATE TABLE IF NOT EXISTS sites (
    site_id TEXT PRIMARY KEY,
    pit_id TEXT NOT NULL,
    owner TEXT NOT NULL,
    raw_json TEXT,
    current_revision_id TEXT,
    campaign_id INTEGER REFERENCES campaigns(campaign_id),
    export_folder TEXT,
    pending_export_folder TEXT,
    location TEXT, site TEXT,
    date TEXT, pit_open_time TEXT,
    temp_time_start TEXT, temp_time_end TEXT,
    total_depth_cm REAL,
    utm_easting REAL, utm_northing REAL, utm_zone_number INTEGER, utm_zone_letter TEXT,
    latitude REAL, longitude REAL, coord_source TEXT,
    elevation_m REAL, slope_angle_deg REAL,
    recorded_by TEXT, surveyors TEXT,
    gps_device TEXT, gps_uncertainty_m REAL,
    precip_rate TEXT, precip_type TEXT, sky_condition TEXT, wind TEXT,
    ground_condition TEXT, ground_roughness TEXT, tree_canopy TEXT,
    snow_cover_condition TEXT, standing_water TEXT,
    vegetation TEXT, veg_height_cm REAL,
    swe_melt_evidence TEXT,
    density_cutter TEXT, flags TEXT, comments TEXT,
    comment_weather TEXT, comment_pit TEXT, comment_hardness TEXT, comment_misc TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    UNIQUE(owner, pit_id)
);


-- Persistent installation identity and append-only pit revision history.
-- installation_id is generated once per database and travels in transfer
-- bundles so an audit can distinguish independently operating field laptops.
CREATE TABLE IF NOT EXISTS app_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS site_revisions (
    revision_id TEXT PRIMARY KEY,
    site_id TEXT NOT NULL REFERENCES sites(site_id) ON DELETE CASCADE,
    parent_revision_id TEXT REFERENCES site_revisions(revision_id),
    revision_number INTEGER NOT NULL,
    payload_version INTEGER NOT NULL DEFAULT 1,
    record_hash TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    source_installation_id TEXT NOT NULL,
    source_owner TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    imported_at TEXT,
    import_bundle_id TEXT,
    UNIQUE(site_id, revision_number)
);
CREATE INDEX IF NOT EXISTS idx_site_revisions_site_number
    ON site_revisions(site_id, revision_number);
CREATE INDEX IF NOT EXISTS idx_site_revisions_parent
    ON site_revisions(parent_revision_id);
CREATE INDEX IF NOT EXISTS idx_sites_current_revision
    ON sites(current_revision_id);

-- Audit trail for one-way field transfer bundles. Dry-runs are returned to the
-- operator but are not persisted; actual imports record one row plus one result
-- row per pit so a bundle can be safely explained and repeated.
CREATE TABLE IF NOT EXISTS transfer_imports (
    import_id TEXT PRIMARY KEY,
    bundle_id TEXT NOT NULL,
    source_installation_id TEXT NOT NULL,
    destination_owner TEXT NOT NULL,
    bundle_sha256 TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('running','complete','partial','failed')),
    summary_json TEXT,
    started_at TEXT DEFAULT (datetime('now')),
    completed_at TEXT,
    UNIQUE(bundle_id, destination_owner)
);

CREATE TABLE IF NOT EXISTS transfer_import_items (
    import_id TEXT NOT NULL REFERENCES transfer_imports(import_id) ON DELETE CASCADE,
    site_id TEXT NOT NULL,
    incoming_revision_id TEXT,
    result TEXT NOT NULL,
    message TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (import_id, site_id)
);

-- People, deduplicated by name across pits.
CREATE TABLE IF NOT EXISTS observers (
    observer_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    email TEXT, institution TEXT   -- curated by hand; the form only writes name
);

-- Who worked which pit, in what role ('recorder' | 'surveyor').
CREATE TABLE IF NOT EXISTS site_observers (
    site_id TEXT NOT NULL REFERENCES sites(site_id) ON DELETE CASCADE,
    observer_id INTEGER NOT NULL REFERENCES observers(observer_id),
    role TEXT DEFAULT 'surveyor',
    PRIMARY KEY (site_id, observer_id, role)
);

-- Devices / surveys / documentation items, deduplicated by name.
CREATE TABLE IF NOT EXISTS instruments (
    instrument_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    model TEXT
);

-- Per-pit checklist: used is Y, N, or NULL when unanswered; serial only for Y.
CREATE TABLE IF NOT EXISTS site_instruments (
    site_id TEXT NOT NULL REFERENCES sites(site_id) ON DELETE CASCADE,
    instrument_id INTEGER NOT NULL REFERENCES instruments(instrument_id),
    serial_number TEXT,
    used TEXT CHECK (used IN ('Y','N')),
    PRIMARY KEY (site_id, instrument_id)
);

-- Every measurement row of every kind, one table. `kind` discriminates which
-- columns are meaningful:
--   temperature  -> height_cm, value_a (deg C), time_recorded
--   density      -> top_cm, bottom_cm, value_a/b/c (kg/m3)
--   lwc          -> top_cm, bottom_cm, value_a/b (permittivity), instrument_id
--   stratigraphy -> top_cm, bottom_cm, grain_*, hand_hardness, manual_wetness
--   ssa          -> height_cm, signal_v, reflectance_pct, ssa_m2kg, grain_type,
--                   instrument_id
CREATE TABLE IF NOT EXISTS layers (
    layer_id INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id TEXT NOT NULL REFERENCES sites(site_id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('temperature','density','lwc','stratigraphy','ssa')),
    top_cm REAL, bottom_cm REAL, height_cm REAL,
    value_a REAL, value_b REAL, value_c REAL,
    grain_size_min_mm REAL, grain_size_max_mm REAL, grain_size_avg_mm REAL,
    grain_type TEXT, hand_hardness TEXT, manual_wetness TEXT,
    signal_v REAL, reflectance_pct REAL, ssa_m2kg REAL,
    layer_density_kgm3 REAL,   -- stratigraphy rows only: optional per-layer density
    time_recorded TEXT, comments TEXT,
    instrument_id INTEGER REFERENCES instruments(instrument_id)
);

-- Spectralon calibration series for the SSA device, one row per level.
-- instrument_id is nullable ON PURPOSE: the app requires an instrument when
-- calibration values are entered, but the schema never fabricates one — a
-- row must not claim "IceCube" when the crew recorded no device.
CREATE TABLE IF NOT EXISTS ssa_calibration (
    calib_id INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id TEXT NOT NULL REFERENCES sites(site_id) ON DELETE CASCADE,
    instrument_id INTEGER REFERENCES instruments(instrument_id),
    operator TEXT,
    spectralon_level REAL, calib_value_v REAL,
    measured_at TEXT, notes TEXT
);

-- Uploaded field documents (pit sheet scans, pit-wall and stratigraphy
-- photos). FILES LIVE ON DISK in the pit's export folder (uploads/); the
-- database stores metadata + a sha256 for integrity only — never blobs.
CREATE TABLE IF NOT EXISTS attachments (
    attachment_id INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id TEXT NOT NULL REFERENCES sites(site_id) ON DELETE CASCADE,
    category TEXT NOT NULL CHECK (category IN ('sheet','pitwall','stratigraphy')),
    filename TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    -- What the photograph SHOWS, as a depth interval in cm below the surface.
    -- Deliberately NOT a layer_id: layers are deleted and rebuilt from
    -- raw_json on every archive, so their ids are reassigned each time (and
    -- the counter is global across pits, so an id means nothing after a
    -- merge). A depth is a fact about the snowpack — it survives re-archiving,
    -- and it survives someone splitting one layer into two, because the photo
    -- still lands in whichever layer now contains it.
    --   62.0 / 45.0  -> a photo of that interval
    --   45.0 / NULL  -> a photo at a single depth
    --   NULL / NULL  -> a general shot, not tied to any layer
    top_cm REAL,
    bottom_cm REAL,
    uploaded_at TEXT DEFAULT (datetime('now')),
    storage_status TEXT DEFAULT 'stored',
    storage_error TEXT,
    pending_delete INTEGER NOT NULL DEFAULT 0,
    trash_relpath TEXT
);
CREATE INDEX IF NOT EXISTS idx_attachments_pit ON attachments(site_id);

-- Browser-side photographs are durable outbox items before they reach the
-- server.  The archive request registers that intent here, so an archived pit
-- can say "three photographs are still expected" even if the browser later
-- disappears. queue_id is generated client-side and is the idempotency key for
-- retries: one queue item can produce at most one stored attachment.
CREATE TABLE IF NOT EXISTS attachment_uploads (
    queue_id TEXT PRIMARY KEY,
    site_id TEXT NOT NULL REFERENCES sites(site_id) ON DELETE CASCADE,
    category TEXT NOT NULL CHECK (category IN ('sheet','pitwall','stratigraphy')),
    original_filename TEXT NOT NULL,
    mime_type TEXT,
    size_bytes INTEGER,
    client_sha256 TEXT,
    top_cm REAL,
    bottom_cm REAL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','stored','cancelled')),
    attachment_id INTEGER REFERENCES attachments(attachment_id) ON DELETE SET NULL,
    last_error TEXT,
    publication_state TEXT,
    staged_relpath TEXT,
    target_relpath TEXT,
    server_sha256 TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_attachment_uploads_site_status
    ON attachment_uploads(site_id, status);
CREATE INDEX IF NOT EXISTS idx_attachment_uploads_attachment
    ON attachment_uploads(attachment_id) WHERE attachment_id IS NOT NULL;

-- The IDENTITY of an attachment is (which pit, which category, which bytes) —
-- never attachment_id, which is a local autoincrement and means nothing once
-- a field laptop's database is merged into the master.
--
-- Making that identity UNIQUE does two jobs with one constraint:
--   1. locally, re-uploading the same photograph is a no-op instead of
--      consuming another slot (six uploads of one photo used to exhaust the
--      whole pit-wall quota, since sha256 was recorded but never consulted);
--   2. on merge, INSERT OR IGNORE becomes correct by construction, so syncing
--      a laptop twice — or syncing two laptops that both hold a copy of the
--      same sheet scan — converges instead of duplicating.
--
-- idx_attachments_identity (UNIQUE on site_id, category, sha256 and layer
-- interval) is created in
-- db.py:_migrate() rather than here, for the same reason as
-- idx_sites_campaign_date: this script runs BEFORE the migration step, and an
-- existing database may still hold the duplicates the index forbids. The
-- migration collapses them first, then creates the index.

-- Interval board SWE measurements: up to three samples (A/B/C) per pit,
-- each with depth, SWE, and density — mirrors the paper sheet block.
CREATE TABLE IF NOT EXISTS swe_samples (
    site_id TEXT NOT NULL REFERENCES sites(site_id) ON DELETE CASCADE,
    sample TEXT NOT NULL CHECK (sample IN ('A','B','C')),
    depth_cm REAL, swe_mm REAL, density_kgm3 REAL,
    PRIMARY KEY (site_id, sample)
);

-- ---------------------------------------------------------------------------
-- Indexes
-- (observers.name and instruments.name are UNIQUE, so SQLite indexes those
-- automatically; the primary keys on the join tables cover site_id-first
-- lookups. These cover the remaining access paths.)
-- ---------------------------------------------------------------------------

-- Fetch a pit's measurements by kind — the load/export hot path.
CREATE INDEX IF NOT EXISTS idx_layers_pit ON layers(site_id, kind);

-- The Saved Pits finder always scopes by owner first, then filters or sorts.
CREATE INDEX IF NOT EXISTS idx_sites_owner_date ON sites(owner, date DESC);
CREATE INDEX IF NOT EXISTS idx_sites_owner_updated ON sites(owner, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_sites_owner_pit_search
    ON sites(owner, pit_id COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_sites_owner_site_search
    ON sites(owner, site COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_sites_owner_campaign_date
    ON sites(owner, campaign_id, date DESC);
CREATE INDEX IF NOT EXISTS idx_sites_pending
    ON sites(owner, pending_export_folder, updated_at DESC);

-- Campaign/season roll-ups ("every pit in WY2026 at Mores Creek"):
-- idx_sites_campaign_date ON sites(campaign_id, date) — created in
-- db.py:_migrate() rather than here, because campaign_id is ADDed to
-- pre-existing databases after this script runs.

-- Reverse lookups from a person/device to their pits ("all pits B. Crew
-- surveyed", "everything measured with SMP serial X"). The join-table PKs are
-- site_id-first, so these directions need their own indexes.
CREATE INDEX IF NOT EXISTS idx_site_observers_observer ON site_observers(observer_id);
CREATE INDEX IF NOT EXISTS idx_site_instruments_instrument ON site_instruments(instrument_id);
CREATE INDEX IF NOT EXISTS idx_layers_instrument ON layers(instrument_id);

-- A pit's calibration series (and cascade-delete support).
CREATE INDEX IF NOT EXISTS idx_ssa_calibration_pit ON ssa_calibration(site_id);

-- ---------------------------------------------------------------------------
-- Seed data
-- The seed is a SUPERSET of the app's §9 checklist: the 14 checklist entries
-- PLUS the three SSA devices offered by the §8 dropdown (IceCube/IRIS/IRIS2),
-- which are selected per-pit rather than checked off. A calibration or LWC
-- save must never fail because its instrument row is missing; get_or_create
-- at save time is the backstop for anything new.
-- ---------------------------------------------------------------------------
INSERT OR IGNORE INTO instruments (name, model) VALUES
    ('Digital LWC', 'Digital LWC meter (Snow Fork / Denoth)'),
    ('Lyte Probe', 'Lyte Probe'),
    ('SMP', 'SnowMicroPen'),
    ('SSA / NIR Box', 'SSA / NIR Box (IceCube / IRIS)'),
    ('Standard ram', 'Standard Rammsonde'),
    ('Powder Ram', 'Powder Rammsonde'),
    ('Force Ram', 'Force Rammsonde'),
    ('Slush Ram', 'Slush Rammsonde'),
    ('Snow Scope', 'Snow Scope'),
    ('Force Snow Scope', 'Force Snow Scope'),
    ('HS Transects', 'HS depth transects'),
    ('Snow Scope Transects', 'Snow Scope transects'),
    ('Stratigraphy pictures', 'Stratigraphy photographs'),
    ('Pit pictures', 'Pit wall photographs'),
    ('IceCube', 'IceCube SSA (1310 nm sphere)'),
    ('IRIS', 'IRIS SSA instrument'),
    ('IRIS2', 'IRIS2 SSA instrument');
