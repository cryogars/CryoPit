// CryoPit DOM smoke test — loads the ACTUAL assembled page in jsdom,
// executes the inline scripts, and checks the behaviors that must work:
// instrument Y/N toggles, checklist, live rail, layer-density toggle,
// attachments UI. Run:  node tests/test_dom.mjs  (needs `npm i jsdom`)
import { JSDOM, VirtualConsole } from "jsdom";
import { execSync } from "child_process";

// Build the page through _render_form() — the SAME function the / route uses —
// rather than re-implementing its substitutions here. The hand-rolled version
// silently went stale every time a new placeholder was added: it knew nothing
// about __LIM_JSON__, so the page under test carried a literal placeholder into
// the JS and every later assertion failed for an unrelated reason.
let html = execSync(
  `python3 -c "import sys; sys.path.insert(0,'.'); ` +
  `from cryopit.web import _render_form; sys.stdout.write(_render_form())"`,
  { cwd: new URL("..", import.meta.url).pathname, maxBuffer: 1 << 24,
    // Saved pits calls fetch() on load, which jsdom has no server for. Turn the
    // feature off through the SAME config switch a deployment would use, rather
    // than editing the rendered HTML.
    env: { ...process.env, CRYOPIT_ENABLE_EDIT: "0" } }).toString();

// Nothing may reach the browser unsubstituted. Record this as a real failure,
// but continue the suite so one token cannot hide hundreds of later checks.
// Replacing with `null` is syntactically safe both for bare JS values and for
// quoted/HTML/CSS contexts; the final failure still preserves the guard.
const unsubstituted = [...new Set(html.match(/__[A-Z0-9_]+__/g) || [])];
html = html.replace(/__[A-Z0-9_]+__/g, 'null');

// Queue removal is asynchronous now that the outbox is backed by IndexedDB, so
// the DOM updates a tick after the click rather than during it. Assertions that
// fire immediately after dispatching are testing the state BEFORE the handler
// has run.
const settle = () => new Promise(r => setTimeout(r, 30));

const errors = [];
const vc = new VirtualConsole();
vc.on("jsdomError", e => errors.push("jsdomError: " + (e.detail && e.detail.stack || e.message)));
vc.on("error", (...a) => errors.push("console.error: " + a.join(" ")));

const dom = new JSDOM(html, {
  runScripts: "dangerously",
  url: "http://localhost:8502/",
  virtualConsole: vc,
  pretendToBeVisual: true,
});
const { window } = dom;
const { document } = window;
window.fetch = () => Promise.resolve({ ok: true, json: () => Promise.resolve({ pits: [], attachments: [] }), blob: () => Promise.resolve(new window.Blob()) });
window.confirm = () => true;
window.addEventListener("error", e => errors.push("window.onerror: " + e.message + " @ line " + e.lineno));

// let scripts finish (they run synchronously on parse; timers may follow)
await new Promise(r => setTimeout(r, 300));

let pass = 0, fail = 0;
function check(cond, label) {
  if (cond) { console.log("PASS " + label); pass++; }
  else { console.log("FAIL " + label); fail++; }
}

check(unsubstituted.length === 0, "page contains no unsubstituted placeholders" +
  (unsubstituted.length ? " -> " + unsubstituted.slice(0, 5).join(", ") : ""));

check(errors.length === 0, "no runtime errors on load" + (errors.length ? " -> " + errors.slice(0, 3).join(" | ") : ""));

// instruments & tasks: rows built with Y/N toggles
const ynBtns = document.querySelectorAll('[id^="yy"], [id^="nn"]');
check(ynBtns.length >= 10, `instrument Y/N toggles built (${ynBtns.length})`);

// checklist populated
const cl = document.getElementById("cl-items");
check(cl && cl.children.length > 3, `checklist populated (${cl ? cl.children.length : 0} items)`);

// live rail drew something
const rail = document.querySelector(".rail");
const railSvg = rail && rail.querySelector("svg");
check(!!railSvg, "live rail rendered an SVG");

// layer-density toggle: click adds the column to a stratigraphy row
window.addRow && window.addRow("s", false);
const before = document.querySelectorAll("#sb .s-den").length;
window.toggleLayerDensity && window.toggleLayerDensity();
const after = document.querySelectorAll("#sb .s-den").length;
check(after === 3, `layer-density toggle adds THREE columns: rhoA, rhoB, mean (got ${after})`);
const th = document.getElementById("s-den-th");
check(th && th.style.display !== "none", "layer-density header visible after toggle");
check(document.getElementById("ld-toggle").textContent.includes("−"),
      "ρ toggle shows − when active");
// physical-bounds validation: a 2000 kg/m3 density is a named error
{
  const din = document.querySelectorAll("#db tr");
  if (!din.length) window.addRow("d", false);
  const ins = document.querySelector("#db tr").querySelectorAll("input[type=number]");
  ins[0].value = "50"; ins[1].value = "40"; ins[2].value = "2000";
  const errs = window._physicalBounds();
  check(errs.some(e => /2000.*917/.test(e)), "2000 kg/m³ blocked with named error");
  ins[2].value = "0";
  check(window._physicalBounds().some(e => /outside 1–917/.test(e)), "0 density blocked");
  ins[2].value = "300";
}
// soft warnings: profile disagreement flagged non-blockingly
{
  const ins = document.querySelector("#db tr").querySelectorAll("input[type=number]");
  ins[2].value = "250"; ins[3].value = "700";
  const w = window.densityWarnings();
  check(w.some(x => /disagree/.test(x)), "A=250/B=700 raises a disagreement warning");
  check(document.getElementById("d-warn").style.display !== "none", "warning shown in §5");
  ins[3].value = "260"; window.densityWarnings();
}
// out-of-range intervals never feed the live SWE/bulk numbers
{
  document.getElementById("depth").value = "47";
  const body = document.getElementById("db");
  body.innerHTML = ""; window.addRow("d", false);
  const ins = body.querySelector("tr").querySelectorAll("input[type=number]");
  ins[0].value = "70"; ins[1].value = "50"; ins[2].value = "300";   // beyond HS=47
  window.drawMini();
  check(document.getElementById("mc-den").textContent.trim() === "—"
        || !/\d/.test(document.getElementById("mc-den").textContent),
        "rail ignores an interval beyond total depth");
  check(ins[0].classList.contains("inp-bad"), "over-depth top cell outlined red");
  ins[0].value = "40"; ins[1].value = "30"; window.drawMini();
  check(/\d/.test(document.getElementById("mc-den").textContent),
        "rail resumes with a valid interval");
  body.innerHTML = ""; window.addRow("d", false); document.getElementById("depth").value = "";
}

// a stale attachment error clears on the next selection
{
  const msg = document.getElementById("attach-msg");
  msg.textContent = "Error: old"; msg.className = "attach-msg err";
  const f2 = new window.File([new Uint8Array([137,80,78,71])], "ok.png", { type: "image/png" });
  const inp2 = document.getElementById("att-pitwall");
  Object.defineProperty(inp2, "files", { value: [f2], configurable: true });
  await window.uploadAttachment("att-pitwall", "pitwall");
  await new Promise(r => setTimeout(r, 30));
  check(!/old/.test(msg.textContent), "stale attach error cleared by a new selection");
  window.removePending("pitwall", 0);   // leave the queue as we found it
}

// sort actually reorders rows surface->ground (regression: onclick escaping)
{
  const body = document.getElementById("tb");
  body.innerHTML = "";
  window.addRow("t", false); window.addRow("t", false);
  const rows = body.querySelectorAll("tr");
  rows[0].querySelector("input").value = "10";
  rows[1].querySelector("input").value = "90";
  window.sortRows("t");
  check(body.querySelector("tr input").value === "90", "sortRows puts surface first");
  const btn = [...document.querySelectorAll("button")].find(b => /⇅/.test(b.textContent));
  check(btn && !btn.getAttribute("onclick").includes("\\"), "sort button onclick has no stray backslashes");
}
// Enter moves vertically through existing profile-table rows. It must not
// create rows, move horizontally, or submit the form at the final row.
{
  const cases = [["t","tb"],["d","db"],["l","lb"],["s","sb"]];
  for (const [kind,id] of cases) {
    const body = document.getElementById(id);
    const saved = body.innerHTML;
    body.innerHTML = "";
    window.addRow(kind, false); window.addRow(kind, false);
    const rows = body.querySelectorAll("tr");
    const first = rows[0].querySelectorAll("input:not([readonly])")[1] ||
                  rows[0].querySelector("input:not([readonly])");
    const second = rows[1].cells[first.closest("td").cellIndex]
                       .querySelector("input:not([readonly])");
    first.focus();
    const down = new window.KeyboardEvent("keydown",
      {key:"Enter",bubbles:true,cancelable:true});
    first.dispatchEvent(down);
    check(down.defaultPrevented, `${id} Enter is consumed instead of submitting`);
    check(document.activeElement === second, `${id} Enter moves to the same next-row column`);
    check(body.rows.length === 2, `${id} Enter does not create a row`);
    const last = new window.KeyboardEvent("keydown",
      {key:"Enter",bubbles:true,cancelable:true});
    second.dispatchEvent(last);
    check(last.defaultPrevented && document.activeElement === second,
          `${id} final-row Enter stays in place`);
    body.innerHTML = saved;
  }
}

