// Tests for the OS National Grid conversion (run: node test/test_osgrid.mjs)
import { gridRef, osgb36ToGrid, wgs84ToOsgb36 } from '../app/static/js/osgrid.js';

let failures = 0;
function check(name, ok, detail) { if (!ok) { failures++; console.log('FAIL', name, detail === undefined ? '' : JSON.stringify(detail)); } }

// The worked example in the OS's "A guide to coordinate systems in Great Britain":
// OSGB36 52°39'27.2531"N 1°43'4.5177"E is E 651409.903, N 313177.270
const en = osgb36ToGrid(52 + 39 / 60 + 27.2531 / 3600, 1 + 43 / 60 + 4.5177 / 3600);
check('OS worked example easting', Math.abs(en[0] - 651409.903) < 0.01, en);
check('OS worked example northing', Math.abs(en[1] - 313177.270) < 0.01, en);
check('OS worked example letters', gridRef(52.658 + 0.0013, 1.716 - 0.0017) !== null);

// Helmert shift: GB's datums differ by roughly 100-150 m, mostly in longitude
const o = wgs84ToOsgb36(51.5, -0.1);
check('datum shift is small', Math.abs(o[0] - 51.5) < 0.001 && Math.abs(o[1] + 0.1) < 0.003, o);

// Landmarks against their published grid references (to 100 m, allowing one step either way)
function near(ref, expected) {
  if (!ref) return false;
  const [a, e, n] = ref.split(' '), [b, e2, n2] = expected.split(' ');
  return a === b && Math.abs(+e - +e2) <= 1 && Math.abs(+n - +n2) <= 1;
}
const places = [
  ['Ben Nevis summit', 56.79685, -5.00360, 'NN 166 712'],
  ['Torness power station', 55.9680, -2.4091, 'NT 745 751'],
  ['Land’s End', 50.0661, -5.7146, 'SW 342 251'],
  ['Lerwick', 60.1550, -1.1456, 'HU 476 413'],
];
for (const [name, lat, lon, expected] of places) {
  const ref = gridRef(lat, lon);
  check(name, near(ref, expected), [ref, expected]);
}
check('ten-figure', /^NN \d{5} \d{5}$/.test(gridRef(56.79685, -5.0036, 5)), gridRef(56.79685, -5.0036, 5));
// The grid runs 700 km east of its origin: Sofia wind farm (2.23°E) is inside, 4°E is not
check('east edge, inside', /^OW /.test(gridRef(54.98, 2.23)), gridRef(54.98, 2.23));
check('east edge, outside', gridRef(55.0, 4.0) === null, gridRef(55.0, 4.0));
check('south of the grid', gridRef(48.5, -2.0) === null, gridRef(48.5, -2.0));

console.log(failures ? failures + ' grid tests failed' : 'grid tests: all passed');
process.exit(failures ? 1 : 0);
