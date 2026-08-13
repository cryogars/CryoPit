# Where CryoPit puts things

The layout of the export root, and how a pit remains recoverable when its
campaign, date, or Pit ID is corrected.

## One readable folder per pit

```text
exports/
└── WY2026_GM120260210_20260210/
    ├── csv/
    │   ├── ..._siteDetails_v01_0.csv
    │   ├── ..._temperature_v01_0.csv
    │   ├── ..._density_v01_0.csv
    │   ├── ..._density_gap_filled_v01_0.csv
    │   ├── ..._LWC_v01_0.csv
    │   ├── ..._stratigraphy_v01_0.csv
    │   └── ..._SSA_v01_0.csv
    ├── figures/
    │   ├── ..._profile_v01_0.pdf
    │   └── ..._profile_v01_0.png
    ├── uploads/
    │   ├── sheet/
    │   ├── pitwall/
    │   └── stratigraphy/
    │       ├── 100-062cm/
    │       └── 062-045cm/
    └── .cryopit-archive.json
```

The configured `CRYOPIT_EXPORT_DIR` remains the root for everything. CryoPit
also maintains private `.staging/` and `.locks/` directories beneath that root;
they are implementation details used to publish complete folders and serialize
archive operations.

## Recorded location versus desired name

The friendly name is derived from `{campaign}_{pitID}_{date}`. The database,
however, records the current authoritative name in `sites.export_folder`.

On a first archive, CryoPit builds a complete folder privately, publishes it,
then records it as complete. On a re-archive it compares:

```text
recorded: sites.export_folder
wanted:   name derived from the edited form
```

If they differ, CryoPit records the wanted name in
`sites.pending_export_folder`, renames the **entire existing pit directory**, and
then finalizes the recorded location. The `uploads/` tree therefore moves with
the pit; photographs are not stranded when campaign, date, or Pit ID changes.

A pending value means an operation was interrupted. Such pits are omitted from
the normal Saved Pits finder and shown under **Needs recovery**. Recovery checks
which of the recorded and desired folders actually exists before continuing; it
never guesses when both or neither exists.

The normal finder is owner-scoped at the repository layer. It searches Pit ID,
site, location, campaign, date, recorder, and normalized observers; filters by
campaign and date range; and returns bounded pages ordered by observation date
(newest first) by default, update time, or Pit ID. Attachment-status counts are calculated from the
server manifest and attachment rows, not inferred from the filesystem.

The Stage 11 workspace is the initial application view. It requests a compact
owner-scoped summary from `/api/workspace`, shows recent pits and recovery work,
and combines server-side expected-photo counts with the current browser's
IndexedDB outbox summary. The workspace and field form remain in one assembled
page, so opening the finder or returning to a record preserves finder filters,
form state, and local photo-queue contexts.

## Data, figures, and photographs

| path | meaning | regenerable? |
|---|---|---|
| `csv/` | seven SnowEx-style exports | yes |
| `figures/` | profile PNG and vector PDF | yes |
| `uploads/` | sheet scans and field photographs | **no** |

During a normal re-archive, CryoPit replaces `csv/` and `figures/` from staged
output and leaves `uploads/` untouched.

Attachment writes have their own hidden recovery areas inside the stable pit
folder:

```text
.attachment-staging/   complete files awaiting publication
.attachment-trash/     files awaiting database deletion finalization
.attachment-orphans/   quarantined files that had no attachment row
```

These directories are implementation/recovery state and are not included in a
normal Download. Completed attachment files remain under `uploads/`. Startup
finishes journaled publication/deletion operations; an explicit full
reconciliation additionally scans for missing and orphan files.

Before pending browser photographs enter `uploads/`, their metadata lives in
SQLite `attachment_uploads`. That table records the expected queue UUID and its
pending/stored/cancelled state; the actual pending bytes remain in the
originating browser's IndexedDB outbox. A completed row links to `attachments`,
which identifies the file now stored under the recorded pit folder.

Do not delete the whole recorded pit folder merely because its generated output
can be recreated. The folder may contain irreplaceable photographs. If the
entire recorded folder is missing, CryoPit reports a storage-integrity problem
instead of silently creating a new empty folder that would conceal the loss.
Restore the directory from backup, then use **Needs recovery** or **Archive
Changes**.

## Why stratigraphy photographs use depth folders

`062-045cm` describes the measured interval and remains meaningful when layers
are inserted or split. `layer2` would not: ordinals shift whenever the
stratigraphy changes. Database `layer_id` values are also unsuitable because
form-derived layer rows are rebuilt on re-archive.

## File names

Each generated file repeats campaign, Pit ID, and date so that a loose file is
still identifiable:

```text
WY2026_GM120260210_20260210_density_v01_0.csv
WY2026_GM120260210_20260210_pitwall_01.jpg
```

Existing uploaded photographs retain the name assigned when they were stored;
the parent pit directory is what moves when identity metadata changes.

## What Download gives you

Download builds a ZIP from the current form: seven CSVs, both profile formats,
and any already-uploaded photographs belonging to the loaded `site_id`. It does
not create or update a database record or server export folder.

See [PHOTOGRAPHS.md](PHOTOGRAPHS.md) for image handling and
[MERGING.md](MERGING.md) for combining field deployments.

## Revision and field-transfer state

Stage 14 adds identities and audit state in SQLite rather than embedding local
integer database keys in transfer bundles:

```text
app_metadata.installation_id       persistent UUID for this CryoPit installation
sites.current_revision_id          accepted scientific revision tip
site_revisions                     append-only canonical JSON revision history
transfer_imports                   bundle-level audit record
transfer_import_items              per-pit import result
```

One-way field imports use two private export-root areas:

```text
.transfer-staging/<bundle_id>/<site_id>/
    complete pit folder being prepared for publication

.transfer-conflicts/<bundle_id>/<site_id>/
    small record.json + conflict.json review package
```

The original verified transfer ZIP remains the authoritative source of large
attachment bytes for a conflict. These private directories are not ordinary pit
archives and are excluded from pit downloads. A real import also creates the
existing `.cryopit-maintenance` marker and holds `.locks/storage.lock`, so
readiness fails and live storage writes cannot race the import.

See [MERGING.md](MERGING.md) for the one-way transfer protocol.
