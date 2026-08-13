# One-way field transfer into central CryoPit

CryoPit supports a one-way, revision-aware transfer workflow for combining
records collected in several independent field-laptop databases into one
institutional installation:

```text
field laptop A ─┐
field laptop B ─┼─ verified transfer ZIPs ─→ central CryoPit
field laptop C ─┘
```

The central installation is the long-term authority. CryoPit does not copy
central edits back to the laptops and does not merge raw SQLite rows.

## Why this is an importer, not a database merge

Each pit has a globally unique immutable `site_id`, but many normalized tables
also contain local integer IDs (`layer_id`, `observer_id`, `instrument_id`, and
`attachment_id`). Those integers are meaningful only in the database that
created them. Copying rows between databases can silently connect a pit to the
wrong observer, instrument, layer, or attachment.

A transfer bundle instead carries:

- the immutable pit `site_id`;
- canonical scientific JSON for every revision;
- revision UUIDs and ancestry;
- source installation and owner provenance;
- attachment metadata, queue UUIDs, files, and SHA-256 hashes;
- the complete archived pit folder and generated outputs;
- a checksummed outer manifest.

The central importer validates the bundle, replays the canonical JSON through
CryoPit's current validation and normalization code, creates destination-local
integer IDs, and stages the attachment files before publication.

## Revision model

`sites.current_revision_id` points to the accepted tip in the append-only
`site_revisions` table. Every scientific form state has:

```text
revision_id             globally unique UUID
site_id                 immutable pit UUID
parent_revision_id      revision this edit was based on
revision_number         display/order number within the pit
record_hash             SHA-256 of canonical scientific JSON
source_installation_id  persistent UUID of the producing CryoPit installation
source_owner            owner on the producing installation
```

The first archive creates revision 1 with no parent. A changed re-archive
creates a new revision whose parent is the former tip. Re-archiving unchanged
content does not create another revision.

The importer applies Policy B: a newer field revision is accepted only when it
fast-forwards the destination's current revision.

```text
destination tip == incoming parent/ancestor  → safe fast-forward
same revision and content                    → already imported
same site_id, independent revision branches  → conflict; do not overwrite
```

Timestamps are retained for audit, but they do not decide conflicts.

## Create a bundle on a field laptop

Run from the CryoPit environment that owns the field database and export tree:

```bash
python -m cryopit.transfer export \
  --output /path/outside/exports/field-laptop-07-2026-02-12.zip
```

The command uses `CRYOPIT_DB_PATH`, `CRYOPIT_EXPORT_DIR`, and
`CRYOPIT_DEV_USER` by default. Explicit paths precede the subcommand:

```bash
python -m cryopit.transfer \
  --db /field/cryopit.db \
  --exports /field/exports \
  export --owner local --output /transfer/field-day.zip
```

Export selected pits by repeating `--site-id`:

```bash
python -m cryopit.transfer export \
  --site-id 7a7ed848-8e23-4d4a-becf-f5d30876b0a2 \
  --site-id 9b641db3-d19e-4a48-ad60-03e9df6e808e \
  --output /transfer/selected-pits.zip
```

The output must be outside `CRYOPIT_EXPORT_DIR`. During creation CryoPit enters
maintenance mode and holds the shared storage lifecycle lock. Source pits are
rejected when they have archive recovery pending, missing/corrupt attachments,
untracked upload files, unfinished upload/deletion journals, or an archive
folder that does not match the current scientific record.

A server backup and a field-transfer bundle serve different purposes:

- `python -m cryopit.ops backup` restores one installation as a whole;
- `python -m cryopit.transfer export` moves selected pit histories from a field
  installation into another CryoPit installation.

## Inspect or dry-run centrally

First verify the bundle without touching the destination:

```bash
python -m cryopit.transfer inspect /transfer/field-day.zip
```

Classify it against a destination owner and database:

```bash
python -m cryopit.transfer inspect \
  /transfer/field-day.zip --owner 00u81abc123
```

Or use the import command in read-only dry-run mode:

```bash
python -m cryopit.transfer import \
  /transfer/field-day.zip \
  --owner 00u81abc123 \
  --dry-run \
  --report /transfer/field-day-plan.json
```

A dry run does not create a missing database or export directory. Results may
include:

- `new` — the destination has no matching `site_id`;
- `fast_forward` — the incoming tip descends from the destination tip;
- `attachments` — the scientific revision is already present but verified
  attachment content is new;
- `already` — the revision and attachment identities are already present;
- `resume` — SQLite committed a prior import but folder publication was
  interrupted and can be completed;
- `conflict` — applying the record would be unsafe.

Typical conflicts include an independently edited central record, a different
`site_id` using the same human Pit ID for the chosen owner, an occupied export
folder, a queue/revision UUID collision, an attachment-capacity violation, or a
pit already owned by another destination identity.

## Import

Back up the central installation first, review the dry-run report, then run:

```bash
python -m cryopit.transfer import \
  /transfer/field-day.zip \
  --owner 00u81abc123 \
  --report /transfer/field-day-result.json
```

`--owner` is a trusted destination identity selected by the operator. The
bundle's source owner is provenance only; a field bundle cannot assign itself
an arbitrary SSO owner.

A real import:

1. verifies every ZIP path, size, checksum, record, revision chain, archive
   marker, attachment identity, and upload queue link before touching the
   destination;
2. creates `.cryopit-maintenance`, causing `/readyz` to return `503` and new
   HTTP writes to be rejected;
3. migrates the destination schema if required;
4. reclassifies every pit after maintenance begins to eliminate a plan/apply
   race;
5. holds the shared storage lifecycle lock while updating SQLite and publishing
   folders;
6. rebuilds normalized rows from canonical JSON;
7. stages and checksum-verifies files before durable rename/copy;
8. records the original revision lineage and source installation provenance;
9. writes `transfer_imports` and `transfer_import_items` audit records;
10. removes maintenance mode even when an item fails.

Repeated import of the same bundle is idempotent. Existing stored attachment
content is recognized by pit/category/depth/hash identity, and stable browser
`queue_id` values prevent duplicate expected-photo operations.

## Attachments and expected photographs

Stored photographs move with their checksums and category/depth metadata.
Pending `attachment_uploads` rows also move, because they record that the source
pit still expects a browser-held photograph. The server bundle cannot include
bytes that exist only in a browser's IndexedDB; after import those entries may
therefore appear as expected but unavailable on the central browser until the
original file is selected or uploaded there.

The importer does not propagate deletions. It preserves central attachments and
adds verified incoming attachment identities. A future explicit deletion
protocol would require its own revision/audit semantics rather than inferring
absence from a bundle.

## Conflicts and interrupted imports

Conflicts and item-level import errors never silently overwrite the destination.
CryoPit writes a small review record beneath:

```text
CRYOPIT_EXPORT_DIR/.transfer-conflicts/<bundle_id>/<site_id>/
```

The directory contains the incoming `record.json` and a conflict report. It
does not duplicate large photograph bytes; retain the original verified ZIP as
the authoritative source for those files.

Temporary publication data lives under `.transfer-staging/`. If SQLite commits
before a process interruption, the pit remains marked with
`pending_export_folder`; importing the same verified bundle again can classify
and complete it as `resume`.

## Multiple field databases

Each CryoPit installation receives one persistent random `installation_id` in
`app_metadata`. Therefore several laptops can independently create UUID-based
pits and revisions without coordinating integer keys. Import each verified
bundle separately into the central database. A repeated site is recognized by
`site_id` and revision ancestry; unrelated sites from different installations
coexist normally.

Keep each laptop's database and export tree unchanged until its transfer bundle
has been imported, audited, backed up centrally, and accepted by the project.

## Trust boundary and future enhancements

SHA-256 verifies integrity, not authorship. Treat transfer ZIPs as untrusted
input and accept them only through the importer, but also transport them through
an institutionally controlled channel. Cryptographic bundle signing and an
administrative import UI remain possible future enhancements.