// §10 has its render button; the rail click-through targets s10 (post-reorder).
// Asserted by BEHAVIOUR, not by the onclick attribute's text: the handler was
// extracted into openFullProfile() so the click path and the keyboard path run
// identical code, and a string match on the attribute would fail on a refactor
// that changed nothing a user can observe.
check([...document.querySelectorAll("#s10 button")].some(b => /render profile/.test(b.textContent)),
      "§10 render-profile button present");
{
  const s10 = document.getElementById("s10");
  s10.classList.add("collapsed");                       // start from closed
  document.querySelector(".rail").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  check(!s10.classList.contains("collapsed"), "rail click opens Profile (s10)");
  s10.classList.add("collapsed");                       // and again from the keyboard
  document.querySelector(".rail").dispatchEvent(
    new window.KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
  check(!s10.classList.contains("collapsed"), "rail Enter key opens Profile (s10)");
  check(document.querySelector(".rail").getAttribute("tabindex") === "0",
        "rail is reachable by keyboard");
}
// permittivity bounds
{
  const body = document.getElementById("lb");
  if (!body.children.length) window.addRow("l", false);
  const ins = body.querySelector("tr").querySelectorAll("input[type=number]");
  ins[0].value = "50"; ins[1].value = "40"; ins[2].value = "15";
  check(window._physicalBounds().some(e => /permittivity 15/.test(e)), "permittivity 15 blocked");
  ins[2].value = "1.5";
  check(!window._physicalBounds().some(e => /permittivity/.test(e)), "permittivity 1.5 accepted");
}
// zero density is visible LIVE (blocking line in the §5 box), not only at action time
{
  const ins = document.querySelector("#db tr").querySelectorAll("input[type=number]");
  ins[2].value = "0";
  window.densityWarnings();
  check(/Blocks archive/.test(document.getElementById("d-warn").textContent),
        "0 density shows a live blocking line in §5");
  check(ins[2].classList.contains("inp-bad"), "offending cell outlined red");
  ins[2].value = "300"; window.densityWarnings();
}

// attachments UI: inputs exist, allow multiple, and the SHEET input is
// enabled even before archiving (files queue and travel with Archive)
for (const id of ["att-sheet", "att-pitwall", "att-strat"]) {
  const el = document.getElementById(id);
  check(!!el, `attachment input ${id} present`);
  if (el) check(el.multiple, `${id} allows multiple selection`);
}
check(!document.getElementById("att-sheet").disabled, "sheet input enabled pre-archive");
// The checklist now REFLECTS evidence, so this gate only holds with no photos
// AND the row set to N. Earlier blocks leave both dirty. Note that clearing the
// photos does NOT reset the row to N — a Yes with nothing attached is a
// legitimate answer ("photographed, files on a separate camera"), so the sync
// only ever unlocks the row, it never overrides what was chosen.
window.eval("_pendingAttach").pitwall.length = 0;
window.eval("_pendingAttach").stratigraphy.length = 0;
window.eval("_attachInfo").counts = {};
window.eval("refreshAttachUI()");
// The §9 checklist no longer gates the photo inputs — it reflects them. It
// could not do both: requiring a Yes before you may attach, while the
// attachment is what sets the Yes, is a loop (and it silently broke the §7
// camera buttons, since .click() on a disabled file input does nothing).
// A row marked N closes its own input (see below), so start from a state where
// nothing has been declared absent.
window.eval("setyn")(window.eval("_rowIndex")("Pit pictures"), "Y");
window.eval("setyn")(window.eval("_rowIndex")("Stratigraphy pictures"), "Y");
window.eval("refreshAttachUI()");
check(!document.getElementById("att-pitwall").disabled,
      "photo inputs are NOT gated by the checklist any more");
{
  const nt = document.getElementById("none-task");
  nt.checked = true; window.eval("onNoneGroup('task', false)");
  check(document.getElementById("att-pitwall").disabled,
        "an explicit 'No tasks done' DOES close them");
  nt.checked = false; window.eval("onNoneGroup('task', true)");
  check(!document.getElementById("att-pitwall").disabled, "and unticking reopens them");
}
// The standing §11 banner was removed: a queued file already shows as a
// pending chip, and the archive toast reports the outcome then disappears.
check(!document.getElementById("attach-state"), "no standing attachment banner");

// queueing: a selected file becomes a pending chip and counts toward limits
if (window.uploadAttachment) {
  const f = new window.File([new Uint8Array([137,80,78,71])], "wall1.png", { type: "image/png" });
  const inp = document.getElementById("att-sheet");
  Object.defineProperty(inp, "files", { value: [f], configurable: true });
  await window.uploadAttachment("att-sheet", "sheet");
  await new Promise(r => setTimeout(r, 50));
  const chips = document.querySelectorAll("#attach-list .att-chip.pending");
  check(chips.length === 1 && /wall1/.test(chips[0].textContent), "queued file shown as pending chip");
  check(!/⏳/.test(chips[0].textContent), "no hourglass on pending chips");
  check(!document.getElementById("attach-state"),
        "no banner even with a queued file — the chip and §11 counter carry it");
  // queued files are removable — the × chip control
  const x = document.querySelector("#attach-list .att-chip.pending .att-x");
  check(!!x, "pending chip has a remove control");
  check(/1 attached|1 queued/.test(document.getElementById("att-cnt").textContent),
        "section meta shows the live count");

  // sheet XOR at selection: a PDF after a queued image is refused locally
  const pdf = new window.File([new Uint8Array([0x25,0x50,0x44,0x46])], "sheet.pdf", { type: "application/pdf" });
  Object.defineProperty(document.getElementById("att-sheet"), "files", { value: [pdf], configurable: true });
  await window.uploadAttachment("att-sheet", "sheet");
  await new Promise(r => setTimeout(r, 50));
  const msg = document.getElementById("attach-msg");
  check(/Error: the pit sheet is one PDF OR up to three images\./.test(msg.textContent), "sheet XOR enforced at selection, locally");
  check(document.querySelectorAll("#attach-list .att-chip.pending").length === 1,
        "refused PDF was not queued");
}

// §7 layer densities get the unusual-value screen, shown locally
{
  const ld = document.querySelector("#sb .s-den input");
  if (ld) {
    ld.value = "800"; window.densityWarnings();
    check(/unusual for snow/.test(document.getElementById("s-warn").textContent),
          "800 layer density warns in the §7 box");
    ld.value = "275"; window.densityWarnings();
    check(document.getElementById("s-warn").style.display === "none",
          "§7 warning clears at a normal value");
  }
}

// toasts replace topbar messages; the chip keeps only pit state
window.setst("test message", "ok");
await new Promise(r => setTimeout(r, 30));
const toastEl = document.querySelector("#toasts .toast.ok");
check(!!toastEl && /test message/.test(toastEl.textContent), "setst produces a toast");
check(!/test message/.test(document.getElementById("tb-st").textContent),
      "topbar chip untouched by transient messages");

// collapsible sections: header click folds, nav click unfolds
// section order: checklist is last (§12), profile §10, attachments §11
const order = [...document.querySelectorAll("section.sec")].map(s => s.id);
check(order[9] === "s10" && order[10] === "s11" && order[11] === "s12",
      "section ids sequential after reorder");
const lbls = [...document.querySelectorAll(".idx-lbl")].map(e => e.textContent);
check(lbls[9] === "Profile" && lbls[10] === "Attachments" && lbls[11] === "Checklist",
      "nav order: Profile, Attachments, Checklist last");
const sec2 = document.getElementById("s2");
sec2.querySelector(".sec-hd").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
check(sec2.classList.contains("collapsed"), "header click collapses the section");
window.expandSection("s2");
check(!sec2.classList.contains("collapsed"), "expandSection reopens it");
// the §7 toggle button must NOT collapse its section
const sec7 = document.getElementById("s7");
// click twice: proves the button doesn't collapse the section AND leaves the
// layer-density state exactly as it was for the tests that follow
document.getElementById("ld-toggle").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
document.getElementById("ld-toggle").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
check(!sec7.classList.contains("collapsed"), "§7 ρ-toggle click does not collapse the section");

// collect() round-trips a layer density value
if (window.collect) {
  const input = document.querySelector("#sb .s-den input");
  const tops = document.querySelectorAll("#sb tr:first-child input");
  if (tops[0]) tops[0].value = "50";
  if (tops[1]) tops[1].value = "40";
  if (input) input.value = "275";
  const p = window.collect();
  check(p.stratigraphy && p.stratigraphy[0] && p.stratigraphy[0].layer_density === 275,
        "collect() captures layer_density");
}

// populate round-trip: what collect() produces must survive populate()
if (window.collect && window.populate) {
  const before = window.collect();
  before.meta.figure_title = "My Custom Title";
  before.meta.lwc_device = "WISe";
  before.ground.swe_samples = [{ sample: "A", depth: 12, swe: 30, density: 250 }];
  window.populate(before);
  await new Promise(r => setTimeout(r, 300));
  const after = window.collect();
  check(after.meta.figure_title === "My Custom Title", "populate restores figure title");
  check(after.meta.lwc_device === "WISe", "populate restores LWC device");
  check((after.ground.swe_samples || []).some(x => x.depth === 12 && x.swe === 30),
        "populate restores interval-board SWE");
  check(after.stratigraphy.length === before.stratigraphy.length
        && after.stratigraphy[0].layer_density === before.stratigraphy[0].layer_density,
        "populate restores stratigraphy incl. layer density");
  check(after.density.length === before.density.length, "populate restores density rows");
}

// ---------------------------------------------------------------------------
// REGRESSION: instrument checklist round-trip.
// collect() omits the blank write-in ("Other") row, so a saved payload can be
// shorter than the checklist. populate() used to restore BY ARRAY POSITION,
// which shifted every row after the write-in by one: a pit saved with
// "Pit pictures = Y" came back as "Stratigraphy pictures = Y", and the photo
// inputs those rows gate stayed locked. Restoration is by NAME now.
// ---------------------------------------------------------------------------
{
  const rows = window.eval("INST").filter(x => !x.g);
  const nameAt = i => rows[i].n || "(write-in)";
  const wIdx = rows.findIndex(x => x.w);
  const lastIdx = rows.length - 1;              // "Pit pictures"

  // start clean, then mark ONLY the last row + one before the write-in
  for (let i = 0; i < rows.length; i++) window.eval("setyn")(i, "N");
  document.getElementById("on" + wIdx).value = "";
  window.eval("setyn")(2, "Y");
  document.getElementById("sn2").value = "SMP-99";
  window.eval("setyn")(lastIdx, "Y");

  const saved = window.collect();
  const savedY = saved.instruments.filter(x => x.used === "Y").map(x => x.name);
  check(savedY.includes(nameAt(lastIdx)), "collect() records the last checklist row");
  check(saved.instruments.length < rows.length,
        "blank write-in row is omitted from the payload (the shift's cause)");

  // wipe the board, then restore
  for (let i = 0; i < rows.length; i++) window.eval("setyn")(i, "N");
  window.populate(JSON.parse(JSON.stringify(saved)));

  const isY = i => document.getElementById("yy" + i).classList.contains("on");
  check(isY(lastIdx), "populate restores the LAST checklist row (was off by one)");
  check(!isY(lastIdx - 1), "populate does NOT flip its neighbour (the shifted row)");

  // A saved null must restore as neither button lit; explicit N must remain N.
  const tri = JSON.parse(JSON.stringify(saved));
  const firstName = nameAt(0);
  const secondName = nameAt(1);
  const first = tri.instruments.find(x => x.name === firstName);
  const second = tri.instruments.find(x => x.name === secondName);
  if(first){first.used=null;first.sn='STALE-U';}
  if(second){second.used='N';second.sn='STALE-N';}
  window.populate(tri);
  const firstY=document.getElementById('yy0'), firstN=document.getElementById('yn0');
  const secondY=document.getElementById('yy1'), secondN=document.getElementById('yn1');
  check(!firstY.classList.contains('on') && !firstN.classList.contains('on'),
        "populate restores null as unanswered");
  check(!secondY.classList.contains('on') && secondN.classList.contains('on'),
        "populate preserves explicit N");
  check(document.getElementById('sn0').value==='' &&
        document.getElementById('sn1').value==='',
        "populate drops serial numbers for unanswered and N rows");
  check(isY(2) && document.getElementById("sn2").value === "SMP-99",
        "populate restores serial numbers with their instrument");
  check(window.eval("_checklistYes('Pit pictures')"),
        "photo-upload gate reads correctly after a round-trip");
}
// write-in instrument survives the round-trip (its name was dropped entirely)
{
  const rows = window.eval("INST").filter(x => !x.g);
  const wIdx = rows.findIndex(x => x.w);
  window.eval("setyn")(wIdx, "Y");
  document.getElementById("on" + wIdx).value = "Avalanche probe";
  document.getElementById("sn" + wIdx).value = "AP-7";
  const saved = window.collect();
  document.getElementById("on" + wIdx).value = "";
  window.eval("setyn")(wIdx, "N");
  window.populate(JSON.parse(JSON.stringify(saved)));
  check(document.getElementById("on" + wIdx).value === "Avalanche probe",
        "populate restores the write-in instrument name");
  check(document.getElementById("sn" + wIdx).value === "AP-7",
        "populate restores the write-in serial");
}

// ---------------------------------------------------------------------------
// REGRESSION: every CSS custom property used must be defined.
// --panel / --panel2 / --green / --amber were used but never declared, so each
// fell back to a hardcoded light-theme literal. .toast took background
// var(--panel,#fff) with no colour of its own, inheriting near-white --ink in
// dark mode: white-on-white. Toasts carry every Archive/Download result.
// ---------------------------------------------------------------------------
{
  const css = [...document.querySelectorAll("style")].map(s => s.textContent).join("\n");
  const used = new Set([...css.matchAll(/var\((--[\w-]+)/g)].map(m => m[1]));
  const declared = new Set([...css.matchAll(/(--[\w-]+)\s*:/g)].map(m => m[1]));
  const missing = [...used].filter(v => !declared.has(v));
  check(missing.length === 0, "no CSS variable is used without being defined" +
        (missing.length ? " -> " + missing.join(", ") : ""));

  // and both themes must declare the same token set, or one theme silently
  // falls back to the other's literals
  // The stylesheet the page serves is every static/css/*.css concatenated, so a
  // chrome option layered on top contributes a SECOND :root and dark block.
  // Collect them all — reading only the first would let a later block's tokens
  // go unchecked, which is precisely the drift this test exists to catch.
  const blocks = sel => [...css.matchAll(new RegExp(sel + "\\s*\\{([^}]*)\\}", "g"))]
    .map(m => m[1]).join("\n");
  const varsIn = s => new Set([...s.matchAll(/(--[\w-]+)\s*:/g)].map(m => m[1]));
  const rootVars = varsIn(blocks(":root"));
  const darkVars = varsIn(blocks('html\\[data-theme="dark"\\]'));

  // Which tokens need a dark value is decided from their VALUE, not from a
  // hand-kept list. The list was a maintenance trap: every new token had to be
  // remembered, and --lifecycle-banner-h (42px) and --ring (a box-shadow built
  // from var(--acc)) were both reported as missing dark values when neither
  // could sensibly have one.
  //
  // A token needs its own dark value only if it hard-codes a colour. If its
  // value is pure geometry, or is composed entirely from other tokens, it
  // already follows whatever those tokens do.
  const declaredValue = name => {
    const m = blocks(":root").match(new RegExp(name + "\\s*:([^;]*)"));
    return m ? m[1] : "";
  };
  const hardCodesColour = v => {
    const withoutVars = v.replace(/var\(--[\w-]+[^)]*\)/g, "");
    // "transparent" is not a colour that can differ between themes — it is the
    // absence of one — so a token like --ring, which is a box-shadow built from
    // var(--acc) fading to transparent, follows --acc and needs no dark value.
    return /#[0-9a-f]{3,8}\b|\brgba?\(|\bhsla?\(|\b(white|black|grey|gray|red|green|blue)\b/i
      .test(withoutVars);
  };

  // The chrome (command bar, section spine, live column) ships in one of three
  // configurations, and two of them are legitimate:
  //   SHARED — the surround is one dark instrument frame in both themes, so its
  //            tokens are declared once, in :root.
  //   THEMED — the surround follows the theme, so EVERY chrome token has a dark
  //            value, like any other colour.
  // What is not legitimate is half of each: a chrome that is partly themed has
  // some tokens silently falling back to the other theme's literals. So the
  // chrome is checked for internal consistency rather than for one fixed shape.
  const rootChrome = [...rootVars].filter(v => v.startsWith("--chrome"));
  const darkChrome = [...darkVars].filter(v => v.startsWith("--chrome"));
  const chromeShared = darkChrome.length === 0;
  const chromeThemed = rootChrome.every(v => darkVars.has(v));

  const isExempt = v =>
    !hardCodesColour(declaredValue(v)) || (chromeShared && v.startsWith("--chrome"));
  const undarked = [...rootVars].filter(v => !isExempt(v) && !darkVars.has(v));
  check(undarked.length === 0, "every themeable token has a dark-mode value" +
        (undarked.length ? " -> " + undarked.join(", ") : ""));

  const chromeGaps = rootChrome.filter(v => !darkVars.has(v));
  check(chromeShared || chromeThemed,
        "the chrome is either wholly shared or wholly themed, not half of each" +
        (chromeShared || chromeThemed ? "" : " -> themed but missing in dark: " +
         chromeGaps.join(", ")));
  check(rootChrome.length >= 5,
        `:root declares the chrome palette (got ${rootChrome.length} tokens)`);

  check(/\.toast\{[^}]*color:var\(--ink\)/.test(css.replace(/\s+/g, "")),
        "toasts set their own text colour (not inherited onto a literal bg)");
}

// REGRESSION: filenames reach innerHTML, so they are escaped.
check(window.eval("esc")('<img src=x onerror=alert(1)>') === "&lt;img src=x onerror=alert(1)&gt;",
      "esc() neutralises HTML in untrusted filenames");
check(window.eval("esc")('a&b"c') === "a&amp;b&quot;c", "esc() handles & and quotes");

// sortRows covers every measurement table (SSA was missing and threw)
for (const t of ["t", "d", "l", "s", "sa"]) {
  let threw = false;
  try { window.sortRows(t); } catch (e) { threw = true; }
  check(!threw, `sortRows('${t}') does not throw`);
}

// ---------------------------------------------------------------------------
// ACCESSIBILITY: controls that were mouse-only are now reachable and stateful.
// ---------------------------------------------------------------------------
check(document.querySelectorAll("nav.index button.idx-item").length > 5,
      "nav entries are real buttons (were div+onclick, so unfocusable)");
check(document.querySelector(".sec-hd").getAttribute("tabindex") === "0" &&
      document.querySelector(".sec-hd").getAttribute("aria-expanded") !== null,
      "section headers are keyboard-operable and expose expanded state");
{
  const hd = document.querySelector("#s4 .sec-hd");
  hd.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
  check(document.getElementById("s4").classList.contains("collapsed") &&
        hd.getAttribute("aria-expanded") === "false",
        "Enter collapses a section and updates aria-expanded");
  window.expandSection("s4");
  check(hd.getAttribute("aria-expanded") === "true", "expandSection resets aria-expanded");
}
check(document.getElementById("tb-prog").getAttribute("role") === "progressbar" &&
      document.getElementById("tb-prog").getAttribute("aria-valuenow") !== null,
      "completion bar exposes progressbar semantics");
{
  const y = document.getElementById("yy0");
  window.eval("setyn")(0, "Y");
  check(y.getAttribute("aria-pressed") === "true", "Y/N toggles report pressed state");
  window.eval("setyn")(0, "N");
  check(y.getAttribute("aria-pressed") === "false", "Y/N pressed state clears");
}
// A row starts with NEITHER lit. N used to ship pre-selected and styled in
// var(--bg) — near-identical to the row behind it — so clicking N changed
// nothing on screen and an untouched row looked answered.
{
  const lit = id => document.getElementById(id).classList.contains("on");
  const rows = window.eval("INST").filter(x => !x.g);
  for (let i = 0; i < rows.length; i++) {
    document.getElementById("yy" + i).classList.remove("on");
    document.getElementById("yn" + i).classList.remove("on");
  }
  check(!lit("yy0") && !lit("yn0"), "a fresh row has neither Y nor N lit");
  window.eval("setyn")(0, "N");
  check(lit("yn0") && !lit("yy0"), "clicking N lights N — the click is visible");
  window.eval("setyn")(0, "Y");
  check(lit("yy0") && !lit("yn0"), "clicking Y moves the light across");
  window.eval("pickyn")(0, "Y");
  check(!lit("yy0") && !lit("yn0"),
        "clicking an already-selected answer retracts it to unanswered");
  // Collection preserves all three states; silence is never converted to N.
  const usedOf = name => window.collect().instruments.find(x => x.name === name)?.used;
  window.eval("setyn")(0, null);
  check(usedOf(rows[0].n) === null, "an unanswered row exports null");
  check(document.getElementById("yy0").getAttribute("aria-pressed") === "false" &&
        document.getElementById("yn0").getAttribute("aria-pressed") === "false",
        "unanswered exposes neither button as pressed");
  window.eval("setyn")(0, "N");
  check(usedOf(rows[0].n) === "N", "an explicit N exports N");
  window.eval("setyn")(0, "Y");
  check(usedOf(rows[0].n) === "Y", "an explicit Y exports Y");
  window.eval("setyn")(0, null);
}
{
  const sk = document.querySelector("a.skip");
  check(!!sk, "skip link present");
  const target = sk && document.querySelector(sk.getAttribute("href"));
  check(!!target, "skip link target exists");
  // a skip link that lands on a non-focusable element scrolls but leaves
  // keyboard focus behind in the nav
  check(target && (target.hasAttribute("tabindex") ||
        /^(A|BUTTON|INPUT|SELECT|TEXTAREA)$/.test(target.tagName)),
        "skip link target can actually receive focus");
}
window.setst("hello", "ok");
check(document.getElementById("toasts").getAttribute("aria-live") !== null,
      "toast region is a live region (Archive/Download feedback was silent)");

// The four controls that were genuinely NOT keyboard-reachable (everything
// else in the app was already native and focusable — this is the real list).
{
  const nonNative = [
    [".idx-item", "nav entry"],
    [".sec-hd",   "section header"],
    [".rail",     "profile rail"],
  ];
  for (const [sel, label] of nonNative) {
    const el = document.querySelector(sel);
    const focusable = el && (el.tagName === "BUTTON" || el.getAttribute("tabindex") === "0");
    check(focusable, `${label} is focusable (was a non-focusable element)`);
  }
  // and the remove-from-queue "x": role + tabindex is not enough, it must ACT
  window.eval("_pendingAttach").pitwall.push({ name: "a.jpg", type: "image/jpeg" });
  window.eval("refreshAttachUI")();
  const x = document.querySelector(".att-x");
  check(!!x && x.getAttribute("tabindex") === "0", "remove-from-queue x is focusable");
  check(!!x && !!x.getAttribute("onkeydown"), "remove-from-queue x responds to Enter/Space");
  if (x) {
    // earlier tests leave files queued, so assert the DELTA, not an absolute
    const before = window.eval("_pendingTotal")();
    x.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    await settle();
    check(window.eval("_pendingTotal")() === before - 1, "Enter actually removes the queued file");
  }
}
// the focus rule must not be dead code: :where() contributes zero specificity,
// so an earlier draft lost the cascade to `.ri input{outline:none}`
{
  const css = [...document.querySelectorAll("style")].map(s => s.textContent).join("\n");
  check(!/:where\([^)]*input[^)]*\):focus-visible/.test(css),
        "focus rule does not rely on zero-specificity :where() against outline:none");
  check(/button:focus-visible/.test(css), "buttons get an explicit focus ring");
}

