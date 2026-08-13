# Backup and restore runbook

The SQLite database and export directory are one logical dataset. Backing up
only one side can preserve records without files, or files without their
metadata.

## Create a consistent bundle

Run as the same service account that owns the database and export tree:

```bash
python -m cryopit.ops backup --output /srv/cryopit/backups/cryopit-$(date +%F-%H%M).zip
```

The command creates `.cryopit-maintenance` in the export directory, then waits
for the shared archive/attachment lifecycle lock. That blocks behind storage
work already in flight and excludes new folder publication while SQLite is
copied through its backup API and the complete export tree (including hidden
recovery journals) is copied. A before/after source fingerprint is the final
consistency check, and the published ZIP contains a checksummed manifest. While
the marker exists, CryoPit rejects new state-changing API calls and `/readyz`
reports `503`.

Do not place the backup output inside the export tree. Do not back up a live
SQLite file with ordinary `cp` while WAL mode is active.

## Verify

```bash
python -m cryopit.ops verify /srv/cryopit/backups/cryopit-2026-08-05-0130.zip
```

Verification checks ZIP paths, duplicate/encrypted/symlink members, the manifest,
file sizes and SHA-256 values, SQLite integrity, and foreign keys. Copy bundles
off-host according to institutional retention policy and test verification after
transfer.

## Restore drill

Stop CryoPit or remove it from the proxy pool, then restore into empty paths:

```bash
python -m cryopit.ops restore /path/to/backup.zip
```

For an intentional replacement of existing data:

```bash
python -m cryopit.ops restore /path/to/backup.zip --force
```

A forced restore preserves the prior database and export tree with
`.pre-restore-<UTC timestamp>` names. Do not delete those rollback copies until
the restored service passes `/readyz`, the workspace opens, several pits and
attachments load, and a new backup verifies.

## Recovery checks after restore

1. start CryoPit and confirm `/healthz` and `/readyz`;
2. inspect **Needs recovery** in the workspace;
3. reconcile any missing or orphan attachment states through the affected pit;
4. verify a recent pit ZIP download;
5. create and verify a fresh backup;
6. record the restore date, operator, source bundle checksum, and outcome.

## Process-lock warning

On POSIX local filesystems CryoPit uses `flock` in addition to its in-process
thread lock. If startup or an operation logs that cross-process storage locking
is unavailable, schedule backups in the same single CryoPit process model and
do not run multiple WSGI processes against the same database/export pair. The
source fingerprint causes an inconsistent backup attempt to fail rather than
publish a questionable bundle.

## Idle WAL databases

CryoPit initializes SQLite's read-side WAL/SHM state before taking the source
fingerprint. This prevents the backup's own connection from looking like a
concurrent write on an otherwise quiet system.

## Before and after a field import

Create and verify a central backup before applying a field-transfer bundle:

```bash
python -m cryopit.ops backup --output /srv/cryopit/backups/pre-import.zip
python -m cryopit.ops verify /srv/cryopit/backups/pre-import.zip
```

Then dry-run and import with `python -m cryopit.transfer`. After a successful
batch, verify several imported pits and attachments, create another backup, and
retain the original field-transfer ZIP plus its import report according to
project policy. A transfer ZIP is not a replacement for a full installation
backup; it contains selected pit histories rather than the complete central
state.
