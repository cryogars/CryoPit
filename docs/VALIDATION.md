# Errors and warnings

Every check CryoPit makes, what triggers it, and whether it stops you.

## Two severities

| | appearance | effect |
|---|---|---|
| **✖ Blocks archive** | red, solid box | Archive refuses until it is fixed |
| **⚠ Warning** | amber, solid box | advisory — Archive still works |

Both stay on screen until the condition clears. They appear in four places, all
driven from one state so they cannot disagree:

- the section's own box, with the specific message
- the section header, as a glyph — `✖` / `⚠` / `✓` — which stays visible even
  when the section is **collapsed**
- the sidebar pip
- the §12 checklist row

A section with warnings shows `⚠` in all four, not a green tick in the checklist
and `⚠` on the header.

**Blocking rules mirror the server exactly, wording included.** Anything that
blocks in the form would also be refused by `save_pit()`; the point of showing
it live is that you find out while you are standing at the pit rather than at
the end.

---

## §1 Identity

| | severity | rule |
|---|---|---|
| Missing location | ✖ | Archive needs a location |
| Missing Pit ID | ✖ | site + date, or typed by hand |
| Missing Recorded by / Surveyors | ✖ | |

## §2 Weather

Weather fields are multi-select because conditions can change while a pit is
open. Select every observed precipitation rate, precipitation type, sky
condition, and wind category. `None` cannot be combined with a specific
precipitation rate or type; choosing either side clears the other in the form,
and the server rejects contradictory payloads. Sequence is not inferred — use
the weather comments when the order or timing of a transition matters.

## §4 Temperature

Heights are measured **up from the ground**: a 40 cm pack runs 40, 30, 20, 10, 0.

| | severity | rule |
|---|---|---|
| Height above total depth | ✖ | cannot measure snow that is not there |
| Height below −10 cm | ✖ | a probe sits just under the interface; deeper is a typo |
| Height between −10 and 0 | ⚠ | *"recorded as a ground temperature"* — legitimate, and noted so a mistyped `-10` for `10` is still visible |
| Temperature below −40 °C | ✖ | below any plausible snow temperature |
| Temperature −40 to −25 °C | ⚠ | extreme but real in some climates |
| Temperature above +1 °C | ⚠ | wet snow sits *at* zero and instruments read either side, so the threshold is +1, not 0 |
| Height with no temperature | ⚠ | see *rows in progress* below |

**Ground temperature.** Occasionally a crew takes one reading below the
snow-ground interface, recorded as a negative height. Temperature is the only
table where that is allowed — every other table measures snow.

## §5 Density

| | severity | rule |
|---|---|---|
| Top ≤ bottom | ✖ | inverted interval |
| Top above total depth | ✖ | |
| Negative depth | ✖ | |
| Reading outside 1–917 kg/m³ | ✖ | 917 is solid ice |
| Interval with no reading | ⚠ | see *rows in progress* |

## §6 LWC

| | severity | rule |
|---|---|---|
| Top ≤ bottom | ✖ | |
| Top above total depth, negative depth | ✖ | |
| Permittivity outside 1–12 | ✖ | |
| Readings present, no LWC instrument marked Used in §9 | ⚠ | **not** auto-corrected: a Digital LWC and a Lyte Probe both produce these readings and the form does not record which, so ticking one would put a statement in the record that nobody made |
| Interval with no permittivity | ⚠ | see *rows in progress* |

## §7 Stratigraphy

| | severity | rule |
|---|---|---|
| Top ≤ bottom | ✖ | |
| Top above total depth, negative depth | ✖ | |
| Layer density outside 1–917 kg/m³ | ✖ | |
| Layer density outside 50–700 kg/m³ | ⚠ | unusual for snow; ice layers can reach 917 |
| Top with no bottom | ⚠ | see *rows in progress* |

