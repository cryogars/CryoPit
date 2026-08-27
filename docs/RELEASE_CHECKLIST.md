# CryoPit release checklist

## Build

- [ ] Build from a clean source tree.
- [ ] Confirm Python 3.11+ and install `requirements.lock` in a fresh virtual or Conda environment.
- [ ] Run `npm ci` for the browser test harness.
- [ ] Run `tests/run_all.sh` with no skipped suites.
- [ ] Confirm the DOM suite reports at least 300 executed assertions, not merely exit code 0.
- [ ] Confirm the release ZIP checksum and clean-extraction comparison.

## SSO boundary

- [ ] CryoPit is reachable only through the authenticated proxy.
- [ ] The proxy strips and replaces the configured identity header.
- [ ] The claim is immutable, unique, nonblank, and documented.
- [ ] Missing identity fails closed.
- [ ] A stable secret of at least 32 characters is installed securely.
- [ ] Alice/Bob route-isolation tests pass.

## Web and storage

- [ ] Public URL is HTTPS; HSTS is enabled only after HTTPS is confirmed.
- [ ] Proxy and CryoPit body-size limits agree.
- [ ] Database is on local disk, not synchronized or network storage.
- [ ] Export tree permissions allow only the service account and operators.
- [ ] `/healthz` and `/readyz` are monitored.
- [ ] Logs retain request IDs and rotate under institutional policy.
- [ ] Capacity alerts cover database, exports, staging, trash, and backups.
- [ ] Logs contain no degraded cross-process-lock or fsync warning.
- [ ] Deployment uses one process unless POSIX `flock` has been verified on the actual export filesystem.

## Interface and accessibility

- [ ] Workspace and form render without external font or asset requests.
- [ ] Light and dark themes have no undeclared CSS variables or browser-default button backgrounds.
- [ ] Current-work **Continue draft/record** text remains readable at rest, hover, and keyboard focus in both themes.
- [ ] Desktop, tablet, and mobile layouts have no horizontal page overflow.
- [ ] Keyboard focus is visible and every form/table control has an accessible name.
- [ ] Lifecycle banners do not overlap the fixed command bar or form content.
- [ ] High-contrast, reduced-motion, browser zoom, and print behavior are reviewed.
- [ ] Attachment states remain understandable without colour alone.

## Field transfer

- [ ] Every field source resolves archive and attachment recovery before export.
- [ ] Transfer ZIP is written outside `CRYOPIT_EXPORT_DIR` and moved through a controlled channel.
- [ ] `inspect` and `import --dry-run` succeed before a real import.
- [ ] The operator confirms the stable destination SSO owner supplied to `--owner`.
- [ ] New, already-imported, fast-forward, attachment-only, resume, and conflict classifications are reviewed.
- [ ] A verified central backup exists immediately before the import.
- [ ] `/readyz` returns `503` and live writes are blocked while a real import holds maintenance mode.
- [ ] Repeated import of the same bundle is idempotent.
- [ ] Divergent central/field revisions are quarantined and never overwritten automatically.
- [ ] Imported attachments and pending expected-photo records are sampled against the bundle.
- [ ] Original transfer ZIP, SHA-256, dry-run plan, and result report are retained for audit.
- [ ] A post-import central backup verifies.

## Operations

- [ ] Backup bundle creates and verifies.
- [ ] Restore drill succeeds on a separate path or host.
- [ ] Migration and rollback steps have named operators.
- [ ] Recovery-required pits and attachments have a response owner.
- [ ] Completed environment file is stored outside version control.
