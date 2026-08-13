# How CryoPit handles photographs

What happens to an image between your camera and the archive.

## The short version

| | |
|---|---|
| **Accepted** | JPEG, PNG, WebP, HEIC — plus PDF for the pit sheet only |
| **Resolution** | untouched. Whatever your camera shot is what gets stored |
| **HEIC** | converted to JPEG, same pixel dimensions |
| **PNG / WebP** | stored exactly as uploaded, *not* converted |
| **Size limit** | 10 MB per file |
| **Identity** | the file's bytes, the pit, the category, and (for layer photos) the depth interval |

**Not everything comes out as JPEG.** Only HEIC is converted. A PNG stays a
PNG.

---

## 1. What "full resolution" means here

**Pixel dimensions.** A 4032 × 3024 photograph is stored as 4032 × 3024.

It does **not** mean 300 DPI. DPI is a printing instruction stored as metadata —
it says how large to print an image, not how much detail it holds. The same
4032 × 3024 photograph is 13 inches wide at 300 DPI and 40 inches wide at 100
DPI, with identical pixels either way. For a photograph of a crystal card, the
pixel count is what matters and the DPI tag is irrelevant.

### Why this is worth stating

An earlier version redrew every uploaded image through a canvas at a maximum of
2000 px and re-encoded it as JPEG at quality 0.8 — roughly a **75% cut in
pixels**, plus compression artefacts, applied silently, with the original never
leaving the phone.

That is survivable for a pit-wall overview. It is not survivable for a grain
photograph you would zoom into to argue facets versus rounds. The downscale was
removed: the app no longer makes that decision for you.

## 2. HEIC, and why it is the exception

iPhones have shot **HEIC** by default since iOS 11, so it is the format a field
crew is most likely to produce.

CryoPit converts HEIC to JPEG on upload at full resolution — every pixel kept,
`quality=95`, no chroma subsampling. Only the compression changes.

**Why convert rather than store it?** So the archive stays in one format that
anything can open: a colleague on Windows, a Python script five years from now,
whatever tool eventually reads the export. HEIC support is still patchy outside
Apple platforms, and a photograph nobody can open is not evidence.

**Why not convert PNG and WebP too?** They are already universally readable, so
converting them would be lossy re-encoding for no gain.

If the server is missing the `pillow-heif` library, the HEIC is stored exactly
as it arrived rather than refused. Losing a field photograph because a server
lacks a dependency would be the worse failure by far.

HEIC decoding is intentionally bounded separately from normal web-request
concurrency. `CRYOPIT_HEIC_CONCURRENCY` defaults to `1`, matching CryoPit's
pre-resource-hardening effective serialization while allowing routine form and
archive requests to continue. The HEIC source and converted JPEG are staged on
disk; only the decoder's unavoidable full-resolution pixel buffers remain in
memory. Raise the conversion limit only after measuring representative iPhone
files on the deployment host.

### One thing to know about iPhones

Uploading straight from the phone through Safari often converts HEIC to JPEG
before CryoPit ever sees it — iOS does this itself for file inputs. The case
where a real HEIC arrives is usually photographs copied to a laptop first and
uploaded from there. Either path works.

If you would rather avoid HEIC entirely:
**Settings → Camera → Formats → Most Compatible.**

## 3. File type is checked by content, not by name

CryoPit reads the first few bytes of every upload and identifies the format from
them. Renaming `notes.txt` to `photo.jpg` does not get it in.

Those leading bytes are called *magic bytes* — a short signature at the start of
a file that identifies its format. A JPEG begins `FF D8 FF`; a PNG begins
`89 P N G`.

**HEIC is identified by its `ftyp` box.** `ftyp` is not a library or a package —
it is four literal characters sitting inside the file, at byte offset 4,
followed by a four-character *brand* saying which flavour of the container it
is:

```
00 00 00 1C  f t y p  h e i c ...
             ^^^^^^^  ^^^^^^^
             the box   the brand
```

HEIC shares this container format with MP4 video, which is why the brand matters
— `heic` is a photograph, `mif1` and `heix` are related image brands, and
CryoPit accepts that set.