// ---------------------------------------------------------------------------
// Attachment limits: sheet 3 images (or one PDF), pitwall 6, stratigraphy 20.
// The whole-pit cap must be the SUM, or a category gets cut off under its own
// limit (a hardcoded 12 stopped stratigraphy at roughly its sixth photo).
// ---------------------------------------------------------------------------
{
  const lim = window.eval("_attachInfo").limits;
  check(lim.sheet === 3, `sheet limit is 3 (got ${lim.sheet})`);
  check(lim.pitwall === 6, `pitwall limit is 6 (got ${lim.pitwall})`);
  check(lim.stratigraphy === 20, `stratigraphy limit is 20 (got ${lim.stratigraphy})`);

  // queue 20 stratigraphy files: none may be refused by a stale cap
  const P = window.eval("_pendingAttach");
  P.sheet.length = 0; P.pitwall.length = 0; P.stratigraphy.length = 0;
  for (let i = 0; i < 20; i++) P.stratigraphy.push({ name: `s${i}.jpg`, type: "image/jpeg" });
  window.eval("refreshAttachUI")();
  check(window.eval("_pendingTotal")() === 20, "20 stratigraphy photos can be queued at once");
  const row = [...document.querySelectorAll(".att-row")].find(r => /Stratigraphy/.test(r.textContent));
  // The count is shown WITHOUT a denominator. 20 is the per-LAYER cap, and the
  // server applies no per-category limit to stratigraphy at all — the only
  // ceiling on the pit is the 150-file total. "20/20" claimed a limit that does
  // not exist and read as full at the 20th photo of a pit allowed 150.
  check(row && /Stratigraphy\s*20(?!\s*\d)/.test(row.textContent.replace(/\s+/g, " ")),
        `UI shows the stratigraphy count (got ${row && JSON.stringify(row.textContent.trim())})`);
  check(row && !/20\s*\/\s*20/.test(row.textContent),
        "and does not claim a 20-file category cap");
  P.stratigraphy.length = 0; window.eval("refreshAttachUI")();
}

// ---------------------------------------------------------------------------
// Dark mode: .tog is the only button class that declared no background, so the
// sort / render-profile / rho-toggle buttons fell back to the UA's light
// ButtonFace with --ink2 text on it.
// ---------------------------------------------------------------------------
{
  const css = [...document.querySelectorAll("style")].map(s => s.textContent).join("\n");
  const flat = css.replace(/\/\*[\s\S]*?\*\//g, "");
  const togBody = (flat.match(/\.tog\{([^}]*)\}/) || [, ""])[1];
  check(/background\s*:/.test(togBody),
        ".tog declares its own background (UA ButtonFace is light even in dark mode)");
  check(/html\[data-theme="dark"\]\{[^}]*color-scheme\s*:\s*dark/.test(flat.replace(/\s*\n\s*/g, "")),
        "dark theme declares color-scheme so native widgets follow it");

  // Every <button> must end up with a background — asked per BUTTON, not per
  // class. Buttons here are composed ("tb-csv tb-workspace", "workspace-link
  // workspace-photo-action") where one class carries the look and the other
  // carries a modifier, so a class-wise test flags the modifier and reports a
  // bug on a button that is perfectly styled. What matters is that no BUTTON
  // falls through to the UA's ButtonFace, which stays light in dark mode.
  const declares = c => {
    const all = [...flat.matchAll(new RegExp("\\." + c + "\\{([^}]*)\\}", "g"))];
    return all.some(m => /background/.test(m[1]));
  };
  const styled = c =>
    [...flat.matchAll(new RegExp("\\." + c + "\\{([^}]*)\\}", "g"))].length > 0;
  const missing = [];
  document.querySelectorAll("button").forEach(b => {
    const cs = [...b.classList];
    if (!cs.length || !cs.some(styled)) return;      // unstyled/utility buttons
    if (!cs.some(declares)) missing.push(cs.join("."));
  });
  check(missing.length === 0,
        "no button class relies on the UA default background" +
        (missing.length ? " -> " + missing.join(", ") : ""));
}

// ---------------------------------------------------------------------------
// Pit-sheet rule matrix: ONE PDF, or up to THREE images, never a mix.
// Enforced twice — at selection (below) and again server-side. Raising the cap
// from 2 to 3 broke the server check, which tested `counts == 1` (only ever
// correct at a cap of 2) and so let a PDF through as the THIRD sheet file.
// ---------------------------------------------------------------------------
{
  const F = (n, t) => new window.File([new Uint8Array([1, 2, 3])], n, { type: t });
  const img = n => F(n || "photo.jpg", "image/jpeg");
  const pdf = n => F(n || "sheet.pdf", "application/pdf");
  const savedPending = window.eval("_pendingAttach").sheet.slice();
  const savedInfo = { ...window.eval("_attachInfo") };

  // returns true when the batch sequence ends with everything accepted
  const attempt = async batches => {
    window.eval("_pendingAttach").sheet.length = 0;
    window.eval("_attachInfo").counts = {};
    window.eval("_attachInfo").sheetPdf = false;
    let blocked = false;
    for (const b of batches) {
      document.getElementById("attach-msg").textContent = "";
      Object.defineProperty(document.getElementById("att-sheet"), "files",
                            { value: b, configurable: true });
      await window.eval("uploadAttachment")("att-sheet", "sheet");
      if (document.getElementById("attach-msg").classList.contains("err")) blocked = true;
    }
    return !blocked;
  };

  // await, every time. attempt() is async, and `!promiseOrValue` on a pending
  // Promise is always false — so before these awaits were added, every NEGATIVE
  // case here failed unconditionally and every POSITIVE one passed without
  // testing anything. The pit-sheet rule was reported as broken in six ways
  // while actually being intact, and untested in the other six.
  check(await attempt([[img("a.jpg")]]), "sheet: 1 image accepted");
  check(await attempt([[img("a.jpg")], [img("b.jpg")]]), "sheet: 2 images accepted");
  check(await attempt([[img("a.jpg")], [img("b.jpg")], [img("c.jpg")]]),
        "sheet: 3 images accepted (the new cap)");
  check(await attempt([[img("a.jpg"), img("b.jpg"), img("c.jpg")]]),
        "sheet: 3 images in ONE selection accepted");
  check(!await attempt([[img("a.jpg"), img("b.jpg"), img("c.jpg"), img("d.jpg")]]),
        "sheet: 4 images in one selection refused");
  check(!await attempt([[img("a.jpg"), img("b.jpg"), img("c.jpg")], [img("d.jpg")]]),
        "sheet: 4th image refused");
  check(await attempt([[pdf()]]), "sheet: 1 PDF accepted");
  check(!await attempt([[pdf("one.pdf")], [pdf("two.pdf")]]), "sheet: 2nd PDF refused");
  check(!await attempt([[pdf()], [img()]]), "sheet: image after a PDF refused");
  check(!await attempt([[img()], [pdf()]]), "sheet: PDF after an image refused (mix)");
  check(!await attempt([[pdf(), img()]]), "sheet: PDF+image in one selection refused");
  check(!await attempt([[pdf("one.pdf"), pdf("two.pdf")]]), "sheet: 2 PDFs in one selection refused");

  // restore so later assertions see the state they expect
  window.eval("_pendingAttach").sheet.length = 0;
  savedPending.forEach(f => window.eval("_pendingAttach").sheet.push(f));
  Object.assign(window.eval("_attachInfo"), savedInfo);
  document.getElementById("attach-msg").textContent = "";
  window.eval("refreshAttachUI")();
}

