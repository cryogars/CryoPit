# CryoPit tests

Run everything:

```bash
./tests/run_all.sh
```

CI runs exactly this (see `.github/workflows/ci.yml`), so a green laptop run
means a green pipeline.

| suite | needs | covers |
|---|---|---|
| `python3 tests/test_density.py` | nothing | backend gap-fill and edge-extension rules |
| `node tests/test_density_rail_parity.mjs` | Node | live-rail edge-gap parity with the backend, including gaps larger than 25% of HS |
| `python3 tests/test_instrument_state.py` | nothing | Y / N / unanswered persistence, CSV export, re-archive and contradiction validation |
| `python3 tests/test_stage6_integration.py` | nothing | cumulative site identity, legacy migration, staged first archive, recovery, re-archive renaming, attachment preservation, duplicate-ID and instrument-state behavior |
| `python3 tests/test_stage8_photo_manifest.py` | nothing | expected-photo schema/migration, archive registration, recovery, explicit cancellation, limits, queue-ID identity and idempotent server upload |
| `python3 tests/test_stage9_attachment_consistency.py` | nothing | staged publication, crash recovery, DB-failure compensation, missing/orphan reconciliation, stale-temp cleanup, safe deletion, ownership, and Stage 8-to-9 migration |
| `python3 tests/test_stage10_saved_pits.py` | nothing | owner-scoped Saved Pits search, campaign/date filters, sort order, pagination, recovery separation, status counts, API validation, and indexes |
| `python3 tests/test_stage11_workspace.py` | nothing | owner-scoped workspace summary, recovery/photo counts, API identity boundary, and assembled initial view |
| `python3 tests/test_stage12_security.py` | nothing | fail-closed identity parsing, owner-bound CSRF tokens, configuration requirements, and rate limiting |
| `python3 tests/test_stage12_ops.py` | nothing | consistent backup, manifest verification, traversal/tamper rejection, restore, and rollback copies |
| `python3 tests/test_stage12_storage_lifecycle.py` | nothing | shared archive/attachment lock, stale-folder rejection, degraded-lock warnings, and rename durability |
| `python3 tests/test_stage13_ui.py` | nothing | offline font independence, workspace hierarchy, section subtitles, responsive/contrast/print modes, and explicit attachment-state styling |
| `python3 tests/test_stage14_transfer.py` | nothing | installation/revision identities, one-way bundles, dry-run, owner mapping, idempotency, fast-forward updates, multiple field databases, attachment transfer, recovery resume, tamper rejection, conflicts, and audit rows |
| `python3 tests/test_resource_stage1_downloads.py` | nothing | disk-backed download ZIP contents, normal/interrupted cleanup, and startup stale-file recovery |
| `python3 tests/test_resource_stage2_uploads.py` | nothing | bounded upload-stream staging, incremental digest/header checks, rejection cleanup, and startup stale-file recovery |
| `python3 tests/test_resource_stage3_heic.py` | Pillow | HEIC conversion semaphore, failure-safe permit release, disk-backed JPEG staging, crash cleanup, and conversion-before-storage-lock ordering |
| `python3 tests/test_resource_stage4_profiles.py` | matplotlib | profile render semaphore, failure-safe permit release, single-build PNG+PDF equivalence, and 300-DPI guardrail |
| `python3 tests/test_resource_stage5_threads.py` | nothing | eight-thread shared-server default, four-thread override, Waitress thread wiring, and startup concurrency diagnostics |
| `python3 tests/test_resource_stage6_resilience.py` | nothing | abrupt-process-kill recovery for download/upload scratch files and process-local HEIC/profile permits |
| `python3 tests/test_resource_stage6_live.py` | flask, waitress, matplotlib | boots the real eight-thread Waitress server, verifies startup scratch cleanup, runs two profile requests concurrently, and checks health traffic remains responsive |
| `python3 tests/test_resource_stage7_sizing.py` | nothing | Stage 7 sizing-policy PASS/PROVISIONAL/FAIL boundaries for peak RSS, host headroom, HEIC mode, soak stability, swap, latency, and cleanup |
| `python3 tests/test_weather_multiselect.py` | nothing | canonical weather arrays, legacy scalar compatibility, validation, normalized reporting columns, and CSV export |
| `node tests/test_stage13_ui.mjs` | nothing | automatic accessible field names and non-invasive populated-field presentation state |
| `node tests/test_table_enter_navigation.mjs` | nothing | Enter moves vertically through existing Temperature, Density, LWC, and Stratigraphy rows without adding rows or submitting |
| `node tests/test_clearable_radios.mjs` | nothing | optional native-radio clear action, accessible naming, idempotent setup, and no-scroll focus return |
| `node tests/test_weather_multiselect.mjs` | nothing | native checkbox coexistence, precipitation None exclusivity, array collection, and legacy/new restore behavior |
| `python3 tests/test_stage12_flask_security.py` | flask, matplotlib | real route-level CSRF, headers, health/readiness, size limits, generic errors, and Alice/Bob isolation |
| `node tests/test_stage12_csrf_ui.mjs` | nothing | browser API helper sends CSRF only on unsafe requests |
| `node tests/test_instrument_ui.mjs` | nothing | lightweight real-function checks for collect() and Y/N/unanswered button state |
| `node tests/test_record_workflow_ui.mjs` | nothing | lightweight execution of edit mode, pending recovery state, Start New Pit, and site_id-based loading |
| `node tests/test_attachment_outbox.mjs` | nothing | durable queue persistence, restore, draft-to-site binding, quota failure, duplicate selection, and explicit discard |
| `node tests/test_attachment_flush.mjs` | nothing | failed upload retention, confirmed upload cleanup, duplicate confirmation, and network retry state |
| `node tests/test_attachment_manifest_ui.mjs` | nothing | archive manifest handoff, cancellation safety, server-only expectations, and stored-attachment deletion controls |
| `node tests/test_stage10_saved_pits_ui.mjs` | nothing | finder query construction, status cards, campaign facets, recovery separation, and load-more accumulation |
| `node tests/test_stage11_workspace_ui.mjs` | nothing | workspace navigation, current-record awareness, recent/recovery actions, finder-state preservation, and all-context photo-queue summary |
| `python3 tests/test_plot.py` | matplotlib | profile figure geometry |
| `python3 tests/test_smoke.py` | flask, matplotlib | end-to-end: archive -> load -> download, the seven CSVs + PNG, attachment limits, the pit-sheet PDF/image rule |
| `node tests/test_coords.mjs` | nothing | UTM/WGS84 converter vs PROJ ground truth |
| `node tests/test_dom.mjs` | `npm ci`, **and Python** | loads the real assembled page in a DOM and checks runtime behaviour |

