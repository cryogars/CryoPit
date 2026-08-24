"""Server-side snow-profile figure (the CryoPit reference figure).

render_profile(payload) -> PNG bytes. Two panels sharing the height axis:
stratigraphy (hand hardness, ICSSG grain colors/symbols, wetness strip) and
density + temperature. The density panel draws the GAP-FILLED interval-mean
column from cryopit.density: measured intervals hatched with mean±half-range
whiskers (whiskers span the full extent of edge-extended intervals);
gap-filled intervals dashed, grey, whisker-free.

Thread-safety: uses the object-oriented Figure/Agg API only (no pyplot
global state), so it is safe under waitress's thread pool. IACS grain symbol
images (assets/iacs, MIT — see ATTRIBUTION.md) are cached at import scope.
"""
import io
import os

import matplotlib
matplotlib.use("Agg")
from matplotlib.figure import Figure
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.patches import Patch
from matplotlib.lines import Line2D
from matplotlib.offsetbox import OffsetImage, AnnotationBbox
from matplotlib import image as mpimage, rcParams
from matplotlib.colors import to_rgba

from .density import analyze, SRC_MEASURED, SRC_CLIPPED, SRC_EXT_TOP, SRC_EXT_BOTTOM

rcParams["hatch.linewidth"] = 0.6

# Official ICSSG main-class colors (Fierz et al., 2009). Subtypes inherit
# their main class (2-letter prefix). MFcr is special-cased: white with
# vertical striping + black edge, so a crust can never vanish white-on-white.
ICSSG_COLOR = {"PP": "#00FF00", "MM": "#FFD700", "DF": "#228B22", "RG": "#FFB6C1",
               "FC": "#ADD8E6", "DH": "#0000FF", "SH": "#FA00FF", "MF": "#FF0000",
               "IF": "#00FFFF"}

# CryoPit grain code -> bundled IACS symbol image. Codes without their own
# glyph fall back to the main class via prefix; the printed text code keeps
# every layer unambiguous either way.
IACS_SYMBOL = {
    "PP": "recent_snow", "PPsd": "recent_snow", "PPgp": "recent_snow", "PPrm": "recent_snow",
    "MM": "rounded_polycrystals",
    "DF": "partly_decomposed",
    "RG": "large_rounded", "RGwp": "wind_packed", "RGxf": "faceted_rounded", "RGlr": "large_rounded",
    "FC": "faceted", "FCsf": "near_surface_faceted", "FCxr": "rounding_faceted", "FCso": "faceted",
    "DH": "hollow_cups", "DHcp": "hollow_cups", "DHpr": "hollow_prism",
    "DHla": "hollow_cups", "DHxr": "rounding_depth_hoar",
    "SH": "surface_hoar", "SHxr": "rounding_surface_hoar",
    "MF": "cluster_rounded", "MFcl": "cluster_rounded", "MFsl": "slush", "MFcr": "melt_freeze_crust",
    "IF": "ice", "IFsc": "sun_crust", "IFrc": "rain_crust", "IFbi": "basal_ice",
}

# D is WHITE by design: "color means water" everywhere — a dry segment
# shows none in the strip just as it casts no band in the guides.
# (Grey remains the wetness-not-recorded fallback, now clearly distinct.)
WET_COLOR = {"D": "#ffffff", "M": "#4fc3f7", "W": "#1565c0", "V": "#5e35b1", "S": "#37474f"}
WET_LABEL = {"D": "Dry", "M": "Moist", "W": "Wet", "V": "Very wet", "S": "Slush"}
WET_TEXT = {"D": "black", "M": "black", "W": "white", "V": "white", "S": "white"}

HH_ORDER = ["F", "4F", "1F", "P", "K", "I"]
HH_VALUE = {h: i ** 2 + 1 for i, h in enumerate(HH_ORDER)}   # quadratic hardness scale

