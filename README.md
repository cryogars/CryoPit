# ❄ CryoPit

A snow-pit data logger for field snow science.


Built was built by the CryoGARS research group at the Department of Geosciences, Boise State University. CryoPit is designed so any institution or research group can deploy and adapt it.


## Entity-Relationship Diagram

The proposed ER diagram for CryoPit is below. This schema is the same as the [SnowEx DB's schema](https://snowexsql.readthedocs.io/en/latest/database_structure.html) with some modifications/extensions. The core tables — `campaigns`, `sites`, `layers`, `measurement_types`, `instruments`, `observers`, and `site_observers` — map directly to their SnowEx equivalents. CryoPit extends the schema in two ways:

1. `site_instruments` is a CryoPit-specific table that logs which instruments were deployed at each pit, including serial their serial numbers; this information is exported as part of the siteDetails CSV.;
2. `ssa_calibration` stores IceCube/IRIS calibration data (Spectralon reference levels and voltage readings) per pit; 

Fields not present in the SnowEx schema, such as `pit_open_time`, `temp_time_start`, `temp_time_end`, `wise_serial`, `gps_device`, `density_cutter`, `snow_cover_condition`, `standing_water`, etc, are retained for field workflow completeness and based on the lateest pit sheet, and are exported to the siteDetails CSV header. You can find the latest (i.e., digitized) field sheet here.

This version (V1) is compatible with SQLite. However, the schema is designed for PostgreSQL/PostGIS migration (V2).

<div align="center">
  <a href="images/erd.png">
    <img src="images/erd.png" width="1700">
  </a>
</div>
