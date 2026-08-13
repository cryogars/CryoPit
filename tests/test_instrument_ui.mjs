/** Lightweight instrument-state browser tests that need no jsdom.
 *
 * They execute the real collect() and setyn() functions in a VM with the
 * smallest DOM surface those functions require. The full page round-trip is
 * still covered by test_dom.mjs when jsdom is installed.
 */
import fs from "node:fs";
import vm from "node:vm";

// Substitute EVERY page placeholder, not a hand-kept list of the ones that
// happened to exist when this file was written. These harnesses load the raw
// static/js sources rather than the assembled page, so any placeholder the app
// introduces later arrives here as an undefined identifier and the whole file
// dies on load. Unknown tokens become null, which is a defined value the VM can
// run.
const KNOWN_PLACEHOLDERS = {
  __CSRF_TOKEN__: "test-token",
  __ENABLE_EDIT__: "true",
  __SHOW_EXAMPLE_PLACEHOLDERS__: "false",
};
const substitutePlaceholders = src =>
  src.replace(/__[A-Z0-9_]+__/g, m =>
    Object.prototype.hasOwnProperty.call(KNOWN_PLACEHOLDERS, m)
      ? KNOWN_PLACEHOLDERS[m] : "null");


let passed = 0;
function check(condition, label) {
  if (!condition) throw new Error(`FAIL ${label}`);
  passed += 1;
  console.log(`PASS ${label}`);
}

function makeClassList(initial = []) {
  const values = new Set(initial);
  return {
    contains: key => values.has(key),
    add: key => values.add(key),
    remove: key => values.delete(key),
    toggle: (key, on) => on ? values.add(key) : values.delete(key),
  };
}

const elements = new Map();
function element(id) {
  if (!elements.has(id)) {
    elements.set(id, {
      id,
      value: "",
      checked: false,
      disabled: false,
      textContent: id === "pitid" ? "TRI-COLLECT" : "",
      innerHTML: "",
      options: [],
      attrs: {},
      classList: makeClassList(),
      setAttribute(name, value) { this.attrs[name] = value; },
      closest() { return { classList: makeClassList() }; },
    });
  }
  return elements.get(id);
}

const document = {
  getElementById: element,
  querySelectorAll: () => [],
};
const context = {
  console,
  document,
  __ENABLE_EDIT__: false,
  _layerDensityOn: false,
  gv: id => element(id).value,
  gr: () => "",
  gcs: () => [],
  tick: () => {},
  refreshAttachUI: () => {},
  setTimeout,
  clearTimeout,
};
context.window = context;
vm.createContext(context);
for (const f of ["00_core", "50_form_io", "10_instruments"]) {
  vm.runInContext(
    substitutePlaceholders(fs.readFileSync(`cryopit/static/js/${f}.js`, "utf8")), context);
}
context.buildInst();
check(element("ig").innerHTML.includes("pickyn(0,'Y')") &&
      element("ig").innerHTML.includes("pickyn(0,'N')"),
      "rendered Y/N buttons use retractable three-state clicks");

function used(name) {
  return context.collect().instruments.find(item => item.name === name)?.used;
}

check(used("Digital LWC") === null,
      "collect() keeps an untouched instrument unanswered");
element("yn0").classList.add("on");
check(used("Digital LWC") === "N", "collect() keeps an explicit N");
element("yn0").classList.remove("on");
element("yy0").classList.add("on");
element("sn0").value = "D-42";
const yes = context.collect().instruments.find(item => item.name === "Digital LWC");
check(yes.used === "Y" && yes.sn === "D-42",
      "collect() keeps explicit Y and its serial number");

context.setyn(0, "N");
check(element("yn0").classList.contains("on") &&
      !element("yy0").classList.contains("on"),
      "setyn() selects only N");
check(element("sn0").disabled && element("sn0").value === "",
      "N disables and clears the serial number");
context.setyn(0, null);
check(!element("yn0").classList.contains("on") &&
      !element("yy0").classList.contains("on"),
      "setyn(null) restores unanswered");
context.pickyn(0, "N");
context.pickyn(0, "N");
check(!element("yn0").classList.contains("on") &&
      !element("yy0").classList.contains("on"),
      "clicking the selected answer again retracts to unanswered");
check(element("yn0").attrs["aria-pressed"] === "false" &&
      element("yy0").attrs["aria-pressed"] === "false",
      "unanswered exposes neither button as pressed");

console.log(`${passed} lightweight instrument UI tests passed`);
