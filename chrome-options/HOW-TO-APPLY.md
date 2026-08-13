# Choosing how the chrome behaves

The shipped app is **as-is**: the command bar, section spine and live column are
dark in both themes. If you want one of the other two, drop a single file in —
no other change is needed.

```
cp optionA_chrome-follows-theme.css  cryopit/static/css/10_chrome.css
```

`web.py` concatenates `static/css/*.css` in sorted order, so a file numbered
above `00_` simply layers on top. Delete it to go back.

| File | What it does |
|---|---|
| `optionA_chrome-follows-theme.css` | Light mode gets a pale cool-slate surround; dark mode keeps polar night. The theme toggle affects the whole window. |
| `optionB_original-split.css` | The command bar stays dark in both themes, as it always was; the spine and rail follow the theme, as they did before the redesign. |
| *(no file)* | As shipped — the whole surround stays dark in both themes. |

## One extra step for Option B

Option B draws the mini snow column on a light rail, so it needs the sheet's ink
rather than the chrome's. In `cryopit/static/js/75_rail.js`, inside `drawMini()`:

```js
const ink3 =css.getPropertyValue('--ink3').trim()||'#56697d';
const rule2=css.getPropertyValue('--rule2').trim()||'#c3d1de';
```

(Those are the two token names the function read before the redesign. Option A
needs no JS change.)

## Contrast

Both options were audited the same way as the shipped build — real rendered
contrast for 30 text roles against their actual painted backgrounds — and both
clear WCAG AA in both themes. Option A needed two adjustments to get there,
which are already in the file: a darker `--chrome-ink3`, and the active spine
numeral moving from firn cyan (2.64:1 on a light surround) to meltwater blue.