// ---------------------------------------------------------------------------
// REGRESSION: the toggle pills (§2 Weather, §3 Ground, §1 density cutter) hid
// their radio/checkbox with display:none. That does not just hide it — it
// removes it from the tab order and the accessibility tree, so all 46 pills
// were unreachable by keyboard and silent to a screen reader. Precipitation,
// sky, wind, ground condition, vegetation and melt evidence could not be
// entered without a mouse at all.
// ---------------------------------------------------------------------------
{
  const inputs = [...document.querySelectorAll(".tog input")];
  check(inputs.length > 40, `toggle pills found (${inputs.length})`);
  const hidden = inputs.filter(i => window.getComputedStyle(i).display === "none");
  check(hidden.length === 0,
        `no toggle input is display:none (${hidden.length} still hidden)`);

  // Weather is a native checkbox group: each option remains focusable, and
  // several observed conditions may reach the scientific payload together.
  const pr = [...document.querySelectorAll('input[name="pr"]')];
  pr[1].focus();
  check(document.activeElement === pr[1], "a weather pill can take focus");
  pr[1].checked = true;
  pr[1].dispatchEvent(new window.Event("change", { bubbles: true }));
  pr[2].checked = true;
  pr[2].dispatchEvent(new window.Event("change", { bubbles: true }));
  check(pr[1].closest(".tog").classList.contains("on") &&
        pr[2].closest(".tog").classList.contains("on"),
        "multiple weather observations can stay selected");
  check(JSON.stringify(window.collect().weather.precip_rate)===
        JSON.stringify([pr[1].value,pr[2].value]),
        "multiple weather observations reach collect() as an array");
  pr[0].checked=true;
  pr[0].dispatchEvent(new window.Event("change",{bubbles:true}));
  check(pr[0].checked && pr.slice(1).every(r=>!r.checked),
        "precipitation None clears specific observations");
  pr[2].checked=true;
  pr[2].dispatchEvent(new window.Event("change",{bubbles:true}));
  check(!pr[0].checked && pr[2].checked,
        "a specific precipitation observation clears None");

  // the ring is drawn on the PILL, because the input itself is invisible
  const css = [...document.querySelectorAll("style")].map(s => s.textContent)
                .join("\n").replace(/\/\*[\s\S]*?\*\//g, "");
  check(/\.tog:has\(input:focus-visible\)/.test(css),
        "focus ring targets the pill via :has(input:focus-visible)");

  // still visually hidden, but the native focus/hit target must fill the
  // pill. A 1 × 1 px target can make Safari scroll the field card to reveal it.
  const st = window.getComputedStyle(inputs[0]);
  check(st.opacity === "0" && st.position === "absolute",
        "toggle input stays visually hidden (opacity 0, out of flow)");
  check(st.width === "100%" && st.height === "100%",
        "toggle input fills the complete pill instead of a 1px focus target");
  check(st.pointerEvents !== "none",
        "toggle input receives the click directly without label focus indirection");

  // populate() accepts both new arrays and legacy one-value strings.
  window.eval("setChecks")("pr", [pr[1].value,pr[3].value]);
  window.eval("refreshTogs")();
  check(pr[1].closest(".tog").classList.contains("on") &&
        pr[3].closest(".tog").classList.contains("on"),
        "populate/refreshTogs restores multiple weather observations");
  window.eval("setChecks")("pr", pr[2].value);
  window.eval("refreshTogs")();
  check(pr[2].checked && pr.filter(r=>r!==pr[2]).every(r=>!r.checked),
        "populate accepts a legacy scalar weather value");
  check(!pr[0].closest('[data-clearable-radio]'),
        "weather checkboxes need no separate clear-selection action");

  // Optional radio groups elsewhere keep native radio semantics and expose one
  // explicit way back to unanswered. The action appears only after an answer.
  // "gr" (ground roughness), not "gc": ground CONDITION is a multi-select
  // checkbox group, where unticking is already the way back to unanswered, so
  // it correctly has no clear action. Pick a group that is still a radio — the
  // thing this block is actually about.
  const gc=[...document.querySelectorAll('input[name="gr"]')];
  window.eval("setRadio")("gr",gc[1].value);
  window.eval("refreshTogs")();
  const gcGroup=gc[0].closest('[data-clearable-radio]');
  const clear=gcGroup&&gcGroup.nextElementSibling;
  check(clear&&clear.classList.contains('radio-clear'),
        "optional radio group receives a clear-selection action");
  check(clear&&clear.type==='button',
        "clear-selection action cannot submit or archive the form");
  check(clear&&!clear.hidden,
        "clear-selection action is visible while a radio answer exists");
  clear.click();
  check(gc.every(r=>!r.checked),
        "clear-selection action returns the optional group to unanswered");
  check(window.collect().ground.roughness==='',
        "unanswered radio state reaches collect() without inventing a value");
  check(clear.hidden,
        "clear-selection action hides again after the group is cleared");
  check(document.activeElement===gc[0],
        "focus returns to the native radio group after clearing");
  window.eval("setRadio")("gr",gc[1].value);
  window.eval("refreshTogs")();
}

// Checkbox pills are NOT radios: each is its own tab stop, Space toggles rather
// than arrow keys, several may be on at once, and one can be turned back OFF.
// The visually-hidden-but-focusable fix has to preserve all of that, so it is
// asserted separately from the radio groups.
{
  const ids = ["dc100", "dc250", "dc1000"];
  const els = ids.map(i => document.getElementById(i));
  check(els.every(e => e && e.type === "checkbox"),
        "density cutter is checkboxes (multi-select), not radios");
  els.forEach(e => { e.checked = false; e.dispatchEvent(new window.Event("change", { bubbles: true })); });

  els[0].focus();
  check(document.activeElement === els[0], "each checkbox pill is its own tab stop");

  els.forEach(e => { e.checked = true; e.dispatchEvent(new window.Event("change", { bubbles: true })); });
  check(els.every(e => e.closest(".tog").classList.contains("on")),
        "every selected checkbox pill lights up");
  check(window.collect().meta.density_cutter === "100, 250, 1000 cc",
        `all three cutters reach collect() (got ${JSON.stringify(window.collect().meta.density_cutter)})`);

  els[1].checked = false;
  els[1].dispatchEvent(new window.Event("change", { bubbles: true }));
  check(window.collect().meta.density_cutter === "100, 1000 cc",
        "a checkbox can be turned back off (radios cannot)");
  check(!els[1].closest(".tog").classList.contains("on"),
        "deselected pill goes dark again");

  els.forEach(e => { e.checked = false; e.dispatchEvent(new window.Event("change", { bubbles: true })); });
}

// A ring drawn OUTSIDE a control that sits flush inside an overflow:hidden
// container gets three of its four sides clipped. `.add` spans the full width
// of `.pw` and touches its bottom edge, so it needs an inset ring.
{
  const css = [...document.querySelectorAll("style")].map(s => s.textContent)
                .join("\n").replace(/\/\*[\s\S]*?\*\//g, "").replace(/\s*\n\s*/g, "");
  check(/\.pw\{[^}]*overflow:hidden/.test(css), ".pw still clips (the constraint this guards)");
  check(/\.add:focus-visible\{[^}]*outline-offset:-2px/.test(css),
        "the add-row button uses an INSET focus ring so it is not clipped");
}

// Selecting a photo must never upload it, even on an archived pit. Everywhere
// else nothing reaches the server until Archive; photos used to be the one
// exception, and there is no delete route to undo a mistaken upload.
{
  const before = window.eval("_pendingAttach").pitwall.length;
  document.getElementById("site").value = "GM1";
  document.getElementById("date").value = "2026-02-10";
  window.eval("updateId()");
  const pid = document.getElementById("pitid").textContent.trim();
  window.eval(`_loaded_site_id='site-test';_loaded_pit_id=${JSON.stringify(pid)}`);
  const ids = window.eval("INST").filter(x => !x.g).map(x => x.n);
  window.eval("setyn")(ids.indexOf("Pit pictures"), "Y");
  window.eval("refreshAttachUI()");
  check(window.eval("attachmentsEnabled()"), "test setup: pit reads as archived and loaded");

  let sent = 0;
  const realFetch = window.fetch;
  window.fetch = (u, o) => { if (String(u).includes("/api/attach/")) sent++; return realFetch(u, o); };
  const f = new window.File([new Uint8Array([9, 9, 9])], "onedit.jpg", { type: "image/jpeg" });
  Object.defineProperty(document.getElementById("att-pitwall"), "files",
                        { value: [f], configurable: true });
  await window.eval("uploadAttachment")("att-pitwall", "pitwall");
  check(sent === 0, "selecting a photo on an archived pit sends nothing");
  check(window.eval("_pendingAttach").pitwall.length === before + 1,
        "the photo is queued instead");
  window.fetch = realFetch;
  window.eval("_pendingAttach").pitwall.length = before;
  window.eval("_loaded_site_id=null;_loaded_pit_id=null");
  window.eval("refreshAttachUI()");
}

// The §11 notice is informational only. An "upload now" link used to sit here,
// duplicating what Archive does — a second path invites the question of which
// button to press, when Archive is the one that saves a pit.
{
  const ids = window.eval("INST").filter(x => !x.g).map(x => x.n);
  window.eval("setyn")(ids.indexOf("Pit pictures"), "Y");
  document.getElementById("site").value = "GM1";
  document.getElementById("date").value = "2026-02-10";
  window.eval("updateId()");
  window.eval(`_loaded_site_id='site-test';_loaded_pit_id=${JSON.stringify(document.getElementById("pitid").textContent.trim())}`);
  const f = new window.File([new Uint8Array([4, 5, 6])], "queued.jpg", { type: "image/jpeg" });
  Object.defineProperty(document.getElementById("att-pitwall"), "files",
                        { value: [f], configurable: true });
  await window.eval("uploadAttachment")("att-pitwall", "pitwall");
  // (the chip itself is asserted earlier, synchronously; here the pit reads as
  // archived so the list re-renders via fetch and is not ready on this tick)
  check(!document.getElementById("attach-state"),
        "no standing notice — the pending chip is the only indicator");
  check(window.eval("_pendingAttach").pitwall.length === 1,
        "the photo sits in the queue rather than uploading");
  window.eval("_pendingAttach").pitwall.length = 0;
  window.eval("_loaded_site_id=null;_loaded_pit_id=null");
  window.eval("refreshAttachUI()");
}

// "No title" hides the title box rather than disabling it, and the three
// states must stay distinguishable in the payload.
{
  const row = document.getElementById("fig-title-row");
  const cb = document.getElementById("fig-notitle");
  check(!!cb && !!row, "§10 has a No-title checkbox and a title row");
  check(row.style.display !== "none", "title box visible by default");
  cb.checked = true; window.eval("toggleFigTitle()");
  check(row.style.display === "none", "ticking No title hides the box");
  check(window.collect().meta.figure_title === "", 'No title sends "" (not null)');
  cb.checked = false; window.eval("toggleFigTitle()");
  check(row.style.display !== "none", "unticking brings the box back");
  document.getElementById("fig-title").value = "Transect 3";
  check(window.collect().meta.figure_title === "Transect 3", "a typed title is sent verbatim");
  document.getElementById("fig-title").value = "";
  check(window.collect().meta.figure_title === null, "blank sends null = auto title");
}

// Changing the Pit ID of a loaded pit remains an edit of the same immutable
// site_id. CryoPit does not support templating or silent forking.
{
  const hint = document.getElementById("pidhint");
  const pid = document.getElementById("pitid");
  window.eval("_loaded_site_id='site-test';_loaded_pit_id='GM120260210'");
  pid.textContent = "GM120260210"; window.eval("onPitEdit()");
  check(/editing saved pit/.test(hint.textContent), "same ID → hint says this record is being edited");
  pid.textContent = "GM220260210"; window.eval("onPitEdit()");
  check(/identifier correction/.test(hint.textContent), "changed ID → hint says it corrects the same pit");
  check(/same pit/.test(hint.textContent), "and explicitly rules out a new record");
  check(hint.classList.contains("pid-new"), "the hint is highlighted when it changes meaning");
  window.eval("_loaded_site_id=null;_loaded_pit_id=null"); window.eval("onPitEdit()");
  check(!hint.classList.contains("pid-new"), "highlight clears for an unloaded form");
}

// Loading a pit must show the photos it ALREADY has. populate() refreshes the
// panel itself, but it runs before _loaded_pid is set — so attachmentsEnabled()
// was false and the list rendered empty until something else happened to
// refresh it (picking a new file). The counters are the answer to "can I still
// add photos to this pit?", so they have to be right on arrival.
{
  const realFetch = window.fetch;
  window.fetch = (u) => {
    const s = String(u);
    if (s.includes("/api/load/"))
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true, site_id: "site-load", pit_id: "LOADTEST", pit: {
        meta: { pit_id: "LOADTEST", location: "GM", site: "GM1", campaign: "WY2026",
                date: "2026-02-10", total_depth: 100, recorded_by: "Ana",
                surveyors: "Ana", flags: "None" },
        weather: {}, ground: {}, temperature: [], density: [], lwc: [], ssa: [],
        stratigraphy: [], ssa_calibration: {},
        instruments: [{ name: "Pit pictures", sn: "", used: "Y" }] } }) });
    if (s.includes("/api/attachments/"))
      return Promise.resolve({ ok: true, json: () => Promise.resolve({
        counts: { pitwall: 3 },
        limits: { sheet: 3, pitwall: 6, stratigraphy: 20 },
        attachments: [1, 2, 3].map(n => ({ category: "pitwall",
          filename: `WY2026_LOADTEST_20260210_pitwall_0${n}.jpg` })) }) });
    return realFetch(u);
  };
  const realConfirm = window.confirm; window.confirm = () => true;
  window.eval("loadPit")("site-load","LOADTEST");
  await new Promise(r => setTimeout(r, 150));
  const row = [...document.querySelectorAll(".att-row")].find(r => /Pit wall/.test(r.textContent));
  check(!!row && /3\/6/.test(row.textContent),
        `loaded pit shows its existing photo count (got ${row && row.textContent.trim()})`);
  check(document.querySelectorAll("#attach-list .att-chip").length === 3,
        "and lists the photos themselves");
  check(/3 attached/.test(document.getElementById("att-cnt").textContent),
        "§11 header reports them too");
  window.fetch = realFetch; window.confirm = realConfirm;
  window.eval("_loaded_site_id=null;_loaded_pit_id=null"); window.eval("refreshAttachUI()");
}

