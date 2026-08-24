# The CryoPit profile figure

The reference figure rendered by `cryopit/plot.py` — shown in §10, saved with
every Archive (`…_profile_v01_0.png`), and included in every Download zip.
This document is the authoritative description of its conventions, including
the ones CryoPit invented and therefore owes the reader an explanation for.

## Anatomy

Two panels sharing the height axis (surface at top, ground at 0):

* **Left — stratigraphy**: hand-hardness bars growing leftward on the
  quadratic scale F=1, 4F=2, 1F=5, P=10, K=17, I=26 (axis spans only the
  hardnesses present, plus annotation space); grain-type fill and symbols;
  the wetness strip at the panel's inner edge.
* **Right — density + temperature**: the gap-filled density column as
  horizontal bars, with the temperature curve on a secondary top axis
  (dotted 0 °C reference; axis bounds are isothermal-safe).

The title is `pit · location · date`, overridable per pit via the optional
Figure-title field in §10.

## Grain types (official ICSSG)

Layer fill uses the official ICSSG main-class colors (Fierz et al., 2009);
subtypes inherit their main class via the two-letter prefix:

| Class | Hex | | Class | Hex |
|---|---|---|---|---|
| PP | `#00FF00` | | DH | `#0000FF` |
| MM | `#FFD700` | | SH | `#FA00FF` |
| DF | `#228B22` | | MF | `#FF0000` |
| RG | `#FFB6C1` | | IF | `#00FFFF` |
| FC | `#ADD8E6` | | | |

**MFcr** is special-cased: white fill with vertical striping and a black
edge, so a melt-freeze crust can never disappear white-on-white.

Grain symbols are the official IACS glyphs (bundled from the MIT-licensed
snowpyt package — `cryopit/assets/iacs/ATTRIBUTION.md`). Subtypes without
their own glyph fall back to the main-class symbol; the text code printed
beside every symbol keeps each layer unambiguous regardless. Codes for
layers thinner than 3 cm carry the wetness letter too: `IF (D)`.

## Wetness (CryoPit's own scale)

The ICSSG defines the classes D/M/W/V/S but assigns **no colors** — the
following scale is CryoPit's convention:

| Class | | Hex |
|---|---|---|
| D | Dry | `#ffffff` (white — "color means water") |
| M | Moist | `#4fc3f7` |
| W | Wet | `#1565c0` |
| V | Very wet | `#5e35b1` |
| S | Slush | `#37474f` |

Dry is deliberately **white**, so the whole system reads as one rule: color
means water — in the strip just as in the guides (dry layers cast no band).
Grey in the strip means wetness was not recorded. Shown as a detached strip
beside the stratigraphy bars (letter inscribed
when the layer is ≥ 3 cm), with a legend listing only the classes present.

**Wetness guides**: layers Moist and wetter also cast a faint band
(alpha 0.13) across the density/temperature panel at their exact extent, so
the wetness ↔ temperature relationship reads at a glance. The density axis
keeps 12 % headroom past the longest bar + whisker, guaranteeing the band
stays visible beside even a near-ice-density bar. Dry pits render with no
guides. Legend entry: "Moist+ layer", present only when guides are.

## Density styling (provenance vocabulary)

The panel draws the gap-filled column from `cryopit/density.py`; styling maps
one-to-one onto the `Source` tags in the `density_gap_filled` CSV:

| Style | Meaning | Source tag |
|---|---|---|
| White, hatched `/`, black edge, whisker | Measured (mean of the profiles measured on that interval; whisker = ± half-range, absent when only one profile) | `measured`, `measured (clipped)` |
| Hatched + grey dashed extension, **one whisker spanning the merged extent** | Nearest measured edge interval extended to HS / to 0 | `measured (extended to HS/0)` |
| Grey, dashed edge, no hatch, no whisker | Derived middle-gap interval | `gap-filled (neighbor-mean)` |

Source tags and derivations are documented in full in
[docs/DENSITY.md](DENSITY.md).

## Small-print rules

* **Thin layers**: anything thinner than 1 % of HS is *drawn* at that floor
  so it stays visible; data and boundaries untouched; leader lines point to
  the true midpoint.
* **Annotation declutter**: symbol+code annotations for tightly packed
  layers are nudged apart vertically, connected to their layers by leader
  lines.
* **Legends** live in the footer and only ever show what the pit contains.