## 4. The same photograph is never stored twice

Every upload is fingerprinted with **SHA-256**, a short code computed from the
file's contents. Identical contents always produce the same fingerprint;
different contents essentially never do. The filename is not part of it, so
renaming a file does not disguise it.

An attachment is identified as **this pit + this category + these bytes + the
layer**. Add the same photograph to the same layer twice and the second is
skipped, and you are told where:

```
Stratigraphy: 2 uploaded, 1 skipped (already on 100-062cm)
```

The layer is part of the identity on purpose. One wide shot may legitimately
document several layers, so the same file attached to 100-062cm and to
062-045cm is two attachments, not a repeat.

### The honest limit

Anything that changes the bytes defeats it, even when the picture looks
identical: re-exporting from Lightroom, stripping EXIF, a different JPEG quality
setting. And because HEIC conversion happens on the server, two machines running
different library versions could in principle encode the same HEIC to slightly
different JPEGs.

Within one laptop it is exact. Across laptops during a merge, treat it as a good
hint rather than a guarantee.


## 5. Before upload: the durable browser outbox

Selecting a photograph does not send it immediately. CryoPit first stores the
original browser `File` in **IndexedDB**, together with:

- a client-generated queue UUID;
- category;
- layer top and bottom for stratigraphy photographs;
- filename, MIME type, size, and last-modified time;
- SHA-256 when the browser's secure crypto API is available;
- queue status and the most recent upload error.

IndexedDB is temporary recovery storage, not the scientific archive. It is
local to the exact CryoPit web origin and browser profile. The same mechanism is
used whether the page talks to a remote HTTPS deployment or to CryoPit running
on `localhost` on a field laptop.

The lifecycle is:

```
selected -> saving locally -> safely queued -> uploading -> server confirmed
                                                           |
                                                           +-> delete local copy
```

A network error or server rejection changes the item to **failed** but keeps it
in IndexedDB. Pressing Archive again retries it. If the browser closes while an
item says saving or uploading, the next page load restores it as queued rather
than assuming the previous attempt completed.

CryoPit asks the browser for persistent storage where supported and displays
whether the queue is persistent or best effort. The browser can still remove
best-effort data under severe storage pressure, and a user can always erase it
by clearing site data. CryoPit therefore deletes a queue item only after the
server confirms the attachment was stored, or confirms that identical bytes
were already attached.

**Start New Pit** is a destructive boundary for the local outbox. It names the
number of queued files and requires confirmation before deleting them. Queued
files are never silently transferred to another pit.

## 6. The server-side expected-photo manifest

The browser outbox protects the bytes. The server needs a separate record that
those bytes are expected. On **Archive** or **Archive Changes**, CryoPit sends a
metadata-only manifest in the same request as the pit form. For every queued
photograph SQLite stores:

- the client-generated queue UUID;
- the immutable pit `site_id`;
- category and optional stratigraphy depth interval;
- original filename, MIME type, size, and client checksum;
- one of `pending`, `stored`, or `cancelled`;
- the completed `attachment_id` once storage succeeds.

The image blob is never stored in this table. Until upload succeeds it remains
in IndexedDB on the originating browser. Once the server confirms the file, the
normal `attachments` row and the file in `uploads/` are authoritative and the
browser deletes its recovery copy.

This separation handles several failure cases deliberately:

```
archive succeeds, upload fails
    -> pit remains archived
    -> browser retains the file
    -> server reports one pending photograph

server stores file, response is lost
    -> browser retries the same queue UUID
    -> server returns the existing attachment (no duplicate)

browser queue is lost or opened on another device
    -> server still shows “expected · unavailable here”
    -> user can reselect the file or explicitly cancel the expectation
```

Absence from a later browser manifest is **not** cancellation, because another
browser may own the durable outbox item. Cancellation is an explicit operation.
A cancelled queue UUID cannot later be reused for different bytes.

The server validates manifest limits before it creates or updates the pit, then
verifies filename, size, checksum, category, and layer interval again when the
blob arrives. A byte-identical attachment already stored for the same pit,
category, and layer can satisfy another queue UUID without writing another file.

