"""Density gap-filling and derived quantities (bulk density, SWE).

Implements the documented CryoPit density rules (README "Density rules"):

  DATA
  1. The database stores measured values only. Everything here is DERIVED at
     export/plot time and never stored.
  2. An interval's density is the arithmetic mean of whichever of profiles
     A / B / Extra were actually measured on it — values are never synthesized
     across profiles.

  GEOMETRY CLEANUP (applied before any filling)
  3. Intervals are sorted surface -> ground regardless of entry order.
  4. Inverted intervals (top <= bottom) are a blocking error (always a typo).
  5. Overlapping intervals are clipped: the UPPER interval wins; the lower
     one's top is clipped down to the upper's bottom. An interval swallowed
     whole is dropped. The verbatim `density` CSV keeps what was entered;
     only the gap-filled products see clipped geometry.

  VERTICAL GAP FILLING (whole missing intervals only)
  6. Middle gap: a new interval at the mean of the interval densities
     directly above and below.                       -> "gap-filled (neighbor-mean)"
  7. Bottom gap: the lowest measured interval's bottom becomes 0 (same
     measurement, larger weight).                    -> "measured (extended to 0)"
  8. Top gap: mirror of rule 7.                      -> "measured (extended to HS)"

     There is no size limit on either. An earlier version extended only gaps
     up to 25% of HS and filled anything larger with the thickness-weighted
     mean of the WHOLE pit, which is the wrong estimate for an edge: a top gap
     filled with the pit mean is far too dense, because surface snow is the
     lightest in the pack and the mean is dominated by everything below it.
     The nearest measured interval is the best information available at an
     edge regardless of how far it has to reach, so it carries. The extent is
     reported instead of being silently swapped for a different method.

  DERIVED VALUES
  10. Overall bulk density and SWE come from the gap-filled interval-mean
      column: bulk = sum(rho*t)/sum(t), SWE(mm) = sum(rho*t_cm)/100.
  11. bulk_X and SWE_X (X in A/B/Extra) come from gap-filling profile X's own
      column with the same rules; a profile with no measurements is skipped.
  12. Measured coverage per profile (% of HS covered by X's measured
      intervals, post-clip) is reported alongside.
"""

EPS = 1e-9

# Source tags (also written to the density_gap_filled CSV)
SRC_MEASURED = "measured"
SRC_EXT_BOTTOM = "measured (extended to 0)"
SRC_EXT_TOP = "measured (extended to HS)"
SRC_CLIPPED = "measured (clipped)"
SRC_NEIGHBOR = "gap-filled (neighbor-mean)"
SRC_FALLBACK = "gap-filled (mean-fallback)"   # retained: no-measurement pits only

EDGE_GAP_MAX_FRACTION = 0.25   # the "25% guard" for top/bottom extensions


class DensityValidationError(ValueError):
    """Blocking data problem (e.g. an inverted interval)."""


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None   # NaN -> None


def clean_intervals(rows, kind_label="Density"):
    """Sort surface->ground, reject inverted intervals, clip overlaps
    (upper interval wins). Input rows are dicts with top/bottom (+ payload
    keys, preserved). Returns new dicts with added 'clipped' flag and
    original extents kept as top0/bottom0. Rows without both bounds, or
    swallowed whole by an upper interval, are dropped."""
    withbounds = []
    for i, r in enumerate(rows or []):
        top, bottom = _num(r.get("top")), _num(r.get("bottom"))
        if top is None or bottom is None:
            continue
        if top <= bottom:
            raise DensityValidationError(
                f"{kind_label} interval {i + 1}: top ({r.get('top')}) must be "
                f"greater than bottom ({r.get('bottom')}).")
        withbounds.append({**r, "top": top, "bottom": bottom,
                           "top0": top, "bottom0": bottom, "clipped": False})
    withbounds.sort(key=lambda r: (-r["top"], -r["bottom"]))
    out = []
    for r in withbounds:
        if out and r["top"] > out[-1]["bottom"] + EPS:      # overlaps interval above
            r["top"] = out[-1]["bottom"]
            r["clipped"] = True
            if r["top"] <= r["bottom"] + EPS:
                continue                                     # swallowed whole -> drop
        out.append(r)
    return out


def interval_value(row, profiles=("a", "b", "c")):
    """Mean and half-range of the profile values actually measured on a row.
    Returns (mean, err, n) with mean None when nothing was measured.
    Non-positive values are treated as not-measured (a 0 density is
    physically impossible and must never drag an average down)."""
    vals = [v for v in (_num(row.get(p)) for p in profiles) if v is not None and v > 0]
    if not vals:
        return None, 0.0, 0
    return sum(vals) / len(vals), ((max(vals) - min(vals)) / 2 if len(vals) > 1 else 0.0), len(vals)


def _weighted_mean(col):
    st = sum((r["top"] - r["bottom"]) for r in col)
    if st <= EPS:
        return None
    return sum(r["value"] * (r["top"] - r["bottom"]) for r in col) / st