// Toggling "No title" requests a re-render — but only when a profile is already
// drawn. Stub drawProfile directly so this test stays independent of the
// separate empty-profile/data-validation rules inside drawProfile().
{
  const wrap = document.getElementById("profile-wrap");
  wrap.innerHTML = "";
  let renders = 0;
  const realDrawProfile = window.drawProfile;
  window.drawProfile = () => { renders++; };
  const cb = document.getElementById("fig-notitle");
  cb.checked = true; window.eval("toggleFigTitle()");
  check(renders === 0, "toggling with no profile on screen does not fire a render");
  const img = document.createElement("img"); img.src = "data:,"; wrap.appendChild(img);
  cb.checked = false; window.eval("toggleFigTitle()");
  check(renders === 1, "toggling with a profile on screen re-renders it");
  window.drawProfile = realDrawProfile; wrap.innerHTML = "";
}

// Duplicate photos must be REPORTED, not merely refused. The server always
// refused them; the archive toast only mentioned stored / rejected / pending,
// so three identical images read as "1 photo attached" with no account of the
// other two — and the only way to find out was to open the folder.
{
  const realFetch = window.fetch, realConfirm = window.confirm;
  window.confirm = () => true;
  let nth = 0;
  // The archive response must echo the pit id the FORM generated: after
  // archiving binds the form to the returned immutable site_id before photos flush.
  const loc0 = document.getElementById("loc");
  loc0.value = [...loc0.options].map(o => o.value).find(v => v && v !== "__c");
  const set0 = (id, v) => { const e = document.getElementById(id); if (e) e.value = v; };
  set0("site", "GM1"); set0("date", "2026-02-10"); set0("depth", "100");
  set0("recby", "Ana"); set0("surv", "Ana");
  window.eval("_pe=false"); window.eval("updateId()");
  const formPid = document.getElementById("pitid").textContent.trim();
  window.fetch = (u, o) => {
    const s = String(u);
    if (s.includes("/api/archive"))
      return Promise.resolve({ ok: true, json: () => Promise.resolve({
        ok: true, site_id: "site-archive", pit_id: formPid, updated: false, csv_count: 7, figure_count: 2, has_png: true, has_pdf: true,
        folder: "/data/exports/" + formPid }) });
    if (s.includes("/api/attach/")) {
      nth++;
      return Promise.resolve({ ok: true, json: () => Promise.resolve(
        nth === 1 ? { ok: true, filename: "DUPTOAST_sheet_01.jpg", category: "sheet" }
                  : { ok: true, duplicate: true, filename: "DUPTOAST_sheet_01.jpg",
                      category: "sheet" }) });
    }
    if (s.includes("/api/attachments/"))
      return Promise.resolve({ ok: true, json: () => Promise.resolve({
        counts: { sheet: 1 }, limits: { sheet: 3, pitwall: 6, stratigraphy: 20 },
        attachments: [{ category: "sheet", filename: "DUPTOAST_sheet_01.jpg" }] }) });
    return realFetch(u, o);
  };
  const F = n => new window.File([new Uint8Array([1, 2, 3])], n, { type: "image/jpeg" });
  const q = window.eval("_pendingAttach");
  q.sheet.length = 0; q.pitwall.length = 0; q.stratigraphy.length = 0;
  q.sheet.push(F("s.jpg"), F("s.jpg"), F("s.jpg"));   // the same image three times
  window.eval("doArchive()");
  await new Promise(r => setTimeout(r, 400));
  // the ARCHIVE toast specifically — it is multi-line, so joining all toasts
  // would destroy the structure being asserted
  const toasts = document.querySelector('#toasts [data-tid="archive"]').textContent;
  // "Pit archived" / "Changes archived", not a bare "Archived": the workflow made
  // creating a record and updating one distinct acts, and the toast has to say
  // which just happened — the whole point of that work is that you can never
  // mistake an edit for a copy. This is a fresh archive, so it must be the
  // former; asserting the pair would let a regression swap them unnoticed.
  check(/^Pit archived/m.test(toasts),
        "archive toast opens by naming the outcome (got " +
        JSON.stringify(toasts.split("\n")[0]) + ")");
  check(!/GM120260210/.test(toasts.split("Export:")[0]),
        "the pit id is not repeated in the toast (the topbar chip carries it)");
  check(/Export: /.test(toasts), "export location is labelled");
  check(/CSVs: 7 · profile figure: PNG \+ PDF/.test(toasts),
        "CSV count and both figure formats are named separately");
  check(/Photos: 1 attached; 2 skipped \(duplicate\)/.test(toasts),
        "photo line reports stored and skipped separately");
  const msg = document.getElementById("attach-msg").textContent;
  check(/Pit sheet: 1 uploaded, 2 skipped \(already on/.test(msg),
        `§11 attributes skipped files to their category (got ${JSON.stringify(msg.trim())})`);
  window.fetch = realFetch; window.confirm = realConfirm;
  q.sheet.length = 0; window.eval("_loaded_site_id=null;_loaded_pit_id=null"); window.eval("refreshAttachUI()");
}

// Aggregate in the toast, per-category in §11. A bare "4 skipped" across three
// categories does not tell you which pile to look at.
{
  const realFetch = window.fetch, realConfirm = window.confirm;
  window.confirm = () => true;
  const seen = new Set();
  const loc = document.getElementById("loc");
  loc.value = [...loc.options].map(o => o.value).find(v => v && v !== "__c");
  const set = (id, v) => { const e = document.getElementById(id); if (e) e.value = v; };
  set("site", "GM1"); set("date", "2026-02-10"); set("depth", "100");
  set("recby", "Ana"); set("surv", "Ana");
  window.eval("_pe=false"); window.eval("updateId()");
  const formPid = document.getElementById("pitid").textContent.trim();
  window.fetch = (u, o) => {
    const s = String(u);
    if (s.includes("/api/archive"))
      return Promise.resolve({ ok: true, json: () => Promise.resolve({
        ok: true, site_id: "site-archive", pit_id: formPid, updated: false, csv_count: 7, figure_count: 2, has_png: true, has_pdf: true,
        folder: "/data/exports/" + formPid }) });
    if (s.includes("/api/attach/")) {
      const cat = o.body.get("category"), f = o.body.get("file");
      const key = cat + ":" + f.name.replace(/^dup-/, "");
      const isDup = seen.has(key); seen.add(key);
      return Promise.resolve({ ok: true, json: () => Promise.resolve(
        isDup ? { ok: true, duplicate: true, filename: key, category: cat }
              : { ok: true, filename: key, category: cat }) });
    }
    if (s.includes("/api/attachments/"))
      return Promise.resolve({ ok: true, json: () => Promise.resolve({
        counts: {}, limits: { sheet: 3, pitwall: 6, stratigraphy: 20 }, attachments: [] }) });
    return realFetch(u, o);
  };
  const F = n => new window.File([new Uint8Array([1, 2, 3])], n, { type: "image/jpeg" });
  const q = window.eval("_pendingAttach");
  q.sheet.length = 0; q.pitwall.length = 0; q.stratigraphy.length = 0;
  q.sheet.push(F("s.jpg"), F("dup-s.jpg"), F("dup-s.jpg"));                  // 3 identical
  q.pitwall.push(F("w1.jpg"), F("w2.jpg"), F("w3.jpg"), F("dup-w3.jpg"));    // 4, last repeats
  q.stratigraphy.push(F("t1.jpg"), F("t2.jpg"), F("t3.jpg"), F("dup-t3.jpg"));
  window.eval("doArchive()");
  await new Promise(r => setTimeout(r, 700));

  const toast = document.querySelector('#toasts [data-tid="archive"]').textContent;
  check(/Photos: 7 attached; 4 skipped \(duplicate\)/.test(toast),
        `toast aggregates across categories (got ${JSON.stringify(toast.split("\n").pop())})`);

  const msg = document.getElementById("attach-msg").textContent;
  check(/Pit sheet: 1 uploaded, 2 skipped \(already on/.test(msg), "§11: pit sheet line");
  check(/Pit wall: 3 uploaded, 1 skipped \(already on/.test(msg), "§11: pit wall line");
  check(/Stratigraphy: 3 uploaded, 1 skipped \(already on/.test(msg), "§11: stratigraphy line");
  // the aggregate must equal the sum of the parts, or one of them is lying
  const per = [...msg.matchAll(/(\d+) uploaded/g)].reduce((n, m) => n + +m[1], 0);
  const skip = [...msg.matchAll(/(\d+) skipped/g)].reduce((n, m) => n + +m[1], 0);
  check(per === 7 && skip === 4, `§11 lines sum to the toast totals (${per}/${skip})`);
  check(per + skip === 11, "every queued file is accounted for");

  window.fetch = realFetch; window.confirm = realConfirm;
  q.sheet.length = 0; q.pitwall.length = 0; q.stratigraphy.length = 0;
  window.eval("_loaded_site_id=null;_loaded_pit_id=null"); window.eval("refreshAttachUI()");
}

// Blocking errors and soft warnings must not look alike: one stops Archive,
// the other is advice. They shared the amber .soft-warn box, so
// "Blocks archive: top (120) exceeds total depth (100)" read as a suggestion.
{
  const css = [...document.querySelectorAll("style")].map(s => s.textContent)
                .join("\n").replace(/\/\*[\s\S]*?\*\//g, "").replace(/\s*\n\s*/g, "");
  check(/\.warn-block\{[^}]*color:var\(--red\)/.test(css), "blocking lines are red");
  check(/\.warn-soft\{[^}]*color:var\(--amber\)/.test(css), "advisory lines stay amber");
  check(/\.warn-box\.has-block\{/.test(css), "the box itself changes when it holds a blocker");

  // drive a real blocker: a density top above the pit's total depth
  document.getElementById("depth").value = "100";
  const body = document.getElementById("db");
  body.innerHTML = "";
  window.addRow("d");
  const ins = body.querySelectorAll("tr input");
  ins[0].value = "120"; ins[1].value = "90";
  ins[0].dispatchEvent(new window.Event("input", { bubbles: true }));
  if (typeof window.densityWarnings === "function") window.densityWarnings();
  const box = document.getElementById("d-warn");
  check(/Blocks archive/.test(box.innerHTML), "the blocker is reported");
  check(box.querySelector(".warn-block"), "and carries the blocking style");
  check(box.classList.contains("has-block"), "the box is marked as holding a blocker");
  body.innerHTML = "";
}

// The section index collapses on wide screens without losing its usefulness.
{
  const btn = document.getElementById("idx-collapse");
  check(!!btn, "index has a collapse control");
  check(!document.body.classList.contains("index-collapsed"), "expanded by default");
  window.eval("toggleIndex()");
  check(document.body.classList.contains("index-collapsed"), "collapses on click");
  check(btn.getAttribute("aria-expanded") === "false", "and reports the state");
  check(document.querySelectorAll(".idx-item").length === 12,
        "every section is still reachable while collapsed");
  check(document.querySelectorAll(".idx-pip").length === 12,
        "completion pips survive the collapse");
  window.eval("toggleIndex()");
  check(!document.body.classList.contains("index-collapsed"), "expands again");
}

// §9 completeness: unanswered is distinct from N, so silence cannot mean
// "nothing was used". Each of the two groups — kit carried, work done — needs
// either a Yes or an explicit "none".
{
  const done = () => window.eval("instChecklistDone()");
  const rows = window.eval("INST").filter(x => !x.g);
  for (let i = 0; i < rows.length; i++) window.eval("setyn")(i, "N");
  document.getElementById("none-inst").checked = false;
  document.getElementById("none-task").checked = false;
  window.eval("onNoneGroup('inst')"); window.eval("onNoneGroup('task')");
  check(!done(), "an untouched checklist is NOT complete");

  window.eval("setyn")(2, "Y");
  check(!done(), "one group answered is not enough");
  document.getElementById("none-task").checked = true; window.eval("onNoneGroup('task')");
  check(done(), "Yes in one group + 'none' in the other completes it");

  window.eval("setyn")(2, "N");
  check(!done(), "removing the only Yes makes that group unanswered again");
  document.getElementById("none-inst").checked = true; window.eval("onNoneGroup('inst')");
  check(done(), "an all-empty pit CAN be complete once both groups are affirmed");

  // the affirmation and a Yes must not be able to contradict each other
  const btn = document.querySelector('table.it[data-group="inst"] .yn button');
  check(btn.disabled, "ticking 'none' locks that group's rows");
  const p = window.collect();
  check(p.meta.no_instruments === true && p.meta.no_tasks === true,
        "both affirmations reach the payload");
  check(p.instruments.every(i => i.used !== "Y"),
        "and no instrument is left marked Yes underneath");

  // round-trip
  document.getElementById("none-inst").checked = false;
  document.getElementById("none-task").checked = false;
  window.eval("onNoneGroup('inst')"); window.eval("onNoneGroup('task')");
  window.populate(JSON.parse(JSON.stringify(p)));
  check(document.getElementById("none-inst").checked &&
        document.getElementById("none-task").checked,
        "affirmations survive a save/load round-trip");
  check(document.querySelector('table.it[data-group="inst"] .yn button').disabled,
        "and the lock is reapplied on load");

  document.getElementById("none-inst").checked = false;
  document.getElementById("none-task").checked = false;
  window.eval("onNoneGroup('inst')"); window.eval("onNoneGroup('task')");
}

// A warning should sit on screen as insistently as an error until it is fixed.
{
  const css = [...document.querySelectorAll("style")].map(s => s.textContent)
                .join("\n").replace(/\/\*[\s\S]*?\*\//g, "").replace(/\s*\n\s*/g, "");
  check(/\.warn-box\.has-warn\{[^}]*border-style:solid/.test(css),
        "a box holding warnings gets a solid border, like an error box");
  check(/\.warn-box\.has-warn\{[^}]*background:/.test(css), "and a tint");
}

// Completion must reflect DATA, not the existence of a row, and the
// denominator must reflect what THIS pit declares it needs.
{
  document.getElementById("tb").innerHTML = "";
  window.eval("tick()");
  const green = id => document.getElementById(id).classList.contains("done");
  window.addRow("t");
  window.eval("tick()");
  check(!green("p4"), "an empty row does not turn the section green");
  const ins = document.querySelectorAll("#tb tr input");
  ins[0].value = "100"; ins[1].value = "-8";
  window.eval("tick()");
  check(green("p4"), "typing the measurement does");

  const lbl = () => document.getElementById("cl-lbl").textContent;
  const ids = window.eval("INST").filter(x => !x.g).map(x => x.n);
  const base = +lbl().match(/of (\d+)/)[1];
  window.eval("setyn")(ids.indexOf("Digital LWC"), "Y"); window.eval("tick()");
  check(+lbl().match(/of (\d+)/)[1] === base + 1, "declaring an LWC instrument adds LWC to the denominator");
  window.eval("setyn")(ids.indexOf("SSA / NIR Box"), "Y"); window.eval("tick()");
  check(+lbl().match(/of (\d+)/)[1] === base + 2, "declaring the SSA box adds SSA");
  window.eval("setyn")(ids.indexOf("Digital LWC"), "N");
  window.eval("setyn")(ids.indexOf("SSA / NIR Box"), "N"); window.eval("tick()");
  check(+lbl().match(/of (\d+)/)[1] === base,
        "a pit that declares neither is not marked down for omitting them");
  document.getElementById("tb").innerHTML = "";
  window.eval("tick()");
}

// Section headers carry status, and keep carrying it while collapsed — the
// header is the only part of a folded section that remains on screen.
{
  const glyph = id => document.querySelector("#" + id + " .sec-status");
  document.getElementById("depth").value = "100";
  document.getElementById("db").innerHTML = "";
  window.addRow("d");
  const di = document.querySelectorAll("#db tr input");
  di[0].value = "120"; di[1].value = "90"; di[2].value = "250";   // top above total depth
  di[0].dispatchEvent(new window.Event("input", { bubbles: true }));
  if (window.densityWarnings) window.densityWarnings();
  window.eval("tick()");
  check(glyph("s5") && glyph("s5").textContent === "✖", "a blocking section shows ✖");
  check(glyph("s5").classList.contains("is-block"), "styled as a blocker");
  check(/blocks archive/.test(glyph("s5").getAttribute("aria-label") || ""),
        "and says so to a screen reader, not by colour alone");
  check(document.getElementById("p5").classList.contains("block"),
        "the sidebar pip agrees with the header");

  document.getElementById("s5").classList.add("collapsed");
  check(!!document.querySelector("#s5 .sec-hd .sec-status"),
        "the glyph survives a collapse (the whole point of putting it here)");
  document.getElementById("s5").classList.remove("collapsed");
  document.getElementById("db").innerHTML = "";
  if (window.densityWarnings) window.densityWarnings();
  window.eval("tick()");
}

// The checklist is the "what's left" panel: it must count §9, name the section
// to go to, sort in form order, and mark a section that BLOCKS archive
// differently from one that is merely unfilled.
{
  const lbl = () => document.getElementById("cl-lbl").textContent;
  const ids = window.eval("INST").filter(x => !x.g).map(x => x.n);
  ["inst", "task"].forEach(g => {
    const cb = document.getElementById("none-" + g);
    if (cb) { cb.checked = false; window.eval(`onNoneGroup('${g}')`); }
  });
  for (let i = 0; i < ids.length; i++) window.eval("setyn")(i, "N");
  window.eval("tick()");
  check(/of 10 required items/.test(lbl()),
        `base denominator is 10 and the noun is "items" (got ${JSON.stringify(lbl())})`);
  window.eval("setyn")(ids.indexOf("Digital LWC"), "Y"); window.eval("tick()");
  check(/of 11 required items/.test(lbl()), "declaring LWC makes it 11");
  window.eval("setyn")(ids.indexOf("SSA / NIR Box"), "Y"); window.eval("tick()");
  check(/of 12 required items/.test(lbl()), "declaring SSA too makes it 12");

  const rows = [...document.querySelectorAll("#cl-items .ci")];
  check(rows.length === 12, "every counted item has a row");
  check(rows.every(r => r.tagName === "BUTTON" && r.dataset.t),
        "each row is a button that navigates to its section");
  const nums = rows.map(r => r.querySelector(".cl-num").textContent);
  check(nums.join(",") === [...nums].sort().join(","),
        `rows are in form order (got ${nums.join(",")})`);
  check(rows.some(r => /Instruments & tasks/.test(r.textContent)),
        "§9 appears in the list now that it counts");

  // an item can hold data AND block the archive — red must win over green
  document.getElementById("depth").value = "100";
  document.getElementById("db").innerHTML = "";
  window.addRow("d");
  const di = document.querySelectorAll("#db tr input");
  di[0].value = "120"; di[1].value = "90"; di[2].value = "250";
  di[0].dispatchEvent(new window.Event("input", { bubbles: true }));
  if (window.densityWarnings) window.densityWarnings();
  window.eval("tick()");
  const dRow = [...document.querySelectorAll("#cl-items .ci")]
    .find(r => /Density/.test(r.textContent));
  check(dRow && dRow.querySelector(".cd.bad"),
        "a blocking section is marked bad in the checklist, not ticked");
  document.getElementById("db").innerHTML = "";
  if (window.densityWarnings) window.densityWarnings();
  for (let i = 0; i < ids.length; i++) window.eval("setyn")(i, "N");
  window.eval("tick()");
}

// The status glyph must track validation IMMEDIATELY, in both directions.
// It used to be painted by tick(), which reads the warning boxes — but those
// were recomputed 300 ms later by densityWarnings() via scheduleMini(), so a
// tick() saw the state from BEFORE the keystroke that triggered it. A new error
// appeared only on the next unrelated edit, and a corrected one stayed red.
{
  const g = () => {
    const e = document.querySelector("#s5 .sec-status");
    return e ? e.textContent : "(none)";
  };
  const type = (el, v) => {
    el.value = v;
    el.dispatchEvent(new window.Event("input", { bubbles: true }));
  };
  document.getElementById("depth").value = "100";
  document.getElementById("db").innerHTML = "";
  window.addRow("d");
  const di = document.querySelectorAll("#db tr input");
  type(di[1], "90"); type(di[2], "250");

  type(di[0], "120");                       // top above the pit's total depth
  check(g() === "✖", `blocker shows at once, with no debounce (got ${JSON.stringify(g())})`);
  type(di[0], "95");                        // valid
  check(g() !== "✖", "and clears at once when corrected");
  type(di[0], "150");
  check(g() === "✖", "reappears when broken again");
  type(di[0], "99");
  check(g() !== "✖", "and clears again — not a one-shot");

  // placement: it describes the section, so it sits with the name, not with
  // the controls
  // The title may be wrapped (.sec-heading surrounds the title and
  // its subtitle), so this asks WHERE the glyph sits relative to the naming
  // block rather than assuming the title is a direct child. The intent is
  // unchanged: the glyph describes the section, so it belongs with the name and
  // ahead of the controls.
  const hd = document.querySelector("#s5 .sec-hd");
  const kids = [...hd.children];
  const names = kids.map(c => c.className.split(" ")[0]);
  const namingBlock = kids.findIndex(c => c.querySelector(".sec-title") ||
                                          c.classList.contains("sec-title"));
  check(namingBlock !== -1, `the header names the section (got ${names.join(" | ")})`);
  check(names.indexOf("sec-status") === namingBlock + 1,
        `glyph sits directly after the title (got ${names.join(" | ")})`);
  check(names.indexOf("sec-status") < names.indexOf("tog"),
        `and before the sort button (got ${names.join(" | ")})`);

  document.getElementById("db").innerHTML = "";
  if (window.densityWarnings) window.densityWarnings();
  window.eval("tick()");
}

// BATCH 2 — per-layer photos and the evidence-driven checklist.
{
  const type = (el, v) => { el.value = v; el.dispatchEvent(new window.Event("input", { bubbles: true })); };
  const F = n => new window.File([new Uint8Array([1, 2, 3])], n, { type: "image/jpeg" });
  const ids = window.eval("INST").filter(x => !x.g).map(x => x.n);
  const q = window.eval("_pendingAttach");
  q.sheet.length = 0; q.pitwall.length = 0; q.stratigraphy.length = 0;
  window.eval("_attachInfo").counts = {};
  // PRECONDITION. An earlier block sets every checklist row to N, and a row
  // marked N now closes its own photo input and the §7 cameras with it. That
  // used to have no effect here, so this block inherited the state without
  // noticing. Say what it needs instead of depending on test order.
  window.eval("setyn")(ids.indexOf("Stratigraphy pictures"), "Y");
  window.eval("setyn")(ids.indexOf("Pit pictures"), "Y");
  window.eval("refreshAttachUI()");
  document.getElementById("sb").innerHTML = "";
  window.addRow("s"); window.addRow("s");
  const rows = [...document.querySelectorAll("#sb tr")];
  const cam = tr => tr.querySelector(".cam");

  check(!!cam(rows[0]), "each layer row has a camera button");
  check(cam(rows[0]).disabled, "disabled until the layer has depths — the interval IS the link");
  let i0 = rows[0].querySelectorAll("input[type=number]");
  type(i0[0], "100"); type(i0[1], "62");
  check(!cam(rows[0]).disabled, "enabled once top and bottom are entered");
  check(/100-062cm/.test(cam(rows[0]).title), "and names the interval it will file under");

  let i1 = rows[1].querySelectorAll("input[type=number]");
  type(i1[0], "62"); type(i1[1], "45");

  const inp = document.getElementById("att-strat");
  window.eval("pickLayerPhotos")(cam(rows[0]));
  Object.defineProperty(inp, "files", { value: [F("a.jpg"), F("b.jpg")], configurable: true });
  await window.eval("uploadAttachment")("att-strat", "stratigraphy");
  window.eval("pickLayerPhotos")(cam(rows[1]));
  Object.defineProperty(inp, "files", { value: [F("c.jpg")], configurable: true });
  await window.eval("uploadAttachment")("att-strat", "stratigraphy");

  const keys = q.stratigraphy.map(x => x.key);
  check(keys.join(",") === "100-062cm,100-062cm,062-045cm",
        `each photo carries the layer it was attached from (got ${keys.join(",")})`);
  check(q.stratigraphy.every(x => x.file instanceof window.File),
        "the queue holds {file, top, bottom}, not bare Files");
  window.eval("refreshLayerCams()");
  check(cam(rows[0]).querySelector(".cam-n").textContent === "2" &&
        cam(rows[1]).querySelector(".cam-n").textContent === "1",
        "per-layer counts show on the rows they belong to");

  // evidence forces the checklist row and locks it
  window.eval("syncChecklistFromEvidence()");
  const si = ids.indexOf("Stratigraphy pictures");
  check(document.getElementById("yy" + si).classList.contains("on"),
        "attaching layer photos forces 'Stratigraphy pictures' to Yes");
  check(document.getElementById("yn" + si).disabled,
        "and locks it — you cannot claim none while photos are attached");
  check(document.getElementById("none-task").disabled,
        "'No tasks done' is unavailable while photos exist");

  // ...and the reverse direction closes the door instead of flipping anything
  q.stratigraphy.length = 0; q.pitwall.length = 0;
  window.eval("syncChecklistFromEvidence()");
  check(!document.getElementById("yn" + si).disabled, "removing the photos unlocks the row again");
  check(!document.getElementById("none-task").disabled, "and re-enables 'No tasks done'");
  document.getElementById("none-task").checked = true;
  window.eval("onNoneGroup('task')");
  window.eval("refreshLayerCams()");
  check(cam(rows[0]).disabled, "with 'No tasks done' ticked the cameras are closed");
  check(/No tasks done/.test(cam(rows[0]).title), "and say why");
  document.getElementById("none-task").checked = false;
  // retract=true is what the checkbox's own onchange passes when a PERSON
  // unticks it; without it the rows stay on the N that ticking set, and an N
  // row now closes its own cameras. The no-argument form is populate()'s path.
  window.eval("onNoneGroup('task', true)");
  window.eval("refreshLayerCams()");
  check(!cam(rows[0]).disabled, "unticking reopens them");

  // SSA is 1:1 with its checklist row; LWC is not, so LWC only warns
  document.getElementById("ssab").innerHTML = "";
  window.addRow("sa");
  const sa = document.querySelectorAll("#ssab tr input");
  type(sa[0], "80");
  window.eval("syncChecklistFromEvidence()");
  const ssi = ids.indexOf("SSA / NIR Box");
  check(document.getElementById("yy" + ssi).classList.contains("on"),
        "SSA measurements force 'SSA / NIR Box' to Yes");
  check(document.getElementById("yn" + ssi).disabled, "and lock it");

  document.getElementById("lb").innerHTML = "";
  window.addRow("l");
  const li = document.querySelectorAll("#lb tr input");
  type(li[0], "100"); type(li[1], "90");
  if (window.densityWarnings) window.densityWarnings();
  const lw = document.getElementById("l-warn");
  check(lw && lw.style.display !== "none", "LWC rows with no declared instrument warn");
  check(/no LWC instrument/.test(lw.textContent), "and say what is inconsistent");
  window.eval("setyn")(ids.indexOf("Digital LWC"), "Y");
  type(li[2], "3.2");          // finish the row, or the half-typed warning stands
  if (window.densityWarnings) window.densityWarnings();
  check(!/no LWC instrument/.test(lw.textContent),
        "declaring an instrument clears the instrument warning");
  check(!document.getElementById("yn" + ids.indexOf("Digital LWC")).disabled,
        "LWC is never forced — the form does not say WHICH instrument was used");

  document.getElementById("sb").innerHTML = "";
  document.getElementById("ssab").innerHTML = "";
  document.getElementById("lb").innerHTML = "";
  window.eval("tick()");
}

// §4 had no live validation at all — every other measurement section had a
// warning box and temperature did not, so a height outside the pit was
// accepted silently on screen and only refused when Archive was pressed.
{
  const type = (el, v) => { el.value = v; el.dispatchEvent(new window.Event("input", { bubbles: true })); };
  const g = () => { const e = document.querySelector("#s4 .sec-status"); return e ? e.textContent : ""; };
  const box = () => document.getElementById("t-warn");
  check(!!box(), "§4 has a warning box like its neighbours");
  document.getElementById("depth").value = "100";
  document.getElementById("tb").innerHTML = "";
  window.addRow("t");
  const ti = document.querySelectorAll("#tb tr input");

  type(ti[0], "150"); type(ti[1], "-8");
  check(g() === "✖", "a height beyond total depth blocks, and shows at once");
  check(/outside -10–100 cm/.test(box().textContent),
        "the live message states the same range the server enforces");
  type(ti[0], "80");
  check(g() !== "✖", "correcting it clears immediately");

  type(ti[1], "5");
  check(g() === "⚠", "an above-freezing temperature warns rather than blocks");
  check(/above freezing/.test(box().textContent), "and explains why");
  type(ti[1], "-3");
  // Assert the above-freezing message is gone, rather than the section glyph
  // being empty. The glyph was a proxy that any other §4 warning would trip —
  // and one legitimately does here: a lone reading at 80 cm in a 100 cm pit
  // spans neither the surface nor the ground, so the coverage check fires.
  check(!/above freezing/.test(box().textContent), "and clears when fixed");
  check(g() !== "✖", "without turning into a blocker");

  document.getElementById("tb").innerHTML = "";
  if (window.densityWarnings) window.densityWarnings();
  window.eval("tick()");
}

// Unticking "No instruments used" RETRACTS the claim, so rows return to
// unanswered. Leaving them lit on N would silently convert one click into
// fifteen explicit "not used" answers — exactly what the neither-lit default
// exists to prevent. But populate() calls the same function to reapply the
// lock after loading a pit, and must NOT wipe what it just restored.
{
  const ids = window.eval("INST").filter(x => !x.g).map(x => x.n);
  const lit = i => document.getElementById("yy" + i).classList.contains("on") ||
                   document.getElementById("yn" + i).classList.contains("on");
  const cb = document.getElementById("none-inst");
  for (let i = 0; i < ids.length; i++) {
    document.getElementById("yy" + i).classList.remove("on");
    document.getElementById("yn" + i).classList.remove("on");
  }
  check(!lit(0), "rows start unanswered");
  cb.checked = true; window.eval("onNoneGroup('inst', false)");
  check(document.getElementById("yn0").classList.contains("on"), "ticking sets the group to N");
  check(document.getElementById("yy0").disabled, "and locks it");
  cb.checked = false; window.eval("onNoneGroup('inst', true)");
  check(!lit(0), "unticking returns rows to UNANSWERED, not a chosen N");
  check(!document.getElementById("yy0").disabled, "and unlocks them");

  // the programmatic path (populate) must leave restored values alone
  window.eval("setyn")(2, "Y");
  window.eval("onNoneGroup('inst', false)");   // no retract flag — the populate path
  check(document.getElementById("yy2").classList.contains("on"),
        "reapplying the lock without retracting preserves restored answers");
  window.eval("setyn")(2, "N");
}

// Layer density is all-or-nothing: rhoA, rhoB and a computed mean appear
// together. It is a genuinely separate measurement from the §5 interval
// profile — which is why it lives behind a toggle — so it gets the same
// duplicate-reading treatment, or none at all.
{
  window.confirm = () => true;
  document.getElementById("sb").innerHTML = "";
  window.eval("setLayerDensity")(false);
  window.addRow("s");
  const tr = () => document.querySelector("#sb tr");
  check(tr().querySelectorAll(".s-den").length === 0, "toggle off: no density cells");
  window.eval("setLayerDensity")(true);
  check(tr().querySelectorAll(".s-den").length === 3, "toggle on: exactly three");
  check([...document.querySelectorAll(".s-den-th")].map(t => t.textContent).join("/") === "ρA/ρB/ρ avg",
        "and three headers to match");

  // a row added AFTER the toggle must match one added before it
  window.addRow("s");
  const rows = [...document.querySelectorAll("#sb tr")];
  check(rows[1].querySelectorAll(".s-den").length === 3,
        "a row created while the toggle is on gets the same three cells");

  const c = rows[0].querySelectorAll(".s-den input");
  c[0].value = "240"; window.eval("calcLayerAvg")(c[0]);
  check(c[2].value === "240", "one reading: the mean is that reading");
  c[1].value = "260"; window.eval("calcLayerAvg")(c[1]);
  check(c[2].value === "250", "two readings: the mean of both");
  c[0].value = "0"; window.eval("calcLayerAvg")(c[0]);
  check(c[2].value === "260", "a zero is not a density and never drags the mean down");
  c[0].value = "240"; window.eval("calcLayerAvg")(c[0]);
  check(rows[0].querySelectorAll(".s-den input")[2].readOnly, "the mean is not typeable");

  // round-trip, including the comments column which shifts by three
  const i = rows[0].querySelectorAll("input");
  i[0].value = "100"; i[1].value = "62"; i[8].value = "crust";
  const got = window.collect().stratigraphy[0];
  check(got.layer_density_a === 240 && got.layer_density_b === 260 && got.layer_density === 250,
        "collect() carries A, B and the mean");
  check(got.comments === "crust", "and the comments column is still read from the right cell");

  // a pit saved before rhoA/rhoB existed must not lose its value
  window.populate({ meta: {}, weather: {}, ground: {}, temperature: [], density: [],
    lwc: [], ssa: [], instruments: [], ssa_calibration: {},
    stratigraphy: [{ top: 100, bottom: 62, layer_density: 275, gtype: "RG",
                     hardness: "1F", wetness: "D", comments: "old" }] });
  const c2 = document.querySelector("#sb tr").querySelectorAll(".s-den input");
  check(c2[0].value === "275" && c2[1].value === "" && c2[2].value === "275",
        "a legacy single density loads into A, with the mean following");
  document.getElementById("sb").innerHTML = "";
  window.eval("setLayerDensity")(false);
  window.eval("tick()");
}

// Every interval table has blocking geometry rules that the SERVER enforces
// (repository.py refuses an inverted stratigraphy layer outright). liveBoundsMark
// reddened the inputs for density, LWC and stratigraphy alike, but only pushed a
// MESSAGE for density — so an inverted §7 layer showed red cells while the header
// glyph stayed ✓, the checklist stayed green, and the only real symptom was
// Archive refusing at the very end.
{
  const type = (el, v) => { el.value = v; el.dispatchEvent(new window.Event("input", { bubbles: true })); };
  const glyph = id => { const e = document.querySelector("#" + id + " .sec-status"); return e ? e.textContent : ""; };
  const clRow = re => [...document.querySelectorAll("#cl-items .ci")].find(x => re.test(x.textContent));

  document.getElementById("depth").value = "50";
  document.getElementById("sb").innerHTML = "";
  window.addRow("s");
  const si = document.querySelectorAll("#sb tr input");
  type(si[0], "120"); type(si[1], "130");        // inverted AND beyond total depth
  check(glyph("s7") === "✖", "an inverted stratigraphy layer blocks §7");
  const sbox = document.getElementById("s-warn");
  check(/must be greater than bottom/.test(sbox.textContent),
        "and says so in §7's own box, in the server's wording");
  check(/exceeds total depth/.test(sbox.textContent), "both rules are reported, not just the first");
  const row = clRow(/Stratigraphy/);
  check(row && row.querySelector(".cd.bad"),
        "the §12 checklist marks it bad instead of green");

  type(si[0], "40"); type(si[1], "20");
  window.eval("tick()");
  check(glyph("s7") !== "✖", "correcting it clears the glyph");
  check(!clRow(/Stratigraphy/).querySelector(".cd.bad"), "and the checklist row");

  // LWC has the same geometry rules and was equally silent
  document.getElementById("lb").innerHTML = "";
  window.addRow("l");
  const li = document.querySelectorAll("#lb tr input");
  type(li[0], "10"); type(li[1], "90");
  check(glyph("s6") === "✖", "an inverted LWC interval blocks §6");
  check(/must be greater than bottom/.test(document.getElementById("l-warn").textContent),
        "with its own message");

  document.getElementById("sb").innerHTML = "";
  document.getElementById("lb").innerHTML = "";
  if (window.densityWarnings) window.densityWarnings();
  window.eval("tick()");
}

// The per-layer count must appear the moment photos are picked. It was
// repainted by the tbody input listener and by the post-upload flush, but not
// by selection itself — so the badge stayed blank until you typed into the
// NEXT layer, which reads as lag rather than a missing call.
{
  const type = (el, v) => { el.value = v; el.dispatchEvent(new window.Event("input", { bubbles: true })); };
  const F = n => new window.File([new Uint8Array([1, 2, 3])], n, { type: "image/jpeg" });
  const q = window.eval("_pendingAttach");
  q.stratigraphy.length = 0;
  document.getElementById("sb").innerHTML = "";
  window.addRow("s");
  const tr = document.querySelector("#sb tr");
  const i = tr.querySelectorAll("input[type=number]");
  type(i[0], "100"); type(i[1], "62");
  const badge = () => tr.querySelector(".cam-n").textContent;
  check(badge() === "", "no badge before anything is attached");

  const inp = document.getElementById("att-strat");
  window.eval("pickLayerPhotos")(tr.querySelector(".cam"));
  Object.defineProperty(inp, "files", { value: [F("a.jpg"), F("b.jpg")], configurable: true });
  await window.eval("uploadAttachment")("att-strat", "stratigraphy");
  check(badge() === "2", `count shows immediately on selection (got ${JSON.stringify(badge())})`);

  window.eval("pickLayerPhotos")(tr.querySelector(".cam"));
  Object.defineProperty(inp, "files", { value: [F("c.jpg")], configurable: true });
  await window.eval("uploadAttachment")("att-strat", "stratigraphy");
  check(badge() === "3", "and accumulates without needing another keystroke");
  check(tr.querySelector(".cam").classList.contains("has"),
        "the button itself shows it is carrying photos");

  q.stratigraphy.length = 0;
  document.getElementById("sb").innerHTML = "";
  window.eval("refreshAttachUI()");
  window.eval("tick()");
}

// Temperature is the ONE table where a negative height is legitimate: the
// profile runs down the pack and a crew may take a single ground reading below
// the snow-ground interface. Every other table measures snow and cannot.
{
  const type = (el, v) => { el.value = v; el.dispatchEvent(new window.Event("input", { bubbles: true })); };
  const glyph = () => { const e = document.querySelector("#s4 .sec-status"); return e ? e.textContent : ""; };
  const txt = () => document.getElementById("t-warn").textContent;
  document.getElementById("depth").value = "40";
  const set = (h, t) => {
    document.getElementById("tb").innerHTML = "";
    window.addRow("t");
    const i = document.querySelectorAll("#tb tr input");
    type(i[0], h); type(i[1], t);
  };
  set("40", "-8");   check(glyph() !== "✖", "a normal reading is clean");
  set("-10", "-0.5");
  check(glyph() !== "✖", "a ground reading at -10 cm does NOT block");
  check(/soil temperature/.test(txt()), "it is noted as a soil temperature");
  set("-25", "-1");  check(glyph() === "✖", "anything below -10 cm is a typo, not a probe reading");
  set("20", "0.5");
  check(glyph() !== "✖" && !/above freezing/.test(txt()),
        "0.5 °C in wet snow does not warn — instruments read either side of zero");
  set("20", "18");   check(/well above freezing/.test(txt()), "but 18 °C does");
  set("20", "-45");  check(glyph() === "✖", "-45 °C is below any plausible snow temperature");
  set("20", "-30");  check(glyph() === "⚠", "-30 °C is extreme but real, so it warns");
  document.getElementById("tb").innerHTML = "";
  if (window.densityWarnings) window.densityWarnings();
  window.eval("tick()");
}

// "Started but not finished" is reported ONCE PER TABLE, not once per row — a
// half-filled table should not produce a wall of near-identical amber lines.
{
  const type = (el, v) => { el.value = v; el.dispatchEvent(new window.Event("input", { bubbles: true })); };
  document.getElementById("depth").value = "100";
  document.getElementById("db").innerHTML = "";
  window.addRow("d"); window.addRow("d");
  const r = [...document.querySelectorAll("#db tr")];
  type(r[0].querySelectorAll("input")[0], "100"); type(r[0].querySelectorAll("input")[1], "90");
  type(r[1].querySelectorAll("input")[0], "90");  type(r[1].querySelectorAll("input")[1], "80");
  const dtxt = () => document.getElementById("d-warn").textContent;
  check(/rows 1, 2 started but no density reading/.test(dtxt()),
        `one aggregated line that NAMES the rows (got ${JSON.stringify(dtxt().trim())})`);
  check((dtxt().match(/started but no density reading/g) || []).length === 1,
        "one line, not one per row");
  type(r[0].querySelectorAll("input")[2], "250");
  check(/row 2 started but/.test(dtxt()) && !/rows 1, 2/.test(dtxt()),
        "and narrows to the row still outstanding");
  type(r[1].querySelectorAll("input")[2], "300");
  check(!/started but/.test(dtxt()), "and clears entirely");
  check(document.getElementById("d-warn").className.indexOf("has-block") === -1,
        "a row in progress is never a blocker");
  document.getElementById("db").innerHTML = "";
  if (window.densityWarnings) window.densityWarnings();
  window.eval("tick()");
}

// The expander: attaching a photo and never seeing WHAT you attached was the
// gap — the badge said "3" and the only way to check was §11, grouped by
// category rather than by layer.
{
  const type = (el, v) => { el.value = v; el.dispatchEvent(new window.Event("input", { bubbles: true })); };
  const F = n => new window.File([new Uint8Array([1, 2, 3])], n, { type: "image/jpeg" });
  const q = window.eval("_pendingAttach");
  q.stratigraphy.length = 0;
  document.getElementById("sb").innerHTML = "";
  window.addRow("s"); window.addRow("s");
  const rows = () => [...document.querySelectorAll("#sb tr:not(.lp-row)")];
  let r = rows();
  let i = r[0].querySelectorAll("input[type=number]"); type(i[0], "100"); type(i[1], "62");
  i = r[1].querySelectorAll("input[type=number]"); type(i[0], "62"); type(i[1], "45");
  const inp = document.getElementById("att-strat");
  const pick = async (tr, names) => {
    window.eval("pickLayerPhotos")(tr.querySelector(".cam"));
    Object.defineProperty(inp, "files", { value: names.map(F), configurable: true });
    await window.eval("uploadAttachment")("att-strat", "stratigraphy");
  };
  check(r[0].querySelector(".lp-toggle").style.display === "none",
        "no expander on a layer with no photographs");
  await pick(r[0], ["wall-a.jpg", "wall-b.jpg", "crust.jpg"]);
  await pick(r[1], ["hoar.jpg"]);
  check(r[0].querySelector(".lp-toggle").style.display !== "none",
        "the expander appears once photos are attached");

  window.eval("toggleLayerPhotos")(r[0].querySelector(".lp-toggle"));
  const lp = () => document.querySelector("#sb .lp-row");
  check(!!lp(), "clicking it opens a row");
  check(lp().previousElementSibling === rows()[0], "directly beneath its own layer");
  check(/wall-a\.jpg/.test(lp().textContent) && /crust\.jpg/.test(lp().textContent),
        "listing that layer's photographs");
  check(!/hoar\.jpg/.test(lp().textContent), "and NOT another layer's");
  check(lp().querySelectorAll(".att-x").length === 3, "each queued photo is removable");

  document.querySelectorAll("#sb .lp-row .att-x")[1]
    .dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await settle();
  check(!!lp(), "removing one leaves the list OPEN (it used to collapse)");
  check(!/wall-b\.jpg/.test(lp().textContent), "the removed photo is gone");
  check(/wall-a\.jpg/.test(lp().textContent) && /crust\.jpg/.test(lp().textContent),
        "the others are untouched");
  check(rows()[0].querySelector(".cam-n").textContent === "2", "the badge follows");
  check(rows()[1].querySelector(".cam-n").textContent === "1", "the other layer is unaffected");

  // Bounded, and it YIELDS. removePending() is async (the queue lives in
  // IndexedDB), so a synchronous while-loop can never see the element go: it
  // never returns to the event loop, the pending removal never resolves, and
  // the loop spins forever. That turned a real assertion failure into a hung
  // process, which in CI reads as a timeout rather than as a test result.
  for (let guard = 0; guard < 25 && document.querySelector("#sb .lp-row .att-x"); guard++) {
    document.querySelector("#sb .lp-row .att-x")
      .dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
    await settle();
  }
  check(!lp(), "removing the last one closes the expander");
  check(rows()[0].querySelector(".lp-toggle").style.display === "none", "and hides the control");

  q.stratigraphy.length = 0;
  document.getElementById("sb").innerHTML = "";
  window.eval("refreshAttachUI()");
  window.eval("tick()");
}

// Header glyph, sidebar pip and §12 checklist must always agree — they are one
// state shown three ways. Two gaps: the checklist knew only about BLOCKERS, so
// a warned section showed a plain green tick there while its header said ⚠; and
// the checklist rows are rebuilt via innerHTML partway through tick(), which
// discarded any state painted before that point.
{
  const type = (el, v) => { el.value = v; el.dispatchEvent(new window.Event("input", { bubbles: true })); };
  const glyph = id => { const e = document.querySelector("#" + id + " .sec-status"); return e ? e.textContent : ""; };
  const clState = re => {
    const r = [...document.querySelectorAll("#cl-items .ci")].find(x => re.test(x.textContent));
    if (!r) return "?";
    if (r.querySelector(".cd.bad")) return "✖";
    if (r.querySelector(".cd.warn")) return "⚠";
    return r.querySelector(".cd.done") ? "✓" : "";
  };
  document.getElementById("depth").value = "100";
  document.getElementById("db").innerHTML = "";
  window.addRow("d");
  const di = document.querySelectorAll("#db tr input");

  // 100-0, not 100-90: the interval spans the pack, so the coverage check stays
  // quiet and the only thing on trial here is the unusual density.
  type(di[0], "100"); type(di[1], "0"); type(di[2], "800");   // unusual: warns only
  window.eval("tick()");
  check(glyph("s5") === "⚠", "an unusual density warns on the header");
  check(document.getElementById("p5").classList.contains("warn"), "the pip agrees");
  check(clState(/Density/) === "⚠",
        `and the CHECKLIST agrees (got ${JSON.stringify(clState(/Density/))})`);

  type(di[2], "250"); window.eval("tick()");
  check(glyph("s5") === "✓" && clState(/Density/) === "✓", "all three clear together");

  type(di[0], "120"); window.eval("tick()");
  check(glyph("s5") === "✖" && clState(/Density/) === "✖",
        "and a blocker shows as a blocker in both");

  document.getElementById("db").innerHTML = "";
  if (window.densityWarnings) window.densityWarnings();
  window.eval("tick()");
}

// Temperature reports half-typed rows ONCE, like every other table.
{
  const type = (el, v) => { el.value = v; el.dispatchEvent(new window.Event("input", { bubbles: true })); };
  document.getElementById("depth").value = "100";
  document.getElementById("tb").innerHTML = "";
  for (let i = 0; i < 3; i++) window.addRow("t");
  [...document.querySelectorAll("#tb tr")].forEach((tr, i) =>
    type(tr.querySelectorAll("input")[0], String(100 - i * 10)));
  const t = document.getElementById("t-warn").textContent;
  check(/rows 1, 2, 3 started but no temperature entered/.test(t),
        `three half-typed rows give ONE line naming them (got ${JSON.stringify(t.trim())})`);
  // Count the half-typed line specifically. Counting EVERY ⚠ in the box made
  // this a tripwire for any unrelated temperature warning — the coverage check
  // legitimately fires here too, since 100/90/80 in a 100 cm pit leaves the
  // bottom 80 cm unmeasured.
  check((t.match(/started but no temperature entered/g) || []).length === 1,
        "the half-typed rows give exactly one line, not one per row");
  document.getElementById("tb").innerHTML = "";
  if (window.densityWarnings) window.densityWarnings();
  window.eval("tick()");
}

// Interval-board rows are three views of one measurement:
// SWE (mm) = density (kg m-3) x depth (cm) / 100.
{
  const g = k => document.getElementById("ib-" + k + "-a");
  const type = (k, v) => { g(k).value = v; window.eval(`ibSolve('a','${k}')`); };
  ["d", "s", "r"].forEach(k => { g(k).value = ""; g(k).classList.remove("is-derived"); });

  type("d", "130"); type("r", "250");
  check(g("s").value === "325", `depth + density gives SWE (got ${g("s").value})`);
  check(g("s").classList.contains("is-derived"), "and the computed cell is marked");

  ["d", "s", "r"].forEach(k => { g(k).value = ""; g(k).classList.remove("is-derived"); });
  type("d", "130"); type("s", "325");
  check(g("r").value === "250", `depth + SWE gives density (got ${g("r").value})`);

  ["d", "s", "r"].forEach(k => { g(k).value = ""; g(k).classList.remove("is-derived"); });
  type("s", "49"); type("r", "272");
  check(g("d").value === "18", `SWE + density gives depth (got ${g("d").value})`);

  // a computed value can be typed over: a crew may have a reading that
  // disagrees slightly with the arithmetic, and the app records what was
  // measured rather than overwriting it
  ["d", "s", "r"].forEach(k => { g(k).value = ""; g(k).classList.remove("is-derived"); });
  type("d", "130"); type("r", "250");
  type("s", "300");
  check(g("r").value === "230.8", `typing over a derived cell makes it the source (got ${g("r").value})`);
  ["d", "s", "r"].forEach(k => { g(k).value = ""; g(k).classList.remove("is-derived"); });
  window.eval("tick()");
}

// A placeholder collision once made this file exit before a single assertion,
// and a later synchronous loop made it hang before the final count. Keep a
// floor below the current ~320 assertions so CI cannot report a misleading
// green run after most of the suite silently stops executing.
const MIN_DOM_ASSERTIONS = 300;
if(pass + fail < MIN_DOM_ASSERTIONS){
  console.log(`FAIL DOM suite executed only ${pass + fail} assertions; expected at least ${MIN_DOM_ASSERTIONS}`);
  fail++;
}
console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
