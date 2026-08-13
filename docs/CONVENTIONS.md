# Conventions

House rules for anyone adding to CryoPit. Small things, but they are what keep
the app feeling like one thing rather than several.

## Writing for the screen

**No em dashes.** Use a full stop, a colon, or brackets. An em dash reads as
filler and it is hard to type on a field tablet if anyone needs to quote a
message back.

```
no    2 photos attached — remove them to change this
yes   2 photos attached. Remove them first.
```

A lone `—` used as a *placeholder* in a field is fine. That is a glyph standing
in for an empty value, not punctuation.

**Say what to do, not just what is wrong.** A message that ends without a next
step leaves the reader stuck.

```
no    Blocks archive: top (120) exceeds total depth (100)
yes   Blocks archive: top (120) exceeds total depth (100). Correct the depth or the interval.
```

**Name the row.** "2 rows started but no reading" makes the reader hunt. Give
the numbers, and tint the rows as well, so the message and the screen agree.

**Never rely on a tooltip alone.** There is no hover on a tablet. If a control
is disabled, the reason belongs on screen next to it.

**Sentence case, no shouting.** Section names as the form shows them: §7
Stratigraphy, not "STRATIGRAPHY" or "the strat table".

**Units always.** `120 cm`, `250 kg/m³`, `-8 °C`. A bare number in a message is
ambiguous the moment it is read out of context.

## Two severities, and only two

| | glyph | meaning |
|---|---|---|
| Blocking | `✖` | Archive refuses. Mirrors a server rule exactly, wording included. |
| Warning | `⚠` | Advisory. The pit archives fine. |

Anything blocking on screen must also be refused by `save_pit()`, in the same
words. If the two ever disagree, the live message is a lie.

Both appear in four places from **one** state: the section's box, the header
glyph, the sidebar pip, and the §12 checklist row. Never paint one without the
others. Adding a fifth surface means adding it to `refreshStatusGlyphs()`, not
to `tick()`.

## Force, or warn. Never guess.

When data identifies an instrument unambiguously, the checklist row is forced
and locked. When it does not, warn and let the surveyor answer.

- Layer photographs → force *Stratigraphy pictures*. Unambiguous.
- SSA rows → force *SSA / NIR Box*. §8's instrument list is entirely NIR boxes.
- LWC rows → **warn only**. A Digital LWC and a Lyte Probe both produce these
  readings and the form does not record which. Ticking one would put a
  statement in the record that nobody made.

## Nothing flips under the user

If a state would contradict an action, close the action rather than allowing it
and correcting afterwards. Tick "No tasks done" and the photo controls shut,
with the reason showing. That is why no "the app changed this" notice is needed
anywhere.

Removing evidence **unlocks** a row but never resets it. A Yes with nothing
attached stays valid: "photographed, files on a separate camera" is a real
answer.

## Nothing reaches the server until Archive

The status pill says "not archived" and it must stay true. Selecting a photo
queues it; Archive uploads it. There is deliberately no second upload control:
Archive is what saves a pit, and a parallel path only raises the question of
which button to press.

## Identity is never a row id

Anything that outlives a re-archive must key on a **fact**, not a database id.

Layers are deleted and rebuilt from `raw_json` on every archive, so `layer_id`
is reassigned each time and the counter is global across pits. The same three
layers went from ids 1–3 to 7–9 after one re-archive that changed nothing.

- Photographs key on the **depth interval** they show, not the layer.
- Export folders are named `062-045cm`, not `layer2`. An ordinal shifts the
  moment a layer is inserted above it.
- Attachments are identified by `(pit, category, sha256, depth interval)`.

The same reasoning applies to any file path rebuilt from mutable metadata.

## Derived data is never stored twice

CSVs and the profile figure are written from the database on every archive, so
they regenerate byte-for-byte. Photographs cannot be regenerated and are the
only irreplaceable thing in the export folder.

Anything computed at read time (a layer's photo count, a mean density, which
layer a depth falls in) stays computed. Storing it creates a second copy that
can disagree with the first.

## Where things go

- Blocking rules: `repository.py`, mirrored in `80_layer_density.js`.
- Anything that repaints a warning box calls `refreshStatusGlyphs()` directly.
  Do not wait for the next `tick()`: validation runs immediately, only the
  profile redraw is debounced.
- Limits live in `web.py` and are written into the page. Never retype a number
  in a template.

## Tests

Assert **behaviour**, not implementation strings. A test that read `.rail`'s
`onclick` for the text `data-t=s10` broke when that handler was extracted into a
named function, despite nothing observable having changed.

Every bug fixed gets a test that fails without the fix. Several in this codebase
were verified by mutation: revert the fix, watch the suite go red.
