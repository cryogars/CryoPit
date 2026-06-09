# ❄ CryoPit

A snow-pit data logger for field snow science built by the CryoGARS research group at the Department of Geosciences, Boise State University. CryoPit is a browser-based OS-agnostic [streamlit app](https://streamlit.io/). This means that the app should run fine on Mac, Linux, and Windows. CryoPit is designed so any institution or research group can deploy and adapt it.

---

## What it Does

* **Structured Field Entry**: guided sections for identity, weather, ground, temperature, density, LWC, stratigraphy, SSA, and an instrument/task checklist.
* **Live Snow Profile Plot**: hand-hardness, grain type, density, and temperature on a shared depth axis.
* **SQLite Database**: every pit is optionally saved to a relational schema (see Entity-Relationship Diagram below).
* **Exports**: SnowEx-style (https://nsidc.org/data/snex23_mar23_sp/versions/1), delivered either as a single ZIP download or written to a folder on the machine running the app.

---

## Requirements
* Python 3.10+
* Dependencies are listed in [requirements.txt](./requiremets.txt).

Install them with 

```{bash}
pip install -r requirements.txt
```

---

## Running the App

```{bash}
streamlit run CryoPit_V1.py
```

This opens CryoPit in your browser. On first run it creates the database automatically (default: `cryopit.db` in the working directory). You can point `CRYOPIT_DB_PATH` at a different location to reuse a database across sessions or machines — it must be a CryoPit-created database or at least have a compatible schema (see below), and the app needs read/write access to the path.

> Note: CryoPit currently runs a small local helper service on port 8502 for saving and exporting. It is designed for local use, i.e., running the app on the same machine as the browser. (Deployment to a shared server is on the roadmap; see below.)

---

## Using the App

1. Fill in **Identity** (location, date, and the required fields marked `*`).
   The Pit ID is generated automatically but can be edited.
2. Work through the sections. Add temperature, density, LWC, stratigraphy,
   and SSA rows as needed. The progress bar and checklist track completeness.
   - **Temperature** and **Density** can auto-generate their depth intervals
     (5 cm or 10 cm) from the total snow depth, so you only enter the readings.
   - **LWC** can copy its intervals directly from the density section.
3. **Save to DB** writes the pit to the database without downloading anything.
4. **Export CSVs** saves the pit to the database (so a download always has a
    matching saved record), then either downloads a single .zip of all CSVs,
    or — switch the dropdown to Folder — writes them to a folder path you
    specify. In other words, Exporting  CSVs also write to an SQLite database. This is a safety feature, not a bug, incase your browswer crashes.
5. Open the **Profile** section to see the plotted snow profile.

---

## Exports

Exporting a pit produces a set of **SnowEx-style CSV files** (https://nsidc.org/data/snex23_mar23_sp/versions/1) — one per measurement category — delivered either as a single ZIP download or written to a folder. Files are named:

```
{CAMPAIGN}_{PitID}_{YYYYMMDD}_{parameter}_v01_0.csv
```

For example, `SNEX26_GM1_20260210_density_v01_0.csv`. The six files are:

| File | Contents |
|---|---|
| `…_siteDetails_…` | Site metadata: location, coordinates, observers, weather, ground, equipment, instrument log |
| `…_density_…` | Density by interval (samples A/B/C) |
| `…_temperature_…` | Temperature profile by depth |
| `…_LWC_…` | Liquid water content / permittivity (A/B) |
| `…_stratigraphy_…` | Layers: grain type, grain size, hand hardness, wetness |
| `…_SSA_…` | Specific surface area, with calibration metadata |

All six files are always produced. For a measurement that wasn't collected, its file contains the header block and column titles but no data rows. Missing values within a file are written as `-9999` (the SnowEx no-data convention). The ZIP download bundles all six into one file: `{CAMPAIGN}_{PitID}.zip`.

---

## Configuration

CryoPit reads these environment variables (all optional, sensible defaults shown):

| Variable | Default | Purpose |
|---|---|---|
| `CRYOPIT_DB_PATH` | `cryopit.db` | SQLite database file (created if absent; must be a CryoPit DB if it exists) |
| `CRYOPIT_INSTITUTION` | `CryoGARS · Boise State University` | Institution name |
| `CRYOPIT_CAMPAIGN` | `SNEX25` | Default campaign code |
| `CRYOPIT_API_PORT` | `8502` | Local helper service port |
| `CRYOPIT_EXPORT_DIR` | `exports` | Default folder for folder-export |

You can set these in a `.env` file in the project directory.

---

## Entity-Relationship Diagram

The proposed ER diagram for CryoPit is below. This schema is the same as the [SnowEx DB's schema](https://snowexsql.readthedocs.io/en/latest/database_structure.html) with some modifications/extensions. The core tables — `campaigns`, `sites`, `layers`, `measurement_types`, `instruments`, `observers`, and `site_observers` — map directly to their SnowEx equivalents. CryoPit extends the schema in two ways:

1. `site_instruments` is a CryoPit-specific table that logs which instruments were deployed at each pit, including serial their serial numbers; this information is exported as part of the siteDetails CSV.;
2. `ssa_calibration` stores IceCube/IRIS calibration data (Spectralon reference levels and voltage readings) per pit; 

Fields not present in the SnowEx schema, such as `pit_open_time`, `temp_time_start`, `temp_time_end`, `wise_serial`, `gps_device`, `density_cutter`, `snow_cover_condition`, `standing_water`, etc, are retained for field workflow completeness and based on the lateest pit sheet, and are exported to the siteDetails CSV header. You can find the latest (i.e., digitized) field sheet [here](docs/reference/digitized_pit_sheet.pdf).

This version (V1) is compatible with SQLite. However, the schema is designed for PostgreSQL/PostGIS migration (V2).

<div align="center">
  <a href="images/erd.png">
    <img src="images/erd.png" width="1700">
  </a>
</div>

---

## Querying the DB

The database is plain SQLite, so any SQLite client works. A few examples:

1. **List all pits with campaign, observer, and total depth:**

```sql
SELECT s.pit_id, s.date, c.name AS campaign, o.name AS recorded_by,
       s.total_depth, s.latitude, s.longitude
FROM sites s
LEFT JOIN campaigns c ON c.id = s.campaign_id
LEFT JOIN observers o ON o.id = s.recorded_by
ORDER BY s.date DESC;
```

2. **Density profile for one pit** (layers join `measurement_types` by name):

```sql
-- GM1 is the PIT ID
SELECT l.depth_from_surface, l.top_cm, l.bottom_cm,
       l.value AS density_a, l.value_b AS density_b, l.value_avg
FROM layers l
JOIN sites s ON s.id = l.site_id
JOIN measurement_types mt ON mt.id = l.measurement_type_id
WHERE s.pit_id = 'GM1' AND mt.name = 'density'
ORDER BY l.depth_from_surface;
```

Measurement types available for the `mt.name` filter include `temperature`, `density`, `permittivity` (LWC), `grain_size` (stratigraphy), and `ssa`.

---

## Modularity is on the Horizon

CryoPit is being built toward a modular architecture so different groups can easily adapt it to their workflows. Planned and in-progress directions:

1. **More Export Formats** CryoPit currently exports CSVs. We plan to add more formats, such as CAAML, to improve interoperability with community tools like niViz. If you have suggestions on useful formats for the community, please raise an issue or contact the author (see contact below).
2. **Server Deployment**: The current local helper service is the part most tied to single-machine use. A deployment-ready version will move save/export to native server-side actions so CryoPit can run behind a shared server with several users at once. By deployment-ready, we mean hosting CryoPit behind a URL where several users can connect and work concurrently. For higher write concurrency, the SQLite database can migrate to PostgreSQL (the schema is already designed for this); small teams can continue on SQLite (it's fast and lightweight).
3. **Downloadable Snow Pit Profile Visualization**: Currently, CryoPit does not allow its users to download the snow pit visualization because more enhancements are planned for a future realease. Once those enhancements are finalized, we will provide a download button.

---

## Contact

Ibrahim Alabi (ibrahimolalekana@boisestate.edu)