GAPFILL_FC, GAPFILL_EC = "#f2f2f2", "#333333"
SYMBOL_PX = 16          # rendered symbol size
CODE_FONTSIZE = 9

# Layer boundaries are the only thing separating two consecutive layers of the
# same grain type, so they are drawn as a full-strength black line rather than
# an alpha-faded one.
EDGE_LW = 1.3

_ASSET_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "iacs")
_IMG_CACHE = {}


def _grain_color(code):
    return ICSSG_COLOR.get(code, ICSSG_COLOR.get((code or "")[:2], "grey"))


def _symbol_image(code):
    name = IACS_SYMBOL.get(code) or IACS_SYMBOL.get((code or "")[:2])
    if not name:
        return None
    if name not in _IMG_CACHE:
        path = os.path.join(_ASSET_DIR, name + ".png")
        _IMG_CACHE[name] = mpimage.imread(path) if os.path.exists(path) else None
    img = _IMG_CACHE[name]
    return OffsetImage(img, zoom=SYMBOL_PX / img.shape[1]) if img is not None else None


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None


def _declutter(mids, min_sep, lo=None, hi=None):
    """Place annotation labels so they never overlap, stay in surface->ground
    order, and stay INSIDE [lo, hi].

    The old version walked the list from the surface down and pushed each label
    that was too close to its predecessor further down. Two consequences, both
    visible on a real pit:

      * All displacement accumulated downward. Every label absorbed the sum of
        the crowding above it, so the deepest labels drifted furthest from the
        layers they name while the shallow ones did not move at all.

      * There was no floor. Capacity was exactly axis/min_sep labels; beyond
        that the cascade simply ran off the bottom of the plot. Those text
        artists still count for layout, so tight_layout() shrank the whole
        stratigraphy panel to make room for them — 689 px of panel at 18 layers
        became 149 px at 40, which made EVERY layer a hairline, not just the
        thin ones. That, not thin layers as such, was the real failure.

    This is the same problem as isotonic regression. Substituting
    u_i = y_i + i*min_sep turns "consecutive labels at least min_sep apart"
    into "u is non-increasing", and pool-adjacent-violators then finds the
    arrangement with the smallest total squared movement from where the labels
    wanted to be. Crowded runs settle around their own centre of mass instead
    of being shoved one way, so displacement is shared rather than accumulated.

    When even min_sep * (n-1) will not fit between lo and hi, the separation is
    reduced to what does fit. Labels crowd — that is unavoidable — but they
    crowd inside the panel instead of destroying it.
    """
    n = len(mids)
    if n == 0:
        return []
    order = sorted(range(n), key=lambda k: -mids[k])    # surface -> ground
    sep = float(min_sep)
    if lo is not None and hi is not None and n > 1:
        sep = min(sep, (hi - lo) / (n - 1))
    sep = max(sep, 0.0)

    want = [mids[order[i]] + i * sep for i in range(n)]
    # PAVA for a non-increasing sequence: merge any block that violates the
    # order into its weighted mean.
    blocks = []                                     # [value, count]
    for x in want:
        cur = [x, 1]
        while blocks and blocks[-1][0] < cur[0]:
            v, c = blocks.pop()
            cur = [(v * c + cur[0] * cur[1]) / (c + cur[1]), c + cur[1]]
        blocks.append(cur)
    u = []
    for v, c in blocks:
        u.extend([v] * c)
    ys = [u[i] - i * sep for i in range(n)]

    # Slide the whole stack back inside the axis. It fits by construction after
    # the sep reduction above, so one shift can never push the other end out.
    if lo is not None and min(ys) < lo:
        ys = [y + (lo - min(ys)) for y in ys]
    if hi is not None and max(ys) > hi:
        ys = [y - (max(ys) - hi) for y in ys]

    out = [0.0] * n
    for i, k in enumerate(order):
        out[k] = ys[i]
    return out


