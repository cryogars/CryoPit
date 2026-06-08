# ❄ CryoPit

A snow-pit data logger for field snow science built by the CryoGARS research group at the Department of Geosciences, Boise State University. CryoPit is designed so any institution or research group can deploy and adapt it.

## What it Does

* **Structured Field Entry**: guided sections for identity, weather, ground, temperature, density, LWC, stratigraphy, SSA, and an instrument/task checklist.
* **Live Snow Profile Plot**: hand-hardness, grain type, density, and temperature on a shared depth axis.
* **SQLite Database**: every pit is optionally saved to a relational schema (see Entity-Relationship Diagram below).
* **Exports**: SnowEx-compatible CSVs, delivered either as a single ZIP download or written to a folder on the machine running the app.

## Requirements
* Python 3.10+
* Dependencies are listed in [requirements.txt](./requiremets.txt).


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