`test_dom.mjs` shells out to Python to assemble the page, so it needs both
runtimes — it is not a pure-JS suite.

## Server resource qualification

The normal suite keeps tests bounded for CI. Before sizing or changing a shared
server, run the Stage 6 resource harness on that host after installing the locked
Python dependencies:

```bash
python3 tests/benchmark_resource_stage6.py --qualification --output stage6.json
```

For a multi-hour stability check, add a wall-clock soak, for example:

```bash
python3 tests/benchmark_resource_stage6.py --qualification --soak-minutes 180 --output stage6-soak.json
```

The harness reports peak/current RSS, host total RAM, minimum `MemAvailable`,
swap growth, staging-disk high-water marks, routine-job latency, open file
descriptors, cleanup leftovers, and whether image conversion used a real HEIC
codec or the clearly labelled decoded-image proxy. On Linux it uses `/proc` if
`psutil` is not installed; on other platforms install `psutil` only for
qualification. `psutil` is intentionally not a CryoPit runtime dependency.

Evaluate a completed target-host soak against the Stage 7 3.5 GiB policy with:

```bash
python3 tests/evaluate_resource_stage7.py stage6-soak.json --ram-gib 3.5
```

The evaluator returns `PASS`, `PROVISIONAL`, or `FAIL`. It deliberately refuses
to turn a proxy-HEIC run, a materially larger benchmark host, or a missing
180-minute soak into a final production-sizing pass.

## Adding a test

Every suite is a plain script with a `check(cond, label)` helper and an exit
code. No framework, no runner, matching the app's no-build-tooling design.

Prefer asserting **behaviour** over implementation strings. A test that read
`.rail`'s `onclick` attribute for the text `data-t=s10` broke when that handler
was extracted into a named function, despite nothing a user could observe
having changed — the replacement collapses the section, fires the event, and
checks that it opened.

## Dependency-limited runs

CryoPit supports Python 3.11 and newer. CI installs `requirements.lock` across the supported Python matrix and also runs the full dependency-backed suite in a Python 3.11 Conda environment. A separate floating-dependency job installs `requirements.txt` to detect future compatibility drift.

The scientific, cumulative integration, operations, security-core, and lightweight JavaScript suites do not require Flask or jsdom. The end-to-end and route-isolation suites require the Python dependencies in `requirements.lock`; the DOM suite requires `npm ci`. A syntax-only pass is not reported as a runtime pass when those dependencies are unavailable. A Stage 14 release candidate is not production-ready until the full dependency-backed suite passes in the target build environment. The Stage 13 interface also receives a real-browser layout/runtime check at representative desktop, tablet, and mobile widths; screenshot review supplements but does not replace the DOM assertions.

### DOM placeholder guard

`test_dom.mjs` records unsubstituted `__TOKEN__` placeholders as a failure but
continues running the remaining assertions, then fails at the end. It also keeps
a minimum assertion-count floor. This preserves the guard without allowing one
placeholder to hide the true state of the DOM suite.

