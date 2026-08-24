# CryoPit configuration reference

Every setting is an environment variable (or a line in `.env`; copy
`.env.example` to `.env` and uncomment what you change). Nothing is required
— an empty `.env` runs the app in safe local mode.

## Serving

| Variable | Default | Notes |
|---|---|---|
| `CRYOPIT_HOST` | `127.0.0.1` | Bind address. `0.0.0.0` accepts network connections — read DEPLOYMENT.md §2–3 first. |
| `CRYOPIT_PORT` | `8502` | A port conflict exits with a clear message. |
| `CRYOPIT_THREADS` | `8` | Waitress worker threads (ignored under Flask's dev fallback or an external WSGI host). Keep 8 as the shared-server starting point; tune only after representative load testing. |
| `CRYOPIT_MAX_BODY_MB` | `16` | Maximum request body in MB; must be larger than the attachment limit to allow multipart overhead. |
| `CRYOPIT_ATTACHMENT_MAX_MB` | `10` | Maximum bytes accepted for one attachment before conversion. |
| `CRYOPIT_HEIC_CONCURRENCY` | `1` | Maximum simultaneous HEIC→JPEG conversions per CryoPit process. Keep separate from `CRYOPIT_THREADS`; raise only after measuring representative iPhone files on the deployment host. |
| `CRYOPIT_FIGURE_DPI` | `150` | Archived profile PNG resolution; allowed range 72–300. The on-screen preview stays at 150 DPI. Use the vector PDF for arbitrary publication scaling rather than an extreme raster DPI. |
| `CRYOPIT_PROFILE_CONCURRENCY` | `2` | Maximum simultaneous server-side Matplotlib profile renders per process. Separate from HTTP threads and HEIC conversion so routine requests stay responsive while render memory is bounded. |
| `CRYOPIT_LOG_LEVEL` | `INFO` | `CRITICAL`, `ERROR`, `WARNING`, `INFO`, or `DEBUG`. |
| `CRYOPIT_ENABLE_HSTS` | `false` | Add a one-year HSTS header. Enable only when the public service is HTTPS-only. |

### HTTP thread tuning

`CRYOPIT_THREADS` controls how many requests the built-in Waitress server can service concurrently. It is intentionally independent from the expensive-operation limits. With the defaults, CryoPit allows one HEIC conversion and two profile renders at once while Waitress retains eight request threads. Those three active heavy operations can therefore leave five threads for routine form/database traffic.

Do not lower the shared-server default merely to control image/profile RAM. The HEIC and profile semaphores are the memory controls for those paths. Four HTTP threads are valid, but with the default heavy-operation limits they can leave only one immediately available request thread during one HEIC conversion plus two profile renders. Also note that a request waiting for an HEIC/profile permit still occupies its Waitress thread, so no thread count can guarantee responsiveness under an unlimited burst of heavy requests. Rate limits, normal browser behavior, and the Stage 6 load/soak test are part of the capacity decision.

Raising the thread count does not raise HEIC or profile concurrency. It only gives the server more room to service or queue other requests. Keep one CryoPit application process against a database/export pair unless the multi-process storage model has been explicitly validated.

## Storage

| Variable | Default | Notes |
|---|---|---|
| `CRYOPIT_DB_PATH` | `cryopit.db` | SQLite file, created if absent. Real local disk only — never a Drive/Dropbox-synced folder or network filesystem (see the README's WAL warning). Relative paths resolve against the launch directory; prefer absolute for anything long-lived. |
| `CRYOPIT_EXPORT_DIR` | `exports` | Pit folders plus archive/attachment staging, temporary download scratch files, trash, recovery journals, and `.locks/storage.lock`. Local disk is recommended; a network/synced filesystem requires a single process and verified rename/locking behavior. |
| `CRYOPIT_SQLITE_JOURNAL` | `WAL` | SQLite journal mode — see the table below. |

### SQLite journal modes

SQLite always keeps enough on disk to survive a crash mid-write; the journal
mode chooses *how*. CryoPit accepts all six; two are sensible, the rest exist
for completeness:

| Mode | How it works | When to use |
|---|---|---|
| `WAL` *(default)* | Changes append to a `-wal` sidecar, merged in periodically. Readers and the writer never block each other. | Local disk (laptop, Docker volume, lab server). The right choice almost always. |
| `DELETE` | Pages about to change are copied to a `-journal` undo file, deleted after commit. Whole-file locking; simple and conservative. | **Network-backed storage** (e.g. PythonAnywhere), where WAL's shared-memory coordination is unreliable. |
| `TRUNCATE` | DELETE, but the journal is truncated instead of unlinked (marginally faster on some filesystems). | Rarely worth choosing over DELETE. |
| `PERSIST` | DELETE, but the journal header is zeroed and the file left in place. | Same niche as TRUNCATE. |
| `MEMORY` | Journal kept in RAM only. **A crash mid-write can corrupt the database.** | Never for real data. |
| `OFF` | No journal at all. **Any interruption can corrupt the database.** | Never for real data. |

An invalid value silently falls back to `WAL`. The `busy_timeout` (10 s) is
set regardless of mode.

### Storage lifecycle locking and durability

Archive, re-archive, attachment upload/deletion/reconciliation, and backup all
share one lock rooted at `CRYOPIT_EXPORT_DIR/.locks/storage.lock`. Threads are
always serialized in-process. POSIX `flock` adds cross-process exclusion when
supported. If `flock`, file `fsync`, or directory `fsync` is unavailable,
CryoPit logs a warning instead of silently claiming the stronger guarantee. In
that degraded mode, run exactly one CryoPit application process against the
database/export pair. Pending journals still make interrupted operations
recoverable.

Browser Download ZIPs are assembled temporarily under
`CRYOPIT_EXPORT_DIR/.download-staging` and streamed from disk so archive size
does not become server RAM usage. These ZIPs are scratch files, are excluded
from CryoPit backups, and are removed after the response closes. Startup also
sweeps leftovers from a killed process or host restart. Plan temporary free disk
space for approximately the size of the largest download that may be generated.

## Access & identity

| Variable | Default | Notes |
|---|---|---|
| `CRYOPIT_ENABLE_EDIT` | `true` | Saved-pits sidebar + load-for-edit. Turn OFF on a shared instance without SSO — otherwise all visitors share one owner identity and can load or update the same saved pits. |
| `CRYOPIT_SAVED_PITS_LIMIT` | `10` | Saved Pits result-page size, per user. Values above 50 are capped by the API. |
| `CRYOPIT_TRUST_PROXY_AUTH` | `false` | Honor the SSO username header. Enable ONLY when an authenticating reverse proxy is the sole path to the app and strips client-supplied copies of the header (DEPLOYMENT.md §3). |
| `CRYOPIT_AUTH_HEADER` | `X-Remote-User` | Header the proxy injects. Ignored unless the trust flag is on. |
| `CRYOPIT_DEV_USER` | `local` | Identity used when proxy auth is off — everyone shares it. |
| `CRYOPIT_SECRET_KEY` | random per process locally | Stable HMAC secret. Required in trusted-proxy mode and must be at least 32 characters. |
| `CRYOPIT_IDENTITY_MAX_LENGTH` | `255` | Maximum normalized SSO subject length. |
| `CRYOPIT_CSRF_TTL_SECONDS` | `43200` | Owner-bound CSRF token lifetime; current and previous time buckets are accepted. |
| `CRYOPIT_RATE_LIMIT_WRITES_PER_MINUTE` | `120` | Per-process state-changing request limit per owner. |
| `CRYOPIT_RATE_LIMIT_UPLOADS_PER_MINUTE` | `200` | Per-process attachment request limit per owner. Sized to allow a complete 150-photo pit outbox to flush in one archive operation. |
| `CRYOPIT_RATE_LIMIT_EXPORTS_PER_MINUTE` | `12` | Per-process profile/download request limit per owner. |

## Branding

| Variable | Default | Notes |
|---|---|---|
| `CRYOPIT_RESEARCH_GROUP` | `CryoGARS` | Shown in the topbar. |
| `CRYOPIT_INSTITUTION` | `Boise State University` | Shown in the browser tab title. |
| `CRYOPIT_CAMPAIGN` | current water year (`WY2026`) | Default campaign code in exports and filenames; the form can override per pit. |

## Field-entry interface

| Variable | Default | Notes |
|---|---|---|
| `CRYOPIT_SHOW_EXAMPLE_PLACEHOLDERS` | `false` | Show sample-looking responses in otherwise-empty field-entry controls. This is presentation-only: it never changes stored data, configured defaults, calculations, or autofill/copy behavior. |

When the setting is `true`, CryoPit shows the following example placeholders.
When it is `false`, those fields are visually blank until the user enters data
or invokes a feature that generates real values.

| Section / field | `true` example | `false` |
|---|---|---|
| Identity — Site / transect | `LSOS, Transect A…` | blank |
| Identity — Total depth | `120` | blank |
| Identity — Pit open | `0830` | blank |
| Identity — Slope | `0` | blank |
| Identity — Field observers | `A. Jones, B. Lee` | blank |
| Identity — GPS device | `GAIA GPSMAP 66` | blank |
| Identity — GPS uncertainty | `3` | blank |
| Identity — UTM Easting / Northing / Zone | `476455` / `7226118` / `11N` | blank |
| Identity — Latitude / Longitude | `65.157650` / `-147.502260` | blank |
| Identity — Flags | `None` | blank |
| Ground — Vegetation height | `0` | blank |
| Temperature — Profile start / end | `0808` / `0828` | blank |
| Manually added Temperature row | height `100`, temperature `-2.0` | blank |
| Manually added Density row | top `120`, bottom `110` | blank |
| LWC — Device | `Snow Fork, WISe, Denoth…` | blank |
| Manually added LWC row | top `120`, bottom `110`, permittivity A `1.173` | blank |
| Manually added Stratigraphy row | top `120`, bottom `110`, grain sizes `0.5` / `1.0` / `0.7` | blank |
| Stratigraphy layer density ρA / ρB | `250` / `252` | blank |
| SSA — Calibration time | `0800` | blank |
| SSA — Spectralon levels | `99,60,40,20,5,0` | blank |
| SSA — Calibration values | `2.024,1.686,1.226,0.705,0.328,0.062` | blank |
| Manually added SSA row | height `35`, signal `1.147`, reflectance `36.22`, SSA `23.76` | blank |

The following are **unaffected** by this setting and always keep their existing
behavior:

| Content / feature | Behavior |
|---|---|
| Campaign | Uses `CRYOPIT_CAMPAIGN` when configured; otherwise defaults to the current water year. |
| Instructional placeholders | Prompts such as `Type location`, `Your name`, `notes…`, `Other instrument…`, and `if different from recorder` remain visible. |
| UI-state placeholders | `auto` and `—` remain visible where they describe an automatic or empty/derived state. |
| Temperature Auto-fill depths | Generated depth values remain available and populate normally. Example temperature placeholders remain hidden when this setting is `false`. |
| Density Auto-fill depths | Generated top/bottom intervals remain available and populate normally. |
| LWC Copy intervals from Density | Copied interval depths remain available and populate normally. Example permittivity placeholders remain hidden when this setting is `false`. |
| Calculated / derived values | Density averages, layer-density averages, interval-board calculations, and other computed values are unchanged. |
| Loaded saved pits and drafts | Real saved/restored values load identically regardless of this setting. |
| Saved/exported data | Unchanged. Placeholders are never treated as measurements or responses. |

Changing this variable requires an application restart, like the other
environment-driven settings.

## Health and maintenance

`/healthz` is a minimal liveness check. `/readyz` validates SQLite access, export-directory permissions, and the absence of the maintenance marker. `python -m cryopit.ops backup` creates that marker while taking a consistent database-plus-files bundle.

## Field-transfer commands

`python -m cryopit.transfer` reads the same `CRYOPIT_DB_PATH`,
`CRYOPIT_EXPORT_DIR`, and local `CRYOPIT_DEV_USER` settings as the application.
Command-line `--db` and `--exports` options, placed before the subcommand,
override those locations for that operation:

```bash
python -m cryopit.transfer \
  --db /field/cryopit.db \
  --exports /field/exports \
  export --owner local --output /transfer/field-day.zip
```

On a central installation, `import --owner` is mandatory. It is the trusted,
stable institutional identity that will own the imported pits. The importer does
not trust an owner requested by the bundle itself. Run a verified backup and a
`--dry-run` before a real import. See [docs/MERGING.md](docs/MERGING.md).
