# The CryoPit profile figure

The reference figure rendered by `cryopit/plot.py` is shown in §10 and written
into the pit archive as a PNG, with a vector PDF when PDF serialization
succeeds. Downloads also include the generated profile products when rendering
succeeds. This document is the authoritative description of the figure's
conventions, including CryoPit-specific display choices.

## Anatomy

The figure uses two panels sharing the height axis (surface at top, ground at
0):

* **Left - stratigraphy**: hand-hardness bars growing leftward on the
  quadratic scale F=1, 4F=2, 1F=5, P=10, K=17, I=26 (axis spans only the
  hardnesses present, plus annotation space); grain-type fill and symbols;
  the wetness strip at the panel's inner edge.
* **Right - density + temperature**: the gap-filled density column as
  horizontal bars, with the temperature curve on a secondary top axis
  (dotted 0 °C reference; axis bounds are isothermal-safe).

Temperature and density can be plotted before any complete stratigraphy layers
exist. In that state the figure is generated without stratigraphy. If there is
no plottable temperature, density, or complete stratigraphy data, the browser
shows an instructional empty state instead of requesting a profile render.
LWC is archived/exported but is not part of this figure.

The automatic title is `pit · location · date`. The Figure-title control in §10
can replace it or suppress the title entirely.

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

**MFcr** is special-cased: white fill with vertical striping and a black edge,
so a melt-freeze crust can never disappear white-on-white.

Grain symbols are the official IACS glyphs (bundled from the MIT-licensed
snowpyt package - `cryopit/assets/iacs/ATTRIBUTION.md`). Subtypes without their
own glyph fall back to the main-class symbol; the text code printed beside
every symbol keeps each layer unambiguous regardless. Codes for layers thinner
than 3 cm carry the wetness letter too: `IF (D)`.

## Wetness (CryoPit's own scale)

The ICSSG defines the classes D/M/W/V/S but assigns **no colors** - the
following scale is CryoPit's convention:

| Class | | Hex |
|---|---|---|
| D | Dry | `#ffffff` (white - "color means water") |
| M | Moist | `#4fc3f7` |
| W | Wet | `#1565c0` |
| V | Very wet | `#5e35b1` |
| S | Slush | `#37474f` |

Dry is deliberately **white**, so the whole system reads as one rule: color
means water - in the strip just as in the guides (dry layers cast no band).
Grey in the strip means wetness was not recorded. The strip is detached beside
the stratigraphy bars. Its letter is inscribed when the layer is at least 3 cm
thick; thinner layers carry the wetness letter in their annotation code instead.
The wetness legend lists only classes present in the pit.

**Wetness guides**: layers Moist and wetter also cast a faint band
(alpha 0.13) across the density/temperature panel at their exact extent, so the
wetness ↔ temperature relationship reads at a glance. The density axis keeps
12% headroom past the longest bar + whisker, guaranteeing the band stays
visible beside even a near-ice-density bar. Dry pits render with no guides.
Legend entry: "Moist+ layer", present only when guides are.

## Density styling (provenance vocabulary)

The panel draws the gap-filled column from `cryopit/density.py`; styling maps
one-to-one onto the `Source` tags in the `density_gap_filled` CSV:

| Style | Meaning | Source tag |
|---|---|---|
| White, hatched `/`, black edge, whisker | Measured (mean of the profiles measured on that interval; whisker = ± half-range, absent when only one profile) | `measured`, `measured (clipped)` |
| Hatched + grey dashed extension, **one whisker spanning the merged extent** | Nearest measured edge interval extended to HS / to 0 | `measured (extended to HS/0)` |
| Grey, dashed edge, no hatch, no whisker | Derived middle-gap interval | `gap-filled (neighbor-mean)` |

Source tags and derivations are documented in full in
[DENSITY.md](DENSITY.md).

## Figure size and output

The profile is **adaptive rather than fixed-size**. The base canvas is 9 × 7.2
inches. It grows with layer count, annotation-symbol height, and the thinnest
colored stratigraphy layers, up to 12 × 16 inches. This gives crowded profiles
more annotation room and preserves useful vertical resolution without changing
any measured layer boundary.

The archived PNG uses `CRYOPIT_FIGURE_DPI` (150 DPI by default; supported range
72–300). The on-screen preview remains fixed at 150 DPI. Because the canvas is
adaptive and the image is saved with a tight bounding box, there is no single
fixed PNG pixel dimension. The companion PDF is vector and should be preferred
when arbitrary publication scaling is needed.

## Small-print rules

* **True layer thickness**: stratigraphy bars are drawn at their measured top
  and bottom depths. CryoPit does not inflate thin layers to a minimum display
  thickness.
* **Thin colored layers**: the figure can grow vertically to preserve usable
  resolution. Where a very thin colored bar would otherwise be consumed by
  its fixed-width black boundary stroke, the boundary stroke is tapered rather
  than changing the bar height. Pale/white layers such as MFcr keep their full
  outline because the outline is necessary for visibility.
* **Annotation declutter**: symbol+code annotations for tightly packed layers
  are nudged apart vertically while preserving surface-to-ground order and
  staying inside the panel. Leader lines point back to the true layer midpoint.
* **Legends** live in the footer and only show what the pit contains.
