// Regression: the live rail must use the same edge-gap rule as
// cryopit/density.py. In particular, large edge gaps extend the nearest
// measured interval; they never fall back to a whole-pit weighted mean.
import fs from 'node:fs';
import vm from 'node:vm';

const rail = fs.readFileSync(new URL('../cryopit/static/js/75_rail.js', import.meta.url), 'utf8');

function runRail(payload) {
  const nodes = new Map();
  const node = id => {
    if (!nodes.has(id)) nodes.set(id, { textContent: '', innerHTML: '', style: {}, addEventListener: () => {} });
    return nodes.get(id);
  };
  const context = {
    console,
    setTimeout,
    clearTimeout,
    collect: () => structuredClone(payload),
    document: {
      getElementById: id => node(id),
      querySelectorAll: () => [],
    },
    getComputedStyle: () => ({ getPropertyValue: () => '' }),
  };
  vm.createContext(context);
  vm.runInContext(rail, context, { filename: '75_rail.js' });
  context.drawMini();
  return {
    bulk: node('mc-den').textContent,
    swe: node('mc-swe').textContent,
    coverageLabel: node('mc-cov-lbl').textContent,
    coverage: node('mc-cov').textContent,
  };
}

function check(condition, message) {
  if (!condition) throw new Error(`FAIL ${message}`);
  console.log(`PASS ${message}`);
}

// Large TOP gap: backend extends 100 kg/m3 from 40 cm all the way to HS=100.
// Expected bulk = (100*80 + 400*20)/100 = 160 kg/m3; SWE = 160 mm.
// The obsolete >25% weighted-mean fallback would have returned 250/250.
{
  const out = runRail({
    meta: { total_depth: 100 },
    density: [
      { top: 40, bottom: 20, a: 100, b: null, c: null },
      { top: 20, bottom: 0, a: 400, b: null, c: null },
    ],
    stratigraphy: [],
    temperature: [],
  });
  check(out.bulk === '160 kg/m³', 'large top edge gap extends nearest interval in live bulk density');
  check(out.swe === '160 mm', 'large top edge gap extends nearest interval in live SWE');
  check(out.coverageLabel.startsWith('est · gap-filled'), 'top edge extension remains labeled as estimated gap-filled coverage');
  check(out.coverage === '60 cm', 'top edge extension reports the filled extent');
}

// Large BOTTOM gap: backend extends 400 kg/m3 from 60 cm to ground.
// Expected bulk = (100*20 + 400*80)/100 = 340 kg/m3; SWE = 340 mm.
{
  const out = runRail({
    meta: { total_depth: 100 },
    density: [
      { top: 100, bottom: 80, a: 100, b: null, c: null },
      { top: 80, bottom: 60, a: 400, b: null, c: null },
    ],
    stratigraphy: [],
    temperature: [],
  });
  check(out.bulk === '340 kg/m³', 'large bottom edge gap extends nearest interval in live bulk density');
  check(out.swe === '340 mm', 'large bottom edge gap extends nearest interval in live SWE');
  check(out.coverage === '60 cm', 'bottom edge extension reports the filled extent');
}

console.log('density live-rail parity tests passed');