def render_profile(payload, dpi=150, fmt="png"):
    """Render the profile figure for a pit payload.

    ``fmt`` may be ``"png"``, ``"pdf"``, or ``"both"``. The combined mode
    constructs the Matplotlib figure once and writes both archive formats from
    that same figure, avoiding a second full profile construction. It returns
    ``(png_bytes, pdf_bytes_or_none)``; PDF remains best-effort exactly as in
    the archive workflow.

    Raises cryopit.density.DensityValidationError on inverted intervals.
    """
    if fmt not in {"png", "pdf", "both"}:
        raise ValueError(f"Unsupported profile format: {fmt!r}")
    m = payload.get("meta") or {}
    strat = [r for r in (payload.get("stratigraphy") or [])
             if _num(r.get("top")) is not None and _num(r.get("bottom")) is not None]
    strat.sort(key=lambda r: -_num(r["top"]))
    temp = [r for r in (payload.get("temperature") or [])
            if _num(r.get("height")) is not None and _num(r.get("temp")) is not None]
    temp.sort(key=lambda r: -_num(r["height"]))

    hs = _num(m.get("total_depth"))
    if not hs:
        hs = max([_num(r["top"]) for r in strat] or [0]) or 100.0
    den = analyze(payload.get("density") or [], hs,
                  payload.get("stratigraphy"))

    # FIGURE SIZE grows with the number of layers.
    #
    # It used to be a fixed 9 x 7.2 in, which fixed the annotation lane's
    # capacity too: labels need a minimum vertical pitch, so a fixed height is a
    # fixed number of labels no matter how deep the pit. A 40-layer pit had
    # nowhere to put them. Snow profiles are naturally tall documents; letting
    # the figure grow is the honest answer and it buys back px/cm for the thin
    # layers at the same time. The width follows gently so a 16-inch figure does
    # not come out as a ribbon.
    n_lay = len(strat)
    # LANE PITCH comes from the tallest symbol ACTUALLY USED in this pit.
    #
    # _symbol_image() sets its zoom from SYMBOL_PX / image WIDTH, so the 16 px
    # is a width, not a height: a tall glyph renders proportionally taller. The
    # ice column is 212x507, so it comes out 38 px — two and a half times what a
    # pitch derived from SYMBOL_PX assumes. Measured on a 17-layer pit with ice
    # layers, eight pairs of symbols overlapped by up to 14 px while the text
    # labels beside them sat perfectly spaced, because only the text had been
    # accounted for.
    # SYMBOL_PX behaves as POINTS, not pixels: OffsetImage draws the array at
    # zoom * dpi/72, so a symbol set to 16 occupies 16 pt of page and its height
    # follows the glyph's aspect ratio. Dividing by 72 rather than 100 is what
    # makes the pitch resolution-independent and correct in both the PNG and the
    # vector PDF.
    sym_h_pt = 0.0
    for _code in {(r.get("gtype") or "") for r in strat}:
        _oi = _symbol_image(_code)
        if _oi is not None:
            _img = _oi.get_data()
            sym_h_pt = max(sym_h_pt, SYMBOL_PX * _img.shape[0] / max(_img.shape[1], 1))
    lane_in = max(sym_h_pt / 72.0, CODE_FONTSIZE * 1.35 / 72.0) + 0.035

    # The figure then grows to fit that pitch, not a fixed guess: a pit of tall
    # ice-column symbols needs more page than the same number of rounded grains.
    #
    # The 1.5 is slack, and it is what keeps labels ON their layers. At 1.06 the
    # lane is exactly full, so the placer has to spread labels evenly and a
    # clustered pit drags them up to 12 cm from the layer they name. Measured on
    # a 17-layer pit: 1.06 -> 11.8 cm worst drift, 1.35 -> 5.9, 1.50 -> 4.2, and
    # past that the page grows faster than the drift falls.
    fig_h = min(16.0, max(7.2, n_lay * lane_in * 1.50 + 2.6))

    # PAGE HEIGHT ALSO BUYS VERTICAL RESOLUTION, not just lane room.
    #
    # Halving the lane pitch let the page shrink, and that quietly took the
    # thin layers down with it: a 1 cm layer needs a couple of points of bar
    # left after its boundary stroke or its grain colour is gone. So the height
    # is the larger of what the lane needs and what the thinnest COLOURED layer
    # needs. Pale layers (MFcr, and anything else drawn white) are excluded
    # because they keep their full outline at any thickness and read as a dark
    # line — they are not competing for pixels.
    _thin = [(_num(r["top"]) - _num(r["bottom"])) for r in strat
             if (r.get("gtype") or "") != "MFcr"
             and _grain_color(r.get("gtype") or "") != "grey"
             and (_num(r["top"]) - _num(r["bottom"])) > 0]
    if _thin:
        _span = (hs * 1.07 + 2) - (-3)
        _axes_needed = 3.2 * _span / (72.0 * min(_thin))
        fig_h = min(16.0, max(fig_h, _axes_needed + 2.6))
    fig_w = min(12.0, 9.0 + 0.30 * (fig_h - 7.2))
    fig = Figure(figsize=(fig_w, fig_h))

    # Axis geometry, needed by both the bar loop (hairline edge taper) and the
    # annotation lane (label pitch). Declared once, here, so the two cannot
    # disagree about how tall the panel is.
    y_lo, y_hi = -3, hs * 1.07 + 2
    axes_in = max(2.0, fig_h - 2.6)      # panel height after title/labels/legend
    FigureCanvasAgg(fig)
    ax3, ax1 = fig.subplots(1, 2, sharey=True,
                            gridspec_kw={"width_ratios": [1.15, 1]})
    # the auto title (pit · location · date · recorder) can be overridden by
    # an optional custom figure title from §10
    # Three states, and they are genuinely different:
    #   figure_title missing/None -> auto title from pit · location · date
    #   figure_title == ""        -> the user ticked "No title": draw none
    #   figure_title == "text"    -> use it verbatim
    # An `or` chain cannot express this, because "" and None are both falsy.
    ft = m.get("figure_title")
    if ft is not None and str(ft).strip() == "":
        title = ""
    else:
        title = (str(ft).strip() if ft else "") or " · ".join(
            x for x in [m.get("pit_id"), m.get("location"), m.get("date")] if x)
    if title:
        fig.suptitle(title, fontsize=13, y=0.97)

    # ---- stratigraphy panel -------------------------------------------------
    hmax = max([HH_VALUE.get(r.get("hardness"), 1) for r in strat] or [1])
    pad = 8.5
    strip_l, strip_w = -0.30, -1.45
    # HAIRLINE EDGE TAPER.
    #
    # The boundary stroke is a fixed width in points while the bar it outlines
    # is a fixed height in CENTIMETRES, so on a thin layer the two collide: a
    # 0.5 cm crust in a 130 cm pit is about 2.8 px tall at 150 dpi and its two
    # 1.3 pt edges are about 2.7 px between them. The stroke eats the bar, the
    # layer renders as a black line, and the ICSSG colour — the thing that says
    # WHICH crust it is — is gone. Twelve stacked crusts became one black smear
    # while the annotation lane still cheerfully labelled all twelve.
    #
    # Raising dpi does not help: the stroke scales with it, so the ratio is
    # unchanged. Neither does the taller figure, for a 0.5 cm layer in a deep
    # pit — that would need a 40-inch page.
    #
    # So the stroke gives way instead, in POINTS so it behaves the same in the
    # PNG and the vector PDF. Thick layers keep the full boundary, which is
    # doing real work separating two layers of the same grain type. Below about
    # 2 pt of bar the edge tapers, and below ~1.2 pt it goes altogether and the
    # layer is drawn as pure colour.
    #
    # Position is untouched: only the STROKE changes, never the height or the
    # midpoint, so layer boundaries still line up with the density intervals
    # across the shared y-axis, and nothing below a thin layer shifts.
    pt_per_cm = (axes_in * 72.0) / max(y_hi - y_lo, 1e-9)

    def _pale(colour):
        """True when a face is too light to be seen without its outline."""
        r, g, b, _a = to_rgba(colour)
        return (0.299 * r + 0.587 * g + 0.114 * b) > 0.93

    def _edge_lw(thickness_cm, face, base=EDGE_LW):
        # A PALE FACE KEEPS ITS FULL OUTLINE, however thin the layer.
        #
        # MFcr is drawn white with a hatch, so its black edge IS the layer, and
        # the "D" wetness strip is white for the same reason. Taper those and a
        # hairline crust does not merely lose its colour — it disappears
        # completely, which is worse than the black smear the taper exists to
        # cure. MFcr is also the grain type most often only a centimetre thick,
        # so this is the common case, not a corner. A thin crust drawn as a dark
        # line is the honest rendering: the hatch cannot resolve at three pixels
        # anyway.
        if _pale(face):
            return base
        th_pt = thickness_cm * pt_per_cm
        if th_pt >= 2.0 + base:
            return base
        return max(0.0, min(base, (th_pt - 1.2) / 2.0))

    for r in strat:
        top, bottom = _num(r["top"]), _num(r["bottom"])
        mid, th = (top + bottom) / 2, top - bottom
        # TRUE HEIGHT, always (user/community decision): a 0.3 cm crust is
        # drawn at 0.3 cm. Hairlines stay identifiable because grain symbols
        # and codes live in the annotation lane with leader lines to the true
        # midpoint, and wetness letters for <3 cm layers ride the lane code.
        dth = th
        hh = HH_VALUE.get(r.get("hardness"), 1)
        code = r.get("gtype") or ""
        if code == "MFcr":
            ax3.barh(mid, hh, height=dth, left=0, color="white", hatch="|||",
                     edgecolor="black", lw=_edge_lw(th, "white"), zorder=3)
        else:
            # alpha goes on the FACE colour only. Passing alpha= to barh fades
            # the edge as well, which turned the black boundary grey — and two
            # consecutive layers of the same grain type then read as one block,
            # because the only thing separating them is that shared edge.
            face = to_rgba(_grain_color(code), 0.7)
            ax3.barh(mid, hh, height=dth, left=0, color=face,
                     edgecolor="black", lw=_edge_lw(th, face), zorder=3)
        wet = (r.get("wetness") or "").strip()
        wface = WET_COLOR.get(wet, "lightgrey")
        ax3.barh(mid, strip_w, height=dth, left=strip_l, color=wface,
                 edgecolor="black", lw=_edge_lw(th, wface, EDGE_LW * 0.8), zorder=3)
        if wet and th >= 3.0:
            ax3.text(strip_l + strip_w / 2, mid, wet, ha="center", va="center",
                     fontsize=7.5, fontweight="bold", color=WET_TEXT.get(wet, "black"))
        # Wetness guides (moist and wetter): a faint band behind the density
        # panel at the layer's exact extent. The 12% density-axis headroom
        # guarantees a band sliver stays visible past even a near-ice bar.
        # Dry pits render with no guides at all.
        if wet in ("M", "W", "V", "S"):
            ax1.axhspan(bottom, top, color=WET_COLOR[wet], alpha=0.13, zorder=0)
    ax3.set_xlim(hmax + pad, strip_l + strip_w - 0.2)
    ticks = [v for h, v in HH_VALUE.items() if v <= hmax]
    ax3.set_xticks(sorted(ticks))
    ax3.set_xticklabels([h for h in HH_ORDER if HH_VALUE[h] <= hmax])
    ax3.grid(axis="x", ls=":", alpha=0.5)
    ax3.plot([0, 0], [0, -0.085], transform=ax3.get_xaxis_transform(),
             color="#555", lw=1.2, clip_on=False)
    # centered over the FULL zone left of the divider (bars + annotation
    # lane), not just the bar span — an all-F pit (hmax=1) would otherwise
    # push the label into the divider and the Wetness label
    _hh_label = ax3.text((hmax + pad) / 2, -0.062, "Hand Hardness",
                         transform=ax3.get_xaxis_transform(), ha="center",
                         va="top", fontsize=11)
    # vertical, larger wetness label alongside the strip
    ax3.text(strip_l + strip_w / 2, -0.038, "Wetness",
             transform=ax3.get_xaxis_transform(), ha="center", va="top",
             fontsize=11.5, rotation=90, style="italic", color="#333")
    ax3.yaxis.tick_right()
    ax3.yaxis.set_label_position("right")
    ax3.set_ylim(-3, hs * 1.07 + 2)

    mids = [( _num(r["top"]) + _num(r["bottom"])) / 2 for r in strat]

    # LANE GEOMETRY, MEASURED.
    #
    # The lane used to sit at fixed fractions of `pad` (0.40 and 0.80), which is
    # a guess about how much room a symbol and a code need. It is wrong by a
    # factor of four between pits, because hardness sets the x-range: the same
    # code is one data unit wide on a soft pit and four on one containing ice.
    # One draw gives a renderer, and the symbol width, the code width and the
    # gaps are then read off the axis actually being drawn.
    #
    # A two-column lane was built and measured as an alternative (it halves the
    # pitch and returns the page to 7.2 in, at 2.04 cm of drift). Single column
    # on a taller page was preferred: it keeps one unbroken line of symbols
    # down the side of the profile, which is how a snow-pit sheet is read.
    fig.canvas.draw()
    _r0 = fig.canvas.get_renderer()
    _inv0 = ax3.transData.inverted()
    _dx_per_px = abs(_inv0.transform((1, 0))[0] - _inv0.transform((0, 0))[0])
    # Empty stratigraphy is a normal in-progress state.  Keep the renderer
    # defensive even though the browser avoids requesting a completely blank
    # profile: temperature/density-only profiles must still render, and direct
    # callers must never trip max() on an empty layer list.
    _longest = max((f"{(r.get('gtype') or '')} (W)" for r in strat), key=len, default="")
    _probe = ax3.text(0, 0, _longest, fontsize=CODE_FONTSIZE)
    _pb = _probe.get_window_extent(_r0)
    code_w = abs(_inv0.transform((_pb.x1, 0))[0] - _inv0.transform((_pb.x0, 0))[0])
    _probe.remove()
    sym_w = SYMBOL_PX * (fig.dpi / 72.0) * _dx_per_px
    gap = 4.0 * (fig.dpi / 72.0) * _dx_per_px          # 4 pt between elements

    # data x grows leftwards (the axis is inverted), so each successive slot
    # sits further from the bars
    lane_s = hmax + gap + sym_w / 2                 # symbol
    lane_t = lane_s + sym_w / 2 + gap + code_w / 2  # code beside it
    # MINIMUM LABEL PITCH, in centimetres of snow.
    #
    # This was hs * 0.050 — five per cent of the axis. That is a constant
    # FRACTION of the panel, so capacity was pinned at 20 labels for every pit
    # ever drawn, however deep and however tall the figure. What actually sets
    # the pitch is how much room a symbol and its code need on paper, so it is
    # derived from those instead and converted into data units through the axis
    # that is really being drawn. Now that the figure grows with layer count
    # (above), capacity grows with it.
    min_sep = (y_hi - y_lo) * lane_in / axes_in
    _lane_texts = []
    for r, y in zip(strat, _declutter(mids, min_sep, y_lo, y_hi)):
        code = r.get("gtype") or ""
        oi = _symbol_image(code)
        if oi is not None:
            # pad=0: AnnotationBbox otherwise adds 0.4 * font size of padding on
            # every side. With frameon=False that padding draws nothing, but it
            # still occupies space, so the symbol's real footprint was about
            # 8 pt taller than the glyph and the lane pitch could not be
            # predicted from the image alone.
            ax3.add_artist(AnnotationBbox(oi, (lane_s, y), frameon=False, pad=0.0))
        # layers too thin to inscribe their wetness letter in the strip get it
        # appended to the lane code instead: "IF (D)"
        th_true = _num(r["top"]) - _num(r["bottom"])
        wet = (r.get("wetness") or "").strip()
        lane_code = f"{code} ({wet})" if (wet and th_true < 3.0) else code
        _lane_texts.append(ax3.text(lane_t, y, lane_code, ha="center", va="center",
                                    fontsize=CODE_FONTSIZE, color="black"))
        mid = (_num(r["top"]) + _num(r["bottom"])) / 2
        ax3.plot([HH_VALUE.get(r.get("hardness"), 1) + 0.3, lane_s - sym_w / 2 - gap * 0.4],
                 [mid, y], color="gray", lw=0.5, alpha=0.55)

    # WIDEN THE PANEL TO FIT THE LANE.
    #
    # `pad` is a fixed 8.5 data units, which leaves the lane code 1.7 units of
    # room either side of its centre. That is enough for "MFcr" but not for
    # "MFcr (D)" — the wetness suffix a thin layer gets when its strip is too
    # narrow to letter — so the widest labels crossed the panel border by about
    # 5 px. The labels the suffix is added to are exactly the thin ones, so the
    # overflow appeared precisely on the pits that were already crowded.
    #
    # Measured rather than estimated: one draw gives a renderer, the text
    # extents are converted back into data units, and the limit is set to hold
    # them. Guessing a character width would break on the next long code.
    fig.canvas.draw()
    _rend = fig.canvas.get_renderer()
    _inv = ax3.transData.inverted()
    _need = hmax + pad
    for _t in _lane_texts:
        _bb = _t.get_window_extent(_rend)
        # the x-axis is inverted, so the box's SMALLEST pixel x is its largest
        # data x — the edge that can escape the panel
        _need = max(_need, _inv.transform((_bb.x0, _bb.y0))[0] + 0.35)
    if _need > hmax + pad + 1e-9:
        ax3.set_xlim(_need, strip_l + strip_w - 0.2)
        _hh_label.set_x(_need / 2)      # keep "Hand Hardness" centred under it

    # ---- density + temperature panel ---------------------------------------
    measured_like = (SRC_MEASURED, SRC_CLIPPED, SRC_EXT_TOP, SRC_EXT_BOTTOM)
    for r in den["column"]:
        mid, th = (r["top"] + r["bottom"]) / 2, r["top"] - r["bottom"]
        src = r["source"]
        if src in measured_like or src.startswith("measured"):
            # measured stretch hatched; any edge-extension stretch shaded;
            # ONE whisker at the merged midpoint spanning the applied extent
            meas_top = r.get("meas_top", r["top"])
            meas_bot = r.get("meas_bottom", r["bottom"])
            ax1.barh((meas_top + meas_bot) / 2, r["value"], height=meas_top - meas_bot,
                     edgecolor="black", color="white", hatch="/", lw=1.0)
            if meas_top < r["top"] - 1e-9:      # top extension
                ax1.barh((r["top"] + meas_top) / 2, r["value"], height=r["top"] - meas_top,
                         edgecolor=GAPFILL_EC, color=GAPFILL_FC, lw=1.1, ls="--")
            if meas_bot > r["bottom"] + 1e-9:   # bottom extension
                ax1.barh((meas_bot + r["bottom"]) / 2, r["value"], height=meas_bot - r["bottom"],
                         edgecolor=GAPFILL_EC, color=GAPFILL_FC, lw=1.1, ls="--")
            if r.get("err"):
                ax1.errorbar(r["value"], mid, xerr=r["err"], fmt="none",
                             ecolor="black", capsize=4, lw=1.2)
        else:
            ax1.barh(mid, r["value"], height=th, edgecolor=GAPFILL_EC,
                     color=GAPFILL_FC, lw=1.1, ls="--")
    xmax = max([r["value"] + (r.get("err") or 0) for r in den["column"]] or [100])
    ax1.set_xlim(0, xmax * 1.12)   # headroom: bars never touch the right edge
    ax1.set_xlabel(r"Density [$kg\,m^{-3}$]", color="black")
    ax1.set_ylabel("Height above ground [cm]", color="black")

    ax2 = ax1.twiny()
    if temp:
        ax2.plot([r["temp"] for r in temp], [r["height"] for r in temp],
                 color="red", marker="o", ms=4)
        tmin = min(r["temp"] for r in temp)
        ax2.set_xlim(min(tmin - 1, -1), 0.5)
    else:
        ax2.set_xlim(-1, 0.5)
    ax2.axvline(0, color="red", ls=":", alpha=0.5)
    ax2.set_xlabel("Temperature [°C]", color="black")
    ax2.grid(axis="x", ls="--", alpha=0.4)

    # ---- footer legends (only wetness classes present) ----------------------
    present = [w for w in ["D", "M", "W", "V", "S"]
               if any((r.get("wetness") or "").strip() == w for r in strat)]
    wet_leg = [Patch(facecolor=WET_COLOR[w], edgecolor="black",
                     label=f"{w} — {WET_LABEL[w]}") for w in present]
    any_filled = any(
        (not r["source"].startswith("measured"))
        or r.get("meas_top", r["top"]) != r["top"]
        or r.get("meas_bottom", r["bottom"]) != r["bottom"]
        for r in den["column"])
    den_leg = [Patch(facecolor="white", edgecolor="black", hatch="//",
                     label="Density (measured, mean ± half-range)")]
    if any_filled:   # only claim gap-filling when the figure actually shows some
        den_leg.append(Patch(facecolor=GAPFILL_FC, edgecolor=GAPFILL_EC, ls="--",
                             label="Gap-filled / extended interval"))
    den_leg.append(Line2D([], [], color="red", marker="o", ms=4, label="Temperature"))
    if any((r.get("wetness") or "").strip() in ("M", "W", "V", "S") for r in strat):
        den_leg.append(Patch(facecolor=WET_COLOR["W"], alpha=0.25,
                             label="Moist+ layer"))
    if wet_leg:
        fig.legend(handles=wet_leg, loc="lower left", bbox_to_anchor=(0.02, -0.035),
                   ncol=len(wet_leg), fontsize=8, title="Wetness (strip)",
                   title_fontsize=8, frameon=False)
    fig.legend(handles=den_leg, loc="lower right", bbox_to_anchor=(0.99, -0.055),
               ncol=1, fontsize=8, frameon=False)
    fig.tight_layout(rect=[0, 0.02, 1, 1])

    def _save_pdf():
        # PDF is vector: this figure is line art (bars, rules, text, a few
        # markers), so it stores as instructions rather than pixels. dpi is
        # meaningless for vector output and is not passed. CreationDate is
        # omitted deliberately so repeat archives remain byte-reproducible.
        buf = io.BytesIO()
        fig.savefig(buf, format="pdf", bbox_inches="tight", metadata={
            "Title": title or "Snow pit profile",
            "Creator": "CryoPit",
            "CreationDate": None,
        })
        return buf.getvalue()

    def _save_png():
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight")
        return buf.getvalue()

    if fmt == "pdf":
        return _save_pdf()
    if fmt == "png":
        return _save_png()

    # Archive/download mode: build the expensive figure once, then serialize
    # both derivatives. PNG is required by the archive lifecycle; PDF has
    # historically been best-effort and remains so here. Writing PNG first
    # preserves that contract even if the vector backend fails afterwards.
    png = _save_png()
    try:
        pdf = _save_pdf()
    except Exception:
        pdf = None
    return png, pdf
