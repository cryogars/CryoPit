"""CryoPit density-rule tests (docs/DENSITY.md). Plain asserts, no
test framework needed:  python3 tests/test_density.py"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from cryopit.density import analyze, DensityValidationError

MEAS = [{"top": 88, "bottom": 78, "a": 180, "b": 184},
        {"top": 78, "bottom": 68, "a": 222, "b": 228, "c": 225},
        {"top": 58, "bottom": 48, "a": 275, "b": 281},
        {"top": 48, "bottom": 38, "a": 296, "b": 302},
        {"top": 38, "bottom": 28, "a": 312, "b": 318, "c": 315},
        {"top": 28, "bottom": 15, "a": 330, "b": 336}]


def test_three_gap_types():
    r = analyze(MEAS, 95)
    col = r["column"]
    # top gap: nearest edge interval extends to the surface, measured extent kept
    assert col[0]["top"] == 95 and col[0]["meas_top"] == 88
    assert "extended to HS" in col[0]["source"]
    # middle gap 68-58: mean of neighbours
    mid = [x for x in col if x["source"].startswith("gap-filled")][0]
    assert (mid["top"], mid["bottom"]) == (68, 58)
    assert abs(mid["value"] - (225 + 278) / 2) < 1e-9
    # bottom gap: nearest edge interval extends to ground
    assert col[-1]["bottom"] == 0 and col[-1]["meas_bottom"] == 15
    assert "extended to 0" in col[-1]["source"]
    # the filled column tiles the full HS
    assert abs(sum(x["top"] - x["bottom"] for x in col) - 95) < 1e-9
    # per-profile derivations exist for measured profiles, coverage reported
    assert set(r["profiles"]) == {"A", "B", "Extra"}
    assert r["coverage"]["A"] == r["coverage"]["B"] == 66.3
    # each profile exposes its filled column, and it reproduces its bulk
    for lbl, p in r["profiles"].items():
        col = p["column"]
        st = sum(x["top"] - x["bottom"] for x in col)
        assert abs(st - 95) < 1e-9, f"{lbl} column does not tile HS"
        wm = sum(x["value"] * (x["top"] - x["bottom"]) for x in col) / st
        assert abs(wm - p["bulk"]) < 1e-9, f"{lbl} bulk not reproducible"


def test_interval_mean_is_measured_only():
    r = analyze(MEAS, 95)
    row = [x for x in r["column"] if x.get("meas_top") == 78][0]
    assert abs(row["value"] - (222 + 228 + 225) / 3) < 1e-9   # all three measured
    row = [x for x in r["column"] if x.get("meas_top") == 58][0]
    assert abs(row["value"] - (275 + 281) / 2) < 1e-9         # A/B only — no invented C


def test_large_edge_gap_carries_the_nearest_measurement():
    """An edge gap carries the nearest measured interval, however large.

    The nearest interval is used regardless of distance; edge gaps are never
    replaced with a whole-pit weighted mean."""
    r = analyze([{"top": 100, "bottom": 60, "a": 200},
                 {"top": 60, "bottom": 40, "a": 300}], 100)
    last = r["column"][-1]
    assert last["source"] == "measured (extended to 0)", last["source"]
    assert (last["top"], last["bottom"]) == (60, 0), (last["top"], last["bottom"])
    assert abs(last["value"] - 300) < 1e-9, "it carries its OWN value, not a mean"

    # and the same at the top, where the old rule was most misleading
    r2 = analyze([{"top": 40, "bottom": 20, "a": 300}], 100)
    first = r2["column"][0]
    assert first["source"] == "measured (extended to HS)"
    assert (first["top"], first["bottom"]) == (100, 0)


def test_overlap_clips_upper_wins():
    r = analyze([{"top": 20, "bottom": 10, "a": 300},
                 {"top": 13, "bottom": 0, "a": 350}], 20)
    c = r["column"]
    assert (c[1]["top"], c[1]["bottom"]) == (10, 0) and "clipped" in c[1]["source"]


def test_swallowed_interval_dropped():
    r = analyze([{"top": 20, "bottom": 10, "a": 300},
                 {"top": 18, "bottom": 12, "a": 350}], 20)
    assert len([x for x in r["column"] if x["source"].startswith("measured")]) == 1


def test_inverted_interval_blocks():
    try:
        analyze([{"top": 10, "bottom": 20, "a": 300}], 30)
    except DensityValidationError:
        return
    raise AssertionError("inverted interval was accepted")


def test_absent_profile_skipped():
    r = analyze([{"top": 20, "bottom": 0, "a": 300}], 20)
    assert "B" not in r["profiles"] and r["coverage"]["B"] == 0.0


def test_layer_fallback_when_no_intervals():
    r = analyze([], 90, [{"top": 90, "bottom": 50, "layer_density": 220},
                         {"top": 50, "bottom": 0, "layer_density": 330}])
    assert r["layer_fallback"] is True
    assert all("per-layer" in x["source"] for x in r["column"]
               if x["source"].startswith("measured"))
    assert abs(r["bulk"] - (220 * 40 + 330 * 50) / 90) < 1e-9


def test_interval_density_beats_layers():
    r = analyze([{"top": 90, "bottom": 0, "a": 300}], 90,
                [{"top": 90, "bottom": 0, "layer_density": 999}])
    assert r["layer_fallback"] is False and abs(r["bulk"] - 300) < 1e-9


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"{len(fns)} density tests passed")
