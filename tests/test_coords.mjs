// CryoPit coordinate-module tests:  node tests/test_coords.mjs
// The module is browser-global style; evaluate it and grab the pure
// functions. API: latLonToUtm(lat,lon) -> {e, n, zn, zl('N'|'S')};
//                 utmToLatLon(e, n, zn, zl) -> {lat, lon}.
import { readFileSync } from "fs";
import { fileURLToPath } from "url";
import { dirname, join } from "path";
const here = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(join(here, "../cryopit/static/js/30_coords.js"), "utf8");
const mod = new Function(src + "; return { latLonToUtm, utmToLatLon };")();

let n = 0;
function ok(cond, label) { if (!cond) throw new Error("FAIL " + label); console.log("PASS " + label); n++; }

// zone arithmetic at known longitudes
ok(mod.latLonToUtm(43.615, -116.2023).zn === 11, "Boise is UTM zone 11");
ok(mod.latLonToUtm(39.03, -108.06).zn === 12, "Grand Mesa is UTM zone 12");
ok(mod.latLonToUtm(46.85, 9.53).zn === 32, "Davos is UTM zone 32");

// absolute reference: pyproj/PROJ EPSG:32611 for (43.615, -116.2023)
// gives (564367.10 E, 4829422.28 N); we require centimeter-level agreement.
const b = mod.latLonToUtm(43.615, -116.2023);
ok(Math.abs(b.e - 564367.10) < 0.15 && Math.abs(b.n - 4829422.28) < 0.15,
   "Boise matches PROJ ground truth within 15 cm");
// band letters (the "11T" users write): note Grand Mesa's band is "S" —
// a NORTHERN band letter, which is exactly why bands, not hemisphere
// letters, are the contract between the two functions.
ok(b.zl === "T", "Boise band is T");
ok(mod.latLonToUtm(39.03, -108.06).zl === "S", "Grand Mesa band is S (northern!)");
ok(mod.latLonToUtm(-43.53, 172.63).zl === "G", "Christchurch band is G (southern)");

// round-trip: lat/lon -> UTM -> lat/lon within 1e-6 deg (~0.1 m) — both
// directions carry the full Snyder series.
for (const [lat, lon, name] of [[43.615, -116.2023, "Boise"],
                                [39.03, -108.06, "Grand Mesa"],
                                [46.85, 9.53, "Davos"],
                                [-43.53, 172.63, "Christchurch (S hemisphere)"]]) {
  const u = mod.latLonToUtm(lat, lon);
  const r = mod.utmToLatLon(u.e, u.n, u.zn, u.zl);
  ok(Math.abs(r.lat - lat) < 1e-6 && Math.abs(r.lon - lon) < 1e-6,
     `round-trip ${name} (max err ${Math.max(Math.abs(r.lat - lat), Math.abs(r.lon - lon)).toExponential(1)} deg)`);
}
console.log(n + " coordinate tests passed");
