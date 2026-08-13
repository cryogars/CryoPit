# Security model

## First-generation boundary

CryoPit is designed to run behind an institution's single sign-on service. The
institution authenticates the user; a trusted reverse proxy supplies one stable
institutional subject identifier; CryoPit uses that identifier as the record
owner. The first-generation UI and API expose only that owner's pits.

CryoPit does not implement passwords, OIDC redirects, or SAML itself. It must not
be directly reachable around the authenticating proxy when
`CRYOPIT_TRUST_PROXY_AUTH=true`.

## Required proxy behavior

The proxy must:

1. authenticate every non-health request;
2. remove any client-supplied copy of `CRYOPIT_AUTH_HEADER`;
3. inject the verified stable subject identifier;
4. keep the CryoPit listener private;
5. terminate HTTPS and enforce institutional session policy;
6. apply fleet-wide request and abuse limits.

Use an immutable subject or employee identifier. Store display names and email
separately in a future identity profile; do not use a mutable display name as
ownership identity.

CryoPit fails closed in trusted-proxy mode: a missing, blank, malformed, or
oversized identity receives `401`. Trusted mode also requires a stable
`CRYOPIT_SECRET_KEY` of at least 32 characters.

## Request protections

Stage 12 adds:

- owner-bound, expiring HMAC CSRF tokens on state-changing API requests;
- rejection of browser-declared cross-site API requests;
- request-body and per-attachment size limits;
- per-process write, upload, and export rate limits;
- standard anti-framing, MIME-sniffing, referrer, permissions, robot, and CSP
  headers;
- optional HSTS for HTTPS-only deployments;
- generic unexpected-error responses with request IDs;
- maintenance-mode write blocking;
- filename, path, archive-member, file-signature, and ownership validation.

The current no-build UI still uses inline handlers and styles, so its CSP must
allow `'unsafe-inline'` for scripts and styles. Removing that exception is a
future UI refactor, not a reason to weaken the other directives.

The in-process rate limiter is keyed by authenticated owner and operation class,
not by source IP. Users behind one institutional proxy therefore do not share an
application bucket. It protects one CryoPit process; multi-process or
multi-instance deployments multiply the effective allowance and must also use a
fleet-wide reverse-proxy limit. The default upload allowance is 200/minute so a
maximum 150-photo outbox can flush after offline work. A 429 is treated by the
browser as pacing: bytes remain durable, the item becomes **waiting to retry**,
and `Retry-After` drives an automatic retry.

## Storage lock and worker model

Pit-folder publication and attachment publication share one lifecycle lock at
`CRYOPIT_EXPORT_DIR/.locks/storage.lock`. This closes the race where an upload
could read an old `export_folder` while a re-archive renamed the pit directory.
Uploads re-read the authoritative folder while holding the lock and abort for a
browser-outbox retry if it changed. Backup waits on the same lock before copying
SQLite and the export tree.

The thread lock is always present. On POSIX, `fcntl.flock` also excludes other
CryoPit processes. Windows and some network filesystems do not provide that
guarantee; CryoPit emits a warning and remains supported only as a single
application process. Waitress's configured threads are one process and are
safe. Do not use a multi-process WSGI worker model against the same storage when
that warning appears.

File contents are flushed before publication and containing directories are
`fsync`ed after rename/unlink when the platform supports it. Unsupported syncing
is warned once. Atomic publication plus pending journals remains recoverable,
but the warning means sudden-power-loss durability is weaker.

## Local mode

With `CRYOPIT_TRUST_PROXY_AUTH=false`, every request maps to
`CRYOPIT_DEV_USER` and any spoofed identity header is ignored. This is intended
for one field laptop or one deliberately shared local installation. Binding a
local-mode instance to a non-loopback address means every visitor shares the
same owner and can access the same pits; startup prints a prominent warning.

## Authorization limits

Stage 12 does not add supervisors, administrators, teams, or campaign sharing.
Future role-based access is described in `docs/FUTURE_WORK.md`. Direct database
reporting remains an operational path for authorized institutional staff, but
should use a read-only copy rather than the live database.

## Security verification

Before release, execute `tests/run_all.sh` in the target deployment environment.
The Flask integration suite creates independent Alice and Bob identities and
attempts cross-owner discovery, load, update, attachment, download, and recovery
operations using known identifiers. A deployment is not ready until those tests
pass with its supported Python and browser versions.

## Field-transfer trust boundary

Stage 14 transfer ZIPs are treated as untrusted input. CryoPit rejects unsafe
ZIP paths, links, encrypted/duplicate members, undeclared files, excessive
expanded size, checksum mismatches, invalid UUID/provenance fields, inconsistent
revision chains, and attachment-manifest discrepancies before modifying the
destination.

SHA-256 proves integrity, not who created the bundle. Move bundles through a
controlled institutional channel and retain their checksum. The destination
operator supplies the trusted institutional `--owner`; source-owner metadata is
provenance and cannot grant SSO access. A real import enters maintenance mode
and rechecks its plan after live writes are blocked. Cryptographic bundle
signing is future work.
