"""Step 4: weather fields preserve every condition observed during a pit."""
from pathlib import Path
import csv
import io
import sys
import types

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if "flask" not in sys.modules:
    flask_stub = types.ModuleType("flask")
    flask_stub.request = types.SimpleNamespace(headers={})
    flask_stub.has_request_context = lambda: False
    sys.modules["flask"] = flask_stub

from cryopit.export import _build_csvs
from cryopit.repository import _site_values, _validate_payload


def payload(weather=None):
    return {
        "meta": {"pit_id": "WEATHER20260806", "campaign": "TEST", "date": "2026-08-06"},
        "weather": weather or {},
        "ground": {}, "temperature": [], "density": [], "lwc": [],
        "stratigraphy": [], "ssa": [], "instruments": [],
    }


def test_weather_template_uses_four_native_checkbox_groups():
    html = (ROOT / "cryopit/templates/sections/02_weather.html").read_text(encoding="utf-8")
    assert html.count("data-weather-multi") == 4
    assert html.count('type="radio"') == 0
    assert html.count('type="checkbox"') == 21
    assert html.count('data-exclusive-value="None"') == 2
    assert "Select all conditions observed during the pit" in html


def test_legacy_scalar_weather_is_canonicalized_to_arrays():
    p = payload({"sky": "Clear", "wind": "Calm (0 mph)"})
    assert _validate_payload(p) is None
    assert p["weather"] == {
        "precip_rate": [], "precip_type": [],
        "sky": ["Clear"], "wind": ["Calm (0 mph)"],
    }


def test_multiple_weather_values_are_valid_and_normalized_for_site_columns():
    p = payload({
        "precip_rate": ["Very light (0.5 cm/hr)", "Light (1 cm/hr)"],
        "precip_type": ["Snow", "Graupel"],
        "sky": ["Broken (>1/2)", "Overcast"],
        "wind": ["Light (1-16 mph)", "Moderate (17-25 mph)"],
    })
    assert _validate_payload(p) is None
    values = _site_values(p, "alice", "{}", 1)
    assert values["precip_rate"] == "Very light (0.5 cm/hr); Light (1 cm/hr)"
    assert values["precip_type"] == "Snow; Graupel"
    assert values["sky_condition"] == "Broken (>1/2); Overcast"
    assert values["wind"] == "Light (1-16 mph); Moderate (17-25 mph)"


def test_none_cannot_be_combined_with_specific_precipitation():
    p = payload({"precip_type": ["None", "Snow"]})
    msg = _validate_payload(p)
    assert msg == "Weather precip type cannot combine None with another value."


def test_invalid_or_non_list_weather_is_rejected():
    p = payload({"sky": {"Clear": True}})
    assert _validate_payload(p) == "weather selections must be a list"
    p = payload({"sky": ["Clear", "Impossible"]})
    assert _validate_payload(p) == "Weather sky has an invalid value."


def test_site_details_csv_writes_all_selected_weather_values():
    p = payload({
        "precip_rate": ["Light (1 cm/hr)", "Moderate (5 cm/hr)"],
        "precip_type": ["Snow", "Graupel"],
        "sky": ["Clear", "Overcast"],
        "wind": ["Calm (0 mph)", "Light (1-16 mph)"],
    })
    assert _validate_payload(p) is None
    csvs = _build_csvs(p)
    site = next(text for name, text in csvs.items() if "siteDetails" in name)
    rows = {row[0]: row[1] for row in csv.reader(io.StringIO(site)) if len(row) >= 2}
    assert rows["# Precipitation Rate"] == "Light (1 cm/hr); Moderate (5 cm/hr)"
    assert rows["# Precipitation Type"] == "Snow; Graupel"
    assert rows["# Sky"] == "Clear; Overcast"
    assert rows["# Wind"] == "Calm (0 mph); Light (1-16 mph)"


TESTS = [v for k, v in list(globals().items()) if k.startswith("test_")]
if __name__ == "__main__":
    failures = 0
    for test in TESTS:
        try:
            test()
            print("PASS", test.__name__)
        except Exception as exc:
            failures += 1
            print("FAIL", test.__name__, repr(exc))
    if failures:
        raise SystemExit(f"{failures} weather multi-select tests failed")
    print(f"{len(TESTS)} weather multi-select tests passed")
