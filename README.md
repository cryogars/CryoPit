# ❄ CryoPit

A snow-pit data logger for field snow science.


Built was built by the CryoGARS research group at the Department of Geosciences, Boise State University. CryoPit is designed so any institution or research group can deploy and adapt it.


## Entity-Relationship Diagram

The proposed ER diagram for CryoPit is below. This schema is the same as the [SnowEx DB's schema](https://snowexsql.readthedocs.io/en/latest/database_structure.html) with some modifications. The core tables — `campaigns`, `sites`, `layers`, `measurement_types`, `instruments`, and `observers` — map directly to their SnowEx equivalents. CryoPit extends the schema in three ways: `site_instruments` is a CryoPit-specific table that logs which instruments were deployed at each pit and feeds the instrument checklist in the form and siteDetails CSV export; 

This version (V1) is compatible with SQLite. However, the schema is designed for PostgreSQL/PostGIS migration (V2).

<div align="center">
  <a href="images/erd.png">
    <img src="images/erd.png" width="1700">
  </a>
</div>
