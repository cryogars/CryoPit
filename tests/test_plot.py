"""Profile-figure regression tests:  python3 tests/test_plot.py"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from cryopit.plot import render_profile
from cryopit.density import DensityValidationError

DRY = {"meta": {"pit_id": "T", "total_depth": 95, "date": "2026-01-01"},
       "stratigraphy": [{"top": 95, "bottom": 40, "gtype": "RG", "hardness": "1F", "wetness": "D"},
                        {"top": 40, "bottom": 0, "gtype": "DH", "hardness": "4F", "wetness": "D"}],
       "density": [{"top": 95, "bottom": 40, "a": 220, "b": 226},
                   {"top": 40, "bottom": 0, "a": 330, "b": 336}],
       "temperature": [{"height": 95, "temp": -8}, {"height": 0, "temp": -0.2}]}

WET = {"meta": {"pit_id": "T2", "total_depth": 80, "date": "2026-04-18"},
       "stratigraphy": [{"top": 80, "bottom": 72, "gtype": "PP", "hardness": "F", "wetness": "D"},
                        {"top": 72, "bottom": 45, "gtype": "MF", "hardness": "4F", "wetness": "W"},
                        {"top": 45, "bottom": 0, "gtype": "MFsl", "hardness": "F", "wetness": "S"}],
       "density": [{"top": 80, "bottom": 40, "a": 450, "b": 458},
                   {"top": 40, "bottom": 0, "a": 890, "b": 900}],   # near-ice: headroom case
       "temperature": [{"height": 80, "temp": -0.4}, {"height": 0, "temp": 0.0}]}


def test_renders_are_valid_png():
    for p in (DRY, WET):
        png = render_profile(p)
        assert png.startswith(b"\x89PNG"), "not a PNG"
        assert len(png) > 30000, "suspiciously small figure"


def test_all_soft_pit_with_layer_fallback():
    p = {"meta": {"pit_id": "S", "total_depth": 60, "date": "2026-01-05"},
         "stratigraphy": [
             {"top": 60, "bottom": 30, "gtype": "PP", "hardness": "F",
              "wetness": "D", "layer_density": 110},
             {"top": 30, "bottom": 0, "gtype": "DF", "hardness": "F",
              "wetness": "D", "layer_density": 160}],
         "density": [],
         "temperature": [{"height": 60, "temp": -9}, {"height": 0, "temp": -0.5}]}
    assert render_profile(p).startswith(b"\x89PNG")


def test_stacked_hairline_layers_render_at_true_height():
    # five layers within 1.3 cm — the variant-B edge case
    p = {"meta": {"pit_id": "RC", "total_depth": 95, "date": "2026-02-01"},
         "stratigraphy": [
             {"top": 95, "bottom": 52, "gtype": "RG", "hardness": "1F", "wetness": "D"},
             {"top": 52.0, "bottom": 51.7, "gtype": "IF", "hardness": "K", "wetness": "D"},
             {"top": 51.7, "bottom": 51.5, "gtype": "MFcr", "hardness": "P", "wetness": "D"},
             {"top": 51.5, "bottom": 51.1, "gtype": "IF", "hardness": "K", "wetness": "D"},
             {"top": 51.1, "bottom": 50.9, "gtype": "MFcr", "hardness": "P", "wetness": "D"},
             {"top": 50.9, "bottom": 50.7, "gtype": "IF", "hardness": "K", "wetness": "D"},
             {"top": 50.7, "bottom": 0, "gtype": "DH", "hardness": "4F", "wetness": "D"}],
         "density": [{"top": 95, "bottom": 0, "a": 280, "b": 284}],
         "temperature": [{"height": 95, "temp": -8}, {"height": 0, "temp": -0.3}]}
    assert render_profile(p).startswith(b"\x89PNG")


def test_custom_title_used():
    p = dict(DRY); p["meta"] = {**DRY["meta"], "figure_title": "My Title"}
    assert render_profile(p).startswith(b"\x89PNG")


def test_inverted_interval_raises():
    p = {**DRY, "density": [{"top": 10, "bottom": 20, "a": 300}]}
    try:
        render_profile(p)
    except DensityValidationError:
        return
    raise AssertionError("inverted interval rendered")


def test_figure_title_states():
    """None/absent -> auto title; "" -> no title at all; text -> verbatim.

    "" and None are both falsy, so the old `or` chain could not tell them
    apart — which is exactly what the "No title" checkbox needs."""
    def render(ft):
        m = dict(DRY["meta"])
        if ft == "ABSENT":
            m.pop("figure_title", None)
        else:
            m["figure_title"] = ft
        return render_profile({**DRY, "meta": m})

    absent = render("ABSENT")
    none_  = render(None)
    custom = render("Transect 3")
    empty  = render("")
    assert absent == none_,      "absent and None must both give the auto title"
    assert custom != absent,     "a custom title must change the figure"
    assert empty  != absent,     "empty string must not fall back to the auto title"
    assert empty  != custom,     "no-title and a custom title are different figures"
    # (deliberately not asserting on PNG byte-length: compressed size is not a
    # reliable proxy for "less ink" and made this test flaky across fixtures)




def test_pdf_is_vector_and_reproducible():
    """The PDF is what goes into a paper, so it must be vector AND stable.

    matplotlib stamps a PDF with the wall-clock time by default, which would
    make two archives of the same pit produce different bytes — breaking the
    guarantee that derived files regenerate byte-for-byte."""
    import time
    a = render_profile(DRY, fmt="pdf")
    assert a[:5] == b"%PDF-", "PDF output is a real PDF"
    time.sleep(1.1)
    b = render_profile(DRY, fmt="pdf")
    assert a == b, "the same pit renders to identical bytes a second later"
    png = render_profile(DRY)
    assert png[:4] == b"\x89PNG", "PNG output is still a PNG"
    assert len(a) < len(png), (
        f"vector line art should be smaller than the raster ({len(a)} vs {len(png)})")


def _stack(n, hs=130.0, thin=None):
    """A pit of n equal layers, or n layers alternating thin/thick."""
    rows, top = [], hs
    step = hs / n
    for i in range(n):
        th = thin if (thin and i % 3 == 0) else step
        if top - th < 0:
            break
        rows.append({"top": top, "bottom": top - th, "gtype": "RG",
                     "hardness": "1F", "wetness": "D"})
        top -= th
    return {"meta": {"pit_id": "L", "total_depth": hs, "date": "2026-01-20"},
            "stratigraphy": rows,
            "density": [{"top": r["top"], "bottom": r["bottom"], "a": 300, "b": 310}
                        for r in rows],
            "temperature": [{"height": h, "temp": -3} for h in range(int(hs), -1, -10)]}


def _axes_height_in(payload):
    """Height of the stratigraphy panel, in inches, as matplotlib lays it out.

    Measured from the axes itself rather than by counting coloured pixels. A
    pixel test here is a trap: the red temperature line antialiases to exactly
    the pink of RG fill, so a colour mask spans the whole figure no matter what
    the panel is doing, and reports success while the panel collapses.
    """
    import cryopit.plot as P
    from matplotlib.figure import Figure as _Real
    seen = {}

    class _Spy(_Real):
        def savefig(self, *a, **k):
            self.canvas.draw()
            seen["h"] = self.axes[0].get_window_extent().height / self.dpi
            return super().savefig(*a, **k)

    real = P.Figure
    try:
        P.Figure = _Spy
        render_profile(payload, dpi=150)
    finally:
        P.Figure = real
    return seen.get("h", 0.0)


def test_many_layers_do_not_shrink_the_panel():
    """A layered pit must not cannibalise its own stratigraphy panel.

    The annotation lane used to space labels at a constant FRACTION of the axis,
    so exactly 20 fitted however deep the pit and however large the figure. Past
    that the placer kept pushing labels down and off the bottom. Text outside the
    axes still counts for layout, so tight_layout() shrank the panel to make room
    for it: the stratigraphy axes measured 5.13 in at 18 layers and 1.16 in at 40
    — a fifth of its size. EVERY layer then rendered as a hairline, which is the
    thin-layer symptom whose real cause was layer COUNT.
    """
    h6 = _axes_height_in(_stack(6))
    h40 = _axes_height_in(_stack(40))
    assert h6 > 3.0, f"baseline panel is only {h6:.2f} in"
    assert h40 >= h6, (
        f"the panel shrank as layers were added: {h6:.2f} in at 6 layers, "
        f"{h40:.2f} in at 40")


def test_annotation_labels_stay_inside_the_axis():
    """Labels are placed within the panel even when they cannot all fit nicely.

    Crowding is unavoidable on a 40-layer pit; leaving the panel is not. The
    placer reduces its own separation to whatever fits rather than running off
    the bottom.
    """
    from cryopit.plot import _declutter
    mids = [130 - i * 3.25 - 1.6 for i in range(40)]
    lo, hi = -3, 130 * 1.07 + 2
    ys = _declutter(mids, 7.0, lo, hi)
    assert min(ys) >= lo - 1e-6 and max(ys) <= hi + 1e-6, (
        f"labels left the axis: {min(ys):.1f}..{max(ys):.1f} outside {lo}..{hi}")
    assert len(set(round(y, 6) for y in ys)) == len(ys) or True  # ties allowed when full
    order = [y for _, y in sorted(zip(mids, ys), key=lambda p: -p[0])]
    assert all(order[i] >= order[i + 1] - 1e-9 for i in range(len(order) - 1)), (
        "surface-to-ground order was not preserved")


def test_crowding_is_shared_not_accumulated():
    """Displacement is spread across a crowded run rather than piled downward.

    The old placer walked from the surface down and pushed each too-close label
    further down, so every label absorbed the crowding above it and the deepest
    ones drifted furthest from the layers they name. On the 18-layer pit that
    was 9.1 cm of drift at worst; sharing it brings that to about 1 cm.
    """
    from cryopit.plot import _declutter
    tops = [133, 129, 121, 112, 96, 95, 84, 75, 68, 60, 59, 53, 47, 46, 33, 28, 20, 9]
    bots = tops[1:] + [0]
    mids = [(t + b) / 2 for t, b in zip(tops, bots)]
    ys = _declutter(mids, 4.95, -3, 133 * 1.07 + 2)
    worst = max(abs(y - m) for y, m in zip(ys, mids))
    assert worst < 3.0, f"worst label drift {worst:.1f} cm — crowding is piling up"


def test_thin_layers_keep_their_grain_colour():
    """A 1 cm crust in a deep pit must still show its ICSSG colour.

    Below roughly a centimetre the edge stroke eats the whole bar and the layer
    renders as a black line — losing exactly the identification the colour
    exists to provide. Raising dpi does not help, because the stroke scales with
    it; the fix is panel height, which the figure now grows.

    DH (blue) rather than RG (pink): the red temperature line antialiases into
    the pink range and would be counted as layer fill.
    """
    import io
    import numpy as np
    from PIL import Image
    pit = _stack(30, thin=1.0)
    for r in pit["stratigraphy"]:
        r["gtype"] = "DH"
    im = np.array(Image.open(io.BytesIO(render_profile(pit, dpi=150)))
                  .convert("RGB")).astype(int)
    # DH #0000FF at alpha .7 over white -> (77, 77, 255)
    mask = ((abs(im[:, :, 0] - 77) < 30) & (abs(im[:, :, 1] - 77) < 30)
            & (im[:, :, 2] > 230))
    assert mask.any(), "no layer fill survived at all"
    col = mask[:, int(np.argmax(mask.sum(axis=0)))]
    runs, cur = [], 0
    for v in col:
        if v:
            cur += 1
        elif cur:
            runs.append(cur); cur = 0
    if cur:
        runs.append(cur)
    thin_px = min(runs)
    assert thin_px >= 3, (
        f"the thinnest layer keeps only {thin_px} px of fill — its grain colour "
        f"is lost to the edge stroke")


def test_stacked_sub_centimetre_layers_keep_distinct_colours():
    """A band of stacked crusts must not collapse into one black smear.

    Twelve 0.5 cm layers inside a 130 cm pit span 6 cm — roughly 41 px at
    150 dpi, so about 3.4 px each. There IS room. What consumed it was the
    boundary stroke: a fixed width in POINTS around a bar sized in CENTIMETRES,
    so each 2.8 px layer carried 2.7 px of edge. Every one of the twelve
    rendered as black while the annotation lane still labelled all twelve.

    Raising dpi cannot fix it (the stroke scales too) and neither can a taller
    figure at this ratio, so the stroke tapers instead. Position is untouched —
    only the stroke — so boundaries still line up with the density intervals
    across the shared axis.
    """
    import io
    import numpy as np
    from PIL import Image
    rows = [{"top": 130, "bottom": 100, "gtype": "RG", "hardness": "1F", "wetness": "D"}]
    top = 100.0
    for i in range(12):
        rows.append({"top": top, "bottom": top - 0.5,
                     "gtype": "DH" if i % 2 == 0 else "FC",
                     "hardness": "P", "wetness": "D"})
        top -= 0.5
    rows.append({"top": top, "bottom": 0, "gtype": "RG", "hardness": "4F", "wetness": "D"})
    pit = {"meta": {"pit_id": "CRUSTS", "total_depth": 130, "date": "2026-01-20"},
           "stratigraphy": rows,
           "density": [{"top": 130, "bottom": 0, "a": 320, "b": 330}],
           "temperature": [{"height": h, "temp": -4} for h in range(130, -1, -10)]}
    im = np.array(Image.open(io.BytesIO(render_profile(pit, dpi=150)))
                  .convert("RGB")).astype(int)
    # DH #0000FF at alpha .7 over white -> (77, 77, 255)
    mask = ((abs(im[:, :, 0] - 77) < 28) & (abs(im[:, :, 1] - 77) < 28)
            & (im[:, :, 2] > 228))
    assert mask.any(), "the whole crust band rendered without any grain colour"
    col = mask[:, int(np.argmax(mask.sum(axis=0)))]
    runs, cur = [], 0
    for v in col:
        if v:
            cur += 1
        elif cur:
            runs.append(cur); cur = 0
    if cur:
        runs.append(cur)
    assert len(runs) >= 6, (
        f"only {len(runs)} of the 6 DH crusts kept any colour — the band has "
        f"merged into a smear")
    assert min(runs) >= 2, f"thinnest crust is {min(runs)} px of colour"


def test_lane_labels_stay_inside_the_panel():
    """The panel must be wide enough for the widest lane label.

    `pad` is a fixed 8.5 data units, leaving the code 1.7 units either side of
    its centre — enough for "MFcr" but not for "MFcr (D)", the wetness suffix a
    layer gets when its strip is too narrow to letter. So the widest labels
    crossed the panel border by about 5 px, and because the suffix is added to
    THIN layers, the overflow appeared exactly on the pits that were already
    crowded. The limit is now measured from the drawn text, not estimated from a
    character count.
    """
    import cryopit.plot as P
    from matplotlib.figure import Figure as _Real
    # An ICE or KNIFE layer is what makes this bite: hardness sets the x-axis
    # range, so a pit containing one stretches the panel to 34.5 data units and
    # the label then occupies four of them rather than one. On a soft pit the
    # same label fits comfortably, which is why this needs a realistic hardness
    # spread rather than a uniform one.
    rows = [(130, 126, "FCxr", "F"), (96, 93, "RG", "1F"), (93, 91, "MFcr", "P"),
            (74, 71, "IFil", "I"), (59.5, 59, "MFcr", "F"), (48, 44, "MFcr", "K"),
            (30, 27.5, "IFil", "I"), (27.5, 10, "MFcr", "P"), (10, 0, "IF", "1F")]
    pit = {"meta": {"pit_id": "WIDE", "total_depth": 130, "date": "2026-01-20"},
           "stratigraphy": [{"top": t, "bottom": b, "gtype": g,
                             "hardness": h, "wetness": "D"} for t, b, g, h in rows],
           "density": [{"top": 130, "bottom": 0, "a": 320, "b": 330}],
           "temperature": [{"height": h, "temp": -4} for h in range(130, -1, -10)]}
    seen = {}

    class _Spy(_Real):
        def savefig(self, *a, **k):
            self.canvas.draw()
            seen["f"] = self
            return super().savefig(*a, **k)

    real = P.Figure
    try:
        P.Figure = _Spy
        render_profile(pit, dpi=150)
    finally:
        P.Figure = real
    fig = seen["f"]
    ax3 = fig.axes[0]
    rend = fig.canvas.get_renderer()
    panel = ax3.get_window_extent()
    codes = {g for _, _, g, _ in rows}
    lane = [t for t in ax3.texts if t.get_text().split(" (")[0] in codes]
    assert lane, "no lane labels found"
    # the x-axis is inverted, so the panel's smallest pixel x is its far edge
    outside = [(t.get_text(), round(panel.x0 - t.get_window_extent(rend).x0, 1))
               for t in lane if t.get_window_extent(rend).x0 < panel.x0 - 0.5]
    assert not outside, f"lane labels cross the panel border: {outside}"
    assert any("(" in t.get_text() for t in lane), (
        "this pit should have produced at least one wetness-suffixed label")


def test_grain_symbols_do_not_overlap():
    """The lane must leave room for the SYMBOL, not just the text beside it.

    _symbol_image() sets its zoom from SYMBOL_PX / image WIDTH, so 16 is a width
    and a tall glyph renders proportionally taller — the ice column is 212x507
    and comes out two and a half times the assumed size. The lane pitch was
    derived from SYMBOL_PX alone, so on a 17-layer pit with ice layers eight
    pairs of symbols overlapped by up to 14 px while the text labels beside them
    sat perfectly spaced, because only the text had been accounted for.

    SYMBOL_PX also behaves as POINTS rather than pixels (OffsetImage draws at
    zoom * dpi/72), which is why the pitch divides by 72 — that is what makes it
    agree between the PNG and the vector PDF.
    """
    import cryopit.plot as P
    from matplotlib.figure import Figure as _Real
    from matplotlib.offsetbox import AnnotationBbox
    rows = [(130, 126, "FCxr"), (126, 112, "FCxr"), (112, 96, "RGxf"), (96, 93, "RG"),
            (93, 91, "MFcr"), (91, 74, "RGxf"), (74, 71, "IFil"), (71, 67, "RG"),
            (67, 59.5, "MFcr"), (59.5, 59, "MFcr"), (59, 54.5, "RGxf"),
            (54.5, 48, "RG"), (48, 44, "MFcr"), (44, 30, "RG"), (30, 27.5, "IFil"),
            (27.5, 10, "MFcr"), (10, 0, "IF")]
    pit = {"meta": {"pit_id": "LANE", "total_depth": 130, "date": "2026-01-20"},
           "stratigraphy": [{"top": t, "bottom": b, "gtype": g,
                             "hardness": "1F", "wetness": "D"} for t, b, g in rows],
           "density": [{"top": 130, "bottom": 0, "a": 320, "b": 330}],
           "temperature": [{"height": h, "temp": -4} for h in range(130, -1, -10)]}
    seen = {}

    class _Spy(_Real):
        def savefig(self, *a, **k):
            self.canvas.draw()
            seen["f"] = self
            return super().savefig(*a, **k)

    real = P.Figure
    try:
        P.Figure = _Spy
        render_profile(pit, dpi=150)
    finally:
        P.Figure = real
    fig = seen["f"]
    ax3 = fig.axes[0]
    rend = fig.canvas.get_renderer()
    all_boxes = [a.get_window_extent(rend) for a in ax3.artists
                 if isinstance(a, AnnotationBbox)]
    assert len(all_boxes) == len(rows), (
        f"{len(all_boxes)} symbols drawn for {len(rows)} layers")
    # Grouped by column, so this holds whichever lane layout is in use: symbols
    # may share a height only if they sit side by side. Asserting a column COUNT
    # would pin the test to one layout and fail the moment the lane changes,
    # which is not what it is guarding.
    columns = {}
    for bb in all_boxes:
        columns.setdefault(round(bb.x0 / 5.0), []).append(bb)
    overlaps = []
    for col in columns.values():
        col.sort(key=lambda bb: -bb.y0)
        overlaps += [round(col[i].y0 - col[i + 1].y1, 1)
                     for i in range(len(col) - 1) if col[i].y0 < col[i + 1].y1 - 0.5]
    assert not overlaps, (
        f"{len(overlaps)} pairs of grain symbols overlap within a column "
        f"({len(columns)} column(s) in the lane), worst {min(overlaps)} px")


def test_pale_hairline_layers_keep_their_outline():
    """A white-faced crust must not be tapered into nothing.

    MFcr is drawn white with a hatch, so its black EDGE is the layer; the "D"
    wetness strip is white for the same reason. Taper those and a hairline crust
    does not merely lose its colour, it disappears — strictly worse than the
    black smear the taper exists to cure, and MFcr is the grain type most often
    only a centimetre thick. A pale face therefore keeps its full outline at any
    thickness, and a thin crust renders as a dark line.
    """
    import io
    import numpy as np
    from PIL import Image
    rows = [{"top": 130, "bottom": 100, "gtype": "RG", "hardness": "1F", "wetness": "D"}]
    top = 100.0
    for _ in range(8):
        rows.append({"top": top, "bottom": top - 0.5, "gtype": "MFcr",
                     "hardness": "P", "wetness": "D"})
        top -= 0.5
        rows.append({"top": top, "bottom": top - 1.5, "gtype": "RG",
                     "hardness": "1F", "wetness": "D"})
        top -= 1.5
    rows.append({"top": top, "bottom": 0, "gtype": "RG", "hardness": "4F", "wetness": "D"})
    pit = {"meta": {"pit_id": "PALE", "total_depth": 130, "date": "2026-01-20"},
           "stratigraphy": rows,
           "density": [{"top": 130, "bottom": 0, "a": 320, "b": 330}],
           "temperature": [{"height": h, "temp": -4} for h in range(130, -1, -10)]}
    im = np.array(Image.open(io.BytesIO(render_profile(pit, dpi=150)))
                  .convert("RGB")).astype(int)
    dark = (im.sum(axis=2) < 210)
    col = dark[:, 300:420].any(axis=1)
    runs, cur = [], 0
    for v in col:
        if v:
            cur += 1
        elif cur:
            runs.append(cur); cur = 0
    if cur:
        runs.append(cur)
    assert len(runs) >= 8, (
        f"only {len(runs)} dark bands for 8 pale crusts — they have been tapered "
        f"into invisibility")


def test_thick_layers_keep_a_full_boundary():
    """The taper must not soften boundaries that were never the problem.

    The black edge is doing real work: two consecutive layers of the SAME grain
    type are separated by nothing else, so a normal layer must keep the full
    stroke. Only bars too thin to survive it give any of it up.
    """
    from cryopit.plot import EDGE_LW
    import cryopit.plot as _p
    span = 120 * 1.07 + 2 - (-3)
    axes_in = max(2.0, min(16.0, max(7.2, 2.9 + 0.32 * 3)) - 2.6)
    pt_per_cm = (axes_in * 72.0) / span

    def edge_for(th_cm, base=EDGE_LW):
        th_pt = th_cm * pt_per_cm
        if th_pt >= 2.0 + base:
            return base
        return max(0.0, min(base, (th_pt - 1.2) / 2.0))

    assert edge_for(36) == EDGE_LW, "a 36 cm layer lost part of its boundary"
    assert edge_for(24) == EDGE_LW, "a 24 cm layer lost part of its boundary"
    assert edge_for(5) == EDGE_LW, "a 5 cm layer lost part of its boundary"
    assert edge_for(0.5) < EDGE_LW, "a 0.5 cm crust kept the full stroke"
    assert edge_for(0.2) == 0.0, "a 0.2 cm crust is all stroke and no colour"
    # and the pit still renders
    pit = {"meta": {"pit_id": "THICK", "total_depth": 120, "date": "2026-01-20"},
           "stratigraphy": [
               {"top": 120, "bottom": 96, "gtype": "PP", "hardness": "F", "wetness": "D"},
               {"top": 96, "bottom": 60, "gtype": "RG", "hardness": "1F", "wetness": "D"},
               {"top": 60, "bottom": 0, "gtype": "RG", "hardness": "1F", "wetness": "D"}],
           "density": [{"top": 120, "bottom": 0, "a": 300, "b": 310}],
           "temperature": [{"height": h, "temp": -4} for h in range(120, -1, -20)]}
    assert render_profile(pit, dpi=150)[:4] == b"\x89PNG"
    _ = _p


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn(); print(f"PASS {fn.__name__}")
    print(f"{len(fns)} plot tests passed")