## 7. Recoverable server publication and deletion

The browser/server manifest makes a retry identifiable; Stage 9 makes the
filesystem and SQLite sides recoverable. They still cannot participate in one
true transaction, so CryoPit uses a small per-upload journal.

For a new attachment:

```text
reserve final relative path in SQLite
-> write bytes to .attachment-staging/<queue_id>.part
-> record the staged path and stored-byte checksum
-> os.replace() the complete file into uploads/
-> insert/link the attachments row and mark the queue item stored
```

The staging and final paths are inside the same recorded pit folder, so the
publish rename does not copy the photograph. If the final SQLite step fails in
an ordinary request, CryoPit moves the file back to staging. If the process
dies between the rename and the database commit, startup or the next attachment
request sees the journal, finds the final file, verifies its SHA-256, and
finishes the database transition. A retry of the same queue UUID is therefore
safe after either a lost response or a server crash.

Deletion follows the same pattern in reverse. CryoPit first marks the
attachment `pending_delete`, atomically moves the file into
`.attachment-trash/`, then removes the database row. A crash leaves a visible,
retryable deletion journal rather than a database row pointing to a partially
removed file. The UI exposes deletion only for stored attachments and checks
pit ownership on the server.

A full reconciliation can also:

- mark database attachments whose files are missing;
- return linked expected-upload rows to `pending`, so the same queue UUID can
  repair a missing file;
- move files present under `uploads/` but absent from SQLite into
  `.attachment-orphans/` rather than deleting them;
- remove old unreferenced staging/trash files;
- finish interrupted publications and deletions.

Downloads are now built from attachment rows, not by blindly walking
`uploads/`. An orphan file is therefore never silently included, and a missing
file cannot be mistaken for a valid attachment.

## 8. Where photographs are stored

Never in the database — only their metadata is. The files live in the pit's
export folder:

```
exports/WY2026_GM1_20260210/uploads/
├── sheet/
├── pitwall/
└── stratigraphy/
    ├── 100-062cm/          <- named by the depth interval they show
    ├── 062-045cm/
    └── 045-000cm/
```

Stratigraphy photographs are filed by **depth interval**, not by layer number,
because a layer number shifts the moment anyone inserts a layer above it — while
`062-045cm` keeps meaning the same snow.

Files are renamed to the pit's convention on upload
(`WY2026_GM1_20260210_pitwall_01.jpg`), so a photograph found loose on a USB
stick still identifies its pit.

**These files cannot be regenerated.** The CSVs and the profile figure are
derived from the database and come back if deleted; a photograph does not. See
[MERGING.md](MERGING.md).

## 9. The profile figure is not a photograph

The `..._profile_v01_0.png` in each pit folder is drawn by CryoPit from your
measurements — it is not an uploaded image, and it *is* regenerable.

It is rendered at **150 DPI** by default (`CRYOPIT_FIGURE_DPI`), 9 × 7.2 inches, giving roughly **1335 × 1128
pixels**. That is comfortable on screen and in a report. Set `CRYOPIT_FIGURE_DPI=300` to write a larger PNG (about 2670 × 2256 px). **300 DPI is the supported raster maximum.** Complex profiles at 600 DPI and above can require more than a GiB of transient server memory, while the PDF already provides scale-free output. The
on-screen preview is always 150 regardless: it is redrawn on every edit, so a
larger render would slow the form for no visible gain.

Usually you do not need the larger PNG. The PDF in the same folder is vector
and already scale-free, which is what a journal actually wants.

## 10. Limits

| | limit | why |
|---|---|---|
| Pit sheet | 1 PDF **or** 3 images | a scan is one thing or the other, never a mix |
| Pit wall | 6 | |
| Stratigraphy | 20 **per layer** | a 15-layer pit under a pit-wide cap of 20 averaged 1.3 a layer |
| Whole pit | 150 | the point beyond which Download cannot assemble the result |
| Per file | 10 MB | applies to the real file now that nothing is downscaled, so a high-end camera can exceed it |
