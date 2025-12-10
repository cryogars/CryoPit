# core/exporters.py
import pandas as pd
from core.database import get_conn


def export_pit_to_csv(pit_id: str) -> str | None:
    conn = get_conn()

    pit_df = pd.read_sql_query(
        "SELECT * FROM pits WHERE pit_id = ?", conn, params=(pit_id,)
    )

    if pit_df.empty:
        conn.close()
        return None

    density_df = pd.read_sql_query(
        """
        SELECT * FROM density_measurements
        WHERE pit_id = ? ORDER BY top_cm DESC
        """,
        conn,
        params=(pit_id,),
    )
    temp_df = pd.read_sql_query(
        """
        SELECT * FROM temperature_measurements
        WHERE pit_id = ? ORDER BY height_cm DESC
        """,
        conn,
        params=(pit_id,),
    )
    strat_df = pd.read_sql_query(
        """
        SELECT * FROM stratigraphy_layers
        WHERE pit_id = ? ORDER BY top_cm DESC
        """,
        conn,
        params=(pit_id,),
    )
    weather_df = pd.read_sql_query(
        "SELECT * FROM weather_observations WHERE pit_id = ?", conn, params=(pit_id,)
    )
    ground_df = pd.read_sql_query(
        "SELECT * FROM ground_observations WHERE pit_id = ?", conn, params=(pit_id,)
    )

    conn.close()

    rows = []

    # Header
    rows.append(["SNEX23 Snow Pit Data"])
    rows.append(["CryoGARS Research Group"])
    rows.append([])
    rows.append(["METADATA"])
    rows.append(["Location:", pit_df["location"].iloc[0]])
    rows.append(["Site:", pit_df["site"].iloc[0]])
    rows.append(["Pit ID:", pit_df["pit_id"].iloc[0]])
    rows.append(["Date:", pit_df["date"].iloc[0]])
    rows.append(["Time:", pit_df["time"].iloc[0]])
    rows.append(["Surveyors:", pit_df["surveyors"].iloc[0]])
    rows.append(["Total Depth (cm):", pit_df["total_depth"].iloc[0]])
    rows.append(["UTME:", pit_df["utme"].iloc[0]])
    rows.append(["UTMN:", pit_df["utmn"].iloc[0]])
    rows.append(["UTM Zone:", pit_df["utm_zone"].iloc[0]])
    rows.append(["Slope:", pit_df["slope"].iloc[0]])
    rows.append(["Comments:", pit_df["comments"].iloc[0]])
    rows.append([])

    if not weather_df.empty:
        rows.append(["WEATHER OBSERVATIONS"])
        rows.append(["Precipitation:", weather_df["precipitation"].iloc[0]])
        rows.append(["Sky Condition:", weather_df["sky_condition"].iloc[0]])
        rows.append(["Wind Strength:", weather_df["wind_strength"].iloc[0]])
        rows.append(["Wind Type:", weather_df["wind_type"].iloc[0]])
        rows.append([])

    if not ground_df.empty:
        rows.append(["GROUND OBSERVATIONS"])
        rows.append(["Ground Condition:", ground_df["ground_condition"].iloc[0]])
        rows.append(["Soil Moisture:", ground_df["soil_moisture"].iloc[0]])
        rows.append(["Ground Roughness:", ground_df["ground_roughness"].iloc[0]])
        rows.append(["Vegetation:", ground_df["vegetation"].iloc[0]])
        rows.append(
            ["Vegetation Height (cm):", ground_df["vegetation_height"].iloc[0]]
        )
        rows.append(["Tree Canopy:", ground_df["tree_canopy"].iloc[0]])
        rows.append(["New Snow Depth (cm):", ground_df["new_snow_depth"].iloc[0]])
        rows.append(["New Snow SWE (mm):", ground_df["new_snow_swe"].iloc[0]])
        rows.append([])

    rows.append(["TEMPERATURE PROFILE"])
    rows.append(["Height (cm)", "Temperature (°C)"])
    for _, r in temp_df.iterrows():
        rows.append([r["height_cm"], r["temperature"]])
    rows.append([])

    rows.append(["DENSITY MEASUREMENTS"])
    rows.append(
        [
            "Height Range",
            "Top (cm)",
            "Bottom (cm)",
            "Density A (kg/m³)",
            "Density B (kg/m³)",
        ]
    )
    for _, r in density_df.iterrows():
        rows.append(
            [
                r["height_range"],
                r["top_cm"],
                r["bottom_cm"],
                r["density_a"],
                r["density_b"],
            ]
        )
    rows.append([])

    rows.append(["STRATIGRAPHY"])
    rows.append(
        [
            "Height Range",
            "Top (cm)",
            "Bottom (cm)",
            "Grain Min (mm)",
            "Grain Max (mm)",
            "Grain Mean (mm)",
            "Grain Type",
            "Wetness",
            "Comments",
        ]
    )
    for _, r in strat_df.iterrows():
        rows.append(
            [
                r["height_range"],
                r["top_cm"],
                r["bottom_cm"],
                r["grain_size_min"],
                r["grain_size_max"],
                r["grain_size_mean"],
                r["grain_type"],
                r["snow_wetness"],
                r["comments"],
            ]
        )

    df = pd.DataFrame(rows)
    return df.to_csv(index=False, header=False)
