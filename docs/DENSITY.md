# Density and SWE: rules and derivations

The authoritative description of how CryoPit turns density measurements into
derived bulk density and SWE. The guiding principle throughout:
**measurement and derivation are never mixed** — the database stores measured
values only, every derived number is recomputable, and every derived value is
labeled as such wherever it appears.

## Inputs

* **§5 interval densities**: up to three cutter profiles (A, B, Extra =
  Profile C) over depth intervals `top → bottom` (cm above ground).
* **§7 per-layer densities** (optional): one density per stratigraphy layer.
* **HS**: total depth (§1).

Values must lie in 1–917 kg/m³ (ice); zero/negative values are blocked and
never enter any average.

## Interval value

An interval's density is the arithmetic mean of whichever profiles were
actually measured on it (its "measured mean"). Profiles are **never
synthesized**: a missing Profile B on one interval stays missing.
Mean ± half-range is what the figure's whiskers show.

## Geometry cleanup (derived products only)

Applied before any filling; the verbatim `density` CSV always keeps exactly
what was entered.

1. Intervals sort surface → ground regardless of entry order.
2. **Inverted intervals** (top ≤ bottom) are a blocking error.
3. **Overlaps** are clipped, the upper interval winning; an interval
   swallowed whole is dropped. Noted in `Source` as `measured (clipped)`.

## Vertical gap filling

Whole missing intervals only — depth is never invented, only density:

| Gap | Rule | Source tag |
|---|---|---|
| Middle | mean of the intervals directly above and below | `gap-filled (neighbor-mean)` |
| Top ≤ 25 % of HS | top interval extends to HS (same measurement, larger weight) | `measured (extended to HS)` |
| Bottom ≤ 25 % of HS | bottom interval extends to 0 | `measured (extended to 0)` |
| Edge > 25 % of HS | thickness-weighted mean of all measured intervals | `gap-filled (mean-fallback)` |

## Derived bulk density and SWE

Reported in the **density CSV header** (marked `# Derived`) and in
siteDetails:

    bulk = Σ(ρ·t) / Σ(t)        SWE [mm] = Σ(ρ·t[cm]) / 100

computed over the full gap-filled column — i.e. **using the measured interval
means wherever they exist, and gap-filled values only for intervals with no
measurements**. Measured coverage (% of HS covered by measured intervals,
post-clip) is reported alongside.

**Per-profile** `bulk_X` / `SWE_X` (gap-filled CSV header): profile X's own
column run through the same rules; a wholly absent profile is skipped. Their
header labels carry `[NN% measured; gaps filled per rules]` whenever gaps
were filled.

## The `density_gap_filled` CSV

**The fully-filled mirror of the density CSV**: the two files are a pair —
`density` is the raw record with `-9999` holes, `density_gap_filled` is the
same table with every hole filled.

* **Profile cells** hold measured values where measured, and that profile's
  OWN gap-filled column elsewhere (each profile's column is filled
  independently by the same rules; union rows are evaluated as
  overlap-weighted averages). A profile with no measurements anywhere is
  omitted — there is nothing to fill it from.
* **Density (kg/m3)**: the interval column — measured mean where one
  exists, gap-filled value otherwise. This is what the figure plots and what
  the overall Derived Bulk/SWE use.
* **Source** lists which profiles were measured on the row
  (`measured [A+B measured]`); anything not listed is gap-filled. Geometry
  events (clipping, edge extensions) appear here too.

## Per-layer fallback

When **no** §5 interval densities exist at all, the column is built from §7
per-layer densities instead — `Source` reads `measured (per-layer)`, the CSV
header notes it, and the live rail labels its numbers `· layer ρ`. Interval
densities always win when present. Per-layer densities are otherwise
recording-only: they export in the stratigraphy CSV (always-present column,
`-9999` when absent) and never enter the interval computation.

## Live rail parity

The in-app rail computes SWE/bulk by these same rules (clipping, means,
gap filling, the 25 % guards), labels gap-filled centimeters
(`est · 12 cm gap-filled`), and excludes intervals that fail geometry bounds.