**Grain type, hardness and wetness are never blank.** Each dropdown starts on
its first option — `PP`, `F`, `D` — so a layer you do not touch is exported with
those values. If they do not describe the layer, set them: **an untouched
dropdown is recorded as a real answer, not as "unknown".**

## §8 SSA

| | severity | rule |
|---|---|---|
| Height with no value | ⚠ | see *rows in progress* |

Recording SSA measurements forces *SSA / NIR Box* to Yes in §9 and locks it —
§8's instrument list (IceCube, IRIS2, IRIS) is entirely NIR boxes, so the data
identifies the instrument unambiguously.

## §9 Instruments & tasks

Not a validation so much as a completeness rule: the section counts as answered
when **each of its two groups** has either a Yes or an explicit *"No instruments
used"* / *"No tasks done"*. N is both the default and a valid answer, so silence
cannot be read as "nothing was used".

Rows are forced and locked where evidence exists — attached photographs force
*Pit pictures* / *Stratigraphy pictures*; SSA measurements force *SSA / NIR
Box*. Removing the evidence unlocks the row but does **not** reset it: a Yes
with nothing attached stays valid, because "photographed, files on a separate
camera" is a real answer.

---

## Rows in progress

Every measurement table reports partly-filled rows the same way:

> ⚠ 2 rows started but no density reading entered

**One line per table, not per row** — ten half-filled rows would otherwise give
ten near-identical amber lines. Always advisory: a row being typed is not a
mistake, and the count falls as rows are completed.

| section | counted as started when | complete when |
|---|---|---|
| §4 Temperature | height entered | temperature entered |
| §5 Density | interval entered | any of A / B / C entered |
| §6 LWC | interval entered | a permittivity entered |
| §7 Stratigraphy | top entered | bottom entered |

## Photographs

| | severity | rule |
|---|---|---|
| Unrecognised file type | ✖ | JPEG, PNG, WebP, HEIC, or PDF (pit sheet only) — checked by the file's own bytes, not its name |
| Over 10 MB | ✖ | applies to the real file: photographs upload at full resolution, so a high-end camera can exceed it where a shrunk copy never would |
| Same photograph twice on one layer/category | — | silently skipped and reported: *"1 already attached (skipped)"* |
| More than 20 on one layer, or 150 on the pit | ✖ | the pit ceiling is what the download can assemble, not rationing |

**HEIC is accepted and converted.** iPhones have shot HEIC by default since
iOS 11, so it is the format a field crew is most likely to produce. CryoPit
converts it to JPEG on upload at **full resolution** — every pixel is kept, only
the compression changes — so the archive stays in one format that anything can
open. The response says so: *"Converted from HEIC to JPEG (full resolution)."*

If the server is missing `pillow-heif`, the HEIC is stored exactly as it
arrived rather than refused. Losing a field photograph because a server lacks a
dependency would be the worse failure.

## Rows that are archived incomplete

A partly-filled row does **not** block an archive — it is exported using the
SnowEx no-data convention, `-9999`:

```
100,-8,-9999          temperature: height 100, reading -8, no second sensor
50,-9999,-9999        height 50 recorded, no temperature taken
```

For density this is not merely tolerated but useful: an interval with no reading
is exactly what the gap-filler is for, and the derived product labels it
honestly.

```
100.0,90.0,250.0,250.0,measured [A measured]
90.0,80.0,275.0,275.0,gap-filled (neighbor-mean)
80.0,70.0,300.0,300.0,measured [A measured]
```

So nothing is silently invented. The ⚠ warning exists to catch the case where
you *meant* to finish the row, not to stop you archiving a pit where a
measurement genuinely was not taken.

## What is NOT checked

Deliberately: weather beyond precipitation rate, ground beyond condition, grain
size, comments, flags, SSA calibration. These are optional and never block.

**100% complete does not mean Archive will succeed.** The completion bar counts
required *items*; Archive additionally refuses on any ✖ above. A pit can show
100% while §5 holds a blocking error — which is why the `✖` glyph appears on the
section header and in the checklist, where the bar alone would not show it.