def gap_fill(column, hs):
    """Apply the vertical gap-filling rules to a measured column
    [{top,bottom,value,err,source...}] (already cleaned, surface-first).
    Returns the full surface->ground column with source tags."""
    col = [dict(r) for r in column if r.get("value") is not None]
    if not col or not hs or hs <= 0:
        return []
    out = []
    # Top edge: the highest measured interval carries up to the surface,
    # however far that is. See the module docstring for why there is no size
    # limit any more.
    gap_top = hs - col[0]["top"]
    if gap_top > EPS:
        col[0]["top"] = hs
        col[0]["source"] = SRC_EXT_TOP
    # body + middle gaps
    for i, r in enumerate(col):
        out.append(r)
        if i + 1 < len(col):
            gap = r["bottom"] - col[i + 1]["top"]
            if gap > EPS:
                out.append({"top": r["bottom"], "bottom": col[i + 1]["top"],
                            "value": (r["value"] + col[i + 1]["value"]) / 2,
                            "err": 0.0, "source": SRC_NEIGHBOR})
    # bottom edge: mirror of the top
    last = col[-1]
    if last["bottom"] > EPS:
        last["bottom"] = 0.0
        last["source"] = (SRC_EXT_BOTTOM if last["source"] == SRC_MEASURED
                          else last["source"])
    return out


def _bulk_swe(col):
    """(bulk kg/m3, swe mm) of a full column; None,None when empty."""
    st = sum(r["top"] - r["bottom"] for r in col)
    if st <= EPS:
        return None, None
    srt = sum(r["value"] * (r["top"] - r["bottom"]) for r in col)
    return srt / st, srt / 100.0


def analyze(density_rows, hs, layer_rows=None):
    """The whole pipeline. Returns a dict:
      column        gap-filled interval-mean column (drives the plot + CSV)
      bulk, swe     overall derived values (rule 10)
      profiles      {label: {'bulk','swe','coverage_pct'}} for measured profiles
      coverage      {label: pct} incl. 0 for wholly absent profiles (A/B always shown)
      layer_fallback  True when NO §5 interval densities existed and the
                      column was built from per-layer densities (§7) instead
                      — the agreed fallback: interval density always wins
                      when present; layers step in only when it is absent.
    Raises DensityValidationError for inverted intervals."""
    hs = _num(hs)
    cleaned = clean_intervals(density_rows)
    if hs is None or hs <= 0:
        # fall back to the highest measured top so derived values still exist
        hs = cleaned[0]["top"] if cleaned else None
    column = []
    for r in cleaned:
        mean, err, n = interval_value(r)
        if mean is None:
            continue                       # an entered interval with no values = a gap
        profs = "+".join(lbl for key, lbl in (("a", "A"), ("b", "B"), ("c", "Extra"))
                         if _num(r.get(key)) is not None and _num(r.get(key)) > 0)
        column.append({"top": r["top"], "bottom": r["bottom"], "value": mean,
                       "meas_top": r["top"], "meas_bottom": r["bottom"],
                       "err": err, "n": n, "profs": profs,
                       "a": _num(r.get("a")), "b": _num(r.get("b")), "c": _num(r.get("c")),
                       "source": SRC_CLIPPED if r["clipped"] else SRC_MEASURED})
    layer_fallback = False
    if not column and layer_rows:
        lrows = [r for r in layer_rows
                 if _num(r.get("layer_density")) is not None
                 and _num(r.get("layer_density")) > 0]
        if lrows:
            lcleaned = clean_intervals(lrows, "Stratigraphy")
            for r in lcleaned:
                v = _num(r.get("layer_density"))
                if v is None or v <= 0:
                    continue
                column.append({"top": r["top"], "bottom": r["bottom"], "value": v,
                               "meas_top": r["top"], "meas_bottom": r["bottom"],
                               "err": 0.0, "n": 1, "profs": "layer", "a": None, "b": None, "c": None,
                               "source": SRC_MEASURED + " (per-layer)"})
            layer_fallback = bool(column)
    filled = gap_fill(column, hs) if hs else []
    bulk, swe = _bulk_swe(filled)

    profiles = {}
    coverage = {}
    for key, label in (("a", "A"), ("b", "B"), ("c", "Extra")):
        prof = [{"top": r["top"], "bottom": r["bottom"], "value": _num(r.get(key)),
                 "err": 0.0, "source": SRC_CLIPPED if r["clipped"] else SRC_MEASURED}
                for r in cleaned
                if _num(r.get(key)) is not None and _num(r.get(key)) > 0]
        cov = (sum(p["top"] - p["bottom"] for p in prof) / hs * 100.0) if (hs and prof) else 0.0
        coverage[label] = round(cov, 1)
        if not prof:
            continue                       # wholly absent profile: no filling, no numbers
        pf = gap_fill(prof, hs) if hs else []
        pb, ps = _bulk_swe(pf)
        # the filled column itself is returned so exports can EXPOSE it —
        # a derived bulk_X that can't be reproduced from visible numbers
        # would break the provenance-first rule
        profiles[label] = {"bulk": pb, "swe": ps, "coverage_pct": round(cov, 1),
                           "column": pf}
    return {"column": filled, "bulk": bulk, "swe": swe,
            "profiles": profiles, "coverage": coverage, "hs": hs,
            "layer_fallback": layer_fallback}


def column_value_over(column, top, bottom):
    """Thickness-weighted value of a filled column over [bottom, top] — used
    to write per-profile gap-filled cells into the union table's rows (each
    profile's filled column has its own boundaries, so union rows are
    evaluated as overlap-weighted averages; usually constant over the row)."""
    num = den = 0.0
    for r in column:
        ov = min(top, r["top"]) - max(bottom, r["bottom"])
        if ov > EPS:
            num += r["value"] * ov
            den += ov
    return (num / den) if den > EPS else None
