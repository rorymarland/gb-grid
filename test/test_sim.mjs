// Tests for the sandbox's frequency model (run: node test/test_sim.mjs)
import { simulate, estimateInertia } from '../app/static/js/gridsim.js';

let failures = 0;
function check(name, ok, detail) { if (!ok) { failures++; console.log('FAIL', name, detail === undefined ? '' : JSON.stringify(detail)); } }

const held = { DCL: 1400, DML: 500, DRL: 480, PQR: 480 };
const base = simulate({ loss: 1320, inertia: 150, held });
const s = base.summary;
// df/dt = f0 * dP / (2E): 50 * 1.32 / (2 * (150 - 7.3)) = 0.23 Hz/s before anything responds
check('initial rate of fall', Math.abs(s.rocof - 0.231) < 0.01, s.rocof);
check('held within limits', s.nadir > 49.5 && s.nadir < 49.8 && !s.lfdd, s);
check('recovers towards 50', s.at60 > s.nadir && s.at60 < 50, s);
const low = simulate({ loss: 1320, inertia: 75, held }).summary;
check('half the inertia, twice the rate', Math.abs(low.rocof / s.rocof - 2.1) < 0.25, [low.rocof, s.rocof]);
check('lower inertia, deeper and sooner', low.nadir < s.nadir && low.nadirAt < s.nadirAt, low);
const noDC = simulate({ loss: 1320, inertia: 150, held: { DML: 500, DRL: 480, PQR: 480 } }).summary;
check('without Dynamic Containment it goes below 49.5', noDC.nadir < 49.5 && noDC.nadir < s.nadir, noDC);
const none = simulate({ loss: 0, inertia: 150, held }).summary;
check('nothing lost, nothing moves', Math.abs(none.nadir - 50) < 1e-6 && none.rocof < 1e-9, none);
const big = simulate({ loss: 2500, inertia: 90, held: {}, governors: 200 });
check('disconnection stops the fall', big.summary.lfdd && big.summary.nadir > 48.5 && big.notes.some(n => /48.8 Hz/.test(n[1])), big.summary);
const old = simulate({ loss: 980, lossSync: false, inertia: 210, demand: 29000, held: {}, governors: 1000, lomMW: 500, lomJolt: 500, ufMW: 350,
  later: [{ t: 57, mw: 210, sync: true }, { t: 80, mw: 190, sync: true }], seconds: 120, reserveStart: 1e9 }).summary;
check('double trip reaches 48.8 Hz after the second loss', old.lfdd && old.nadirAt > 57 && old.lom === 500, old);
check('inertia estimate', estimateInertia(20, { a: 6, b: 30 }) === 150 && estimateInertia(0) === 40);

console.log('sim tests:', failures ? failures + ' failed' : 'all passed');
process.exit(failures ? 1 : 0);
