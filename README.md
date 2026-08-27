# ❄ CryoPit

CryoPit is a browser-based snow-pit data logger developed by the [CryoGARS research group](https://www.cryogars.com/) at Boise State University. A Python/Flask backend stores observations in SQLite and produces archive products, while the browser interface supports structured field entry, live derived values, profile visualization, coordinate conversion, photographs, and offline-first field workflows.

CryoPit can run on a single Mac, Linux, or Windows laptop in the field, or as a shared institutional service.

## What CryoPit does

- Guides entry of pit identity, weather, ground conditions, temperature, density, LWC, stratigraphy, SSA, instruments, and field tasks using the [digitized pit sheet](docs/reference/digitized_pit_sheet.pdf) as the workflow reference.
- Converts between WGS84 latitude/longitude and UTM coordinates in the browser.
- Calculates bulk density and SWE live from measured density intervals and documented gap-filling rules.
- Generates snow-profile figures from temperature, density, and stratigraphy observations.
- Stores archived pits in a normalized SQLite database with stable internal pit identifiers.
- Creates SnowEx-style CSV products and a dedicated archive folder for each pit.
- Keeps photographs and other attachments associated with the pit, including stratigraphy depth intervals where applicable.
- Supports verified one-way transfer of field-laptop records into a central CryoPit installation without copying SQLite rows directly.

## Requirements

- Python 3.11+
- A modern browser

CryoPit provides three dependency files for different purposes:

- [`requirements.lock`](requirements.lock): fully pinned environment for normal `venv` and production installs.
- [`environment.yml`](environment.yml): supported Conda environment using `conda-forge`.
- [`requirements.txt`](requirements.txt): maintainer-facing direct dependency policy used when intentionally updating dependency ranges.

## Quick start

### Option A: standard Python virtual environment

macOS or Linux:

```bash
python3 --version
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.lock
```

Windows PowerShell:

```powershell
py --version
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.lock
```

If PowerShell blocks activation for the current terminal:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

### Option B: Conda

```bash
conda env create -f environment.yml
conda activate cryopit
python --version
```

No additional pip install is required inside the supported Conda environment.

## Run CryoPit locally

For a simple local run:

```bash
python -m cryopit
```

Then open <http://localhost:8502>.

By default, CryoPit creates `cryopit.db` and an `exports/` directory in the current working directory. For field or production use, configure explicit database and export paths instead. CryoPit accepts `CRYOPIT_*` environment variables and also reads a local `.env` file.

For example, on macOS or Linux:

```bash
mkdir -p test-data
export CRYOPIT_DB_PATH="$PWD/test-data/cryopit.db"
export CRYOPIT_EXPORT_DIR="$PWD/test-data/exports"
python -m cryopit
```

For the complete configuration reference, including ports, campaign naming, attachment limits, rendering limits, authentication, and server settings, see **[docs/CONFIGURATION.md](docs/CONFIGURATION.md)**.

## Shared and institutional deployment

CryoPit can be exposed on a LAN or deployed behind an institutional authentication proxy. Shared deployments require deliberate storage, authentication, backup, and concurrency configuration rather than simply exposing a local development instance.

See:

- **[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)** for Docker, LAN, institutional, and hosted deployment.
- **[docs/SECURITY.md](docs/SECURITY.md)** for the authentication and authorization boundary.
- **[docs/BACKUP_RESTORE.md](docs/BACKUP_RESTORE.md)** for consistent backups and restore drills.

## Data, calculations, and archive products

CryoPit keeps the scientific rules and storage behavior documented separately so the README does not duplicate implementation details:

- **Density and SWE:** [docs/DENSITY.md](docs/DENSITY.md)
- **Profile figure conventions:** [docs/PLOTS.md](docs/PLOTS.md)
- **Photographs and attachments:** [docs/PHOTOGRAPHS.md](docs/PHOTOGRAPHS.md)
- **Archive folders and downloads:** [docs/STRUCTURE.md](docs/STRUCTURE.md)
- **Validation rules:** [docs/VALIDATION.md](docs/VALIDATION.md)

The canonical SQLite schema is [`cryopit/schema.sql`](cryopit/schema.sql). A rendered schema diagram is available at [docs/schema.png](docs/schema.png).

## Field-laptop transfer

Independent field laptops should **not** be combined by copying or merging SQLite rows. CryoPit provides a verified transfer-bundle workflow that preserves stable pit identity, revision ancestry, attachments, archive content, and provenance while rebuilding normalized rows in the destination database.

See **[docs/MERGING.md](docs/MERGING.md)** for export, inspection, dry-run, import, idempotency, and conflict handling.

## Documentation

| Topic | Reference |
| --- | --- |
| Configuration | [docs/CONFIGURATION.md](docs/CONFIGURATION.md) |
| Deployment | [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) |
| Security model | [docs/SECURITY.md](docs/SECURITY.md) |
| Backup and restore | [docs/BACKUP_RESTORE.md](docs/BACKUP_RESTORE.md) |
| Upgrade and rollback | [docs/UPGRADE_ROLLBACK.md](docs/UPGRADE_ROLLBACK.md) |
| Field transfer | [docs/MERGING.md](docs/MERGING.md) |
| Density and SWE | [docs/DENSITY.md](docs/DENSITY.md) |
| Profile figures | [docs/PLOTS.md](docs/PLOTS.md) |
| Photographs | [docs/PHOTOGRAPHS.md](docs/PHOTOGRAPHS.md) |
| Archive structure | [docs/STRUCTURE.md](docs/STRUCTURE.md) |
| Validation | [docs/VALIDATION.md](docs/VALIDATION.md) |
| Interface and accessibility | [docs/INTERFACE.md](docs/INTERFACE.md) |
| Release checks | [docs/RELEASE_CHECKLIST.md](docs/RELEASE_CHECKLIST.md) |
| Future work | [docs/FUTURE_WORK.md](docs/FUTURE_WORK.md) |
| Test suite | [tests/README.md](tests/README.md) |

## Development and testing

Run the repository test suite with:

```bash
bash tests/run_all.sh
```

The test inventory and environment-specific notes are documented in **[tests/README.md](tests/README.md)**. CI also checks the locked Python environment across supported Python versions and exercises the Conda installation path.

## Citation

Software citation metadata is provided in [`CITATION.cff`](CITATION.cff).
