/* The first minute after a power station trips: a one-node model of GB
   frequency, for the "what if" sandbox.

   Every synchronous machine on the grid shares one frequency, so the grid
   behaves like a single spinning mass. When generation and demand differ by
   dP (GW), frequency changes at

       df/dt = f0 * dP / (2 * E)

   where E is the stored kinetic energy (inertia, GVA·s) and f0 = 50 Hz. The
   gap is closed by demand that falls with frequency (motors slow down),
   NESO's response services, other generators' governors and, within a
   minute, reserve. Below 48.8 Hz distribution networks start disconnecting
   demand automatically.

   This is a teaching model, not a power system study: one node, no
   network, and typical service shapes and delays rather than measured ones.

     simulate({ inertia: 150, loss: 1320, demand: 28000, held: { DCL: 1400 } }) */

export const F0 = 50;
export const LIMITS = [
  [49.5, 'Lowest allowed for a normal loss'],
  [49.2, 'Lowest allowed for the largest losses'],
  [48.8, 'Automatic disconnection of demand begins']
];
/* Automatic low frequency demand disconnection: [Hz, share of demand] */
export const LFDD = [[48.8, 0.05], [48.75, 0.05], [48.7, 0.075], [48.6, 0.075], [48.5, 0.1]];

/* Share of a service's contracted MW delivered at a frequency deviation x (Hz, positive) */
function ramp(x, points) {
  for (var i = 1; i < points.length; i++) {
    if (x <= points[i][0]) {
      var a = points[i - 1], b = points[i];
      return a[1] + (b[1] - a[1]) * (x - a[0]) / (b[0] - a[0]);
    }
  }
  return 1;
}
export const CURVES = {
  /* Dynamic Containment: a little inside ±0.2 Hz, the rest from 0.2 to 0.5 Hz */
  dc: [[0, 0], [0.015, 0], [0.2, 0.05], [0.5, 1]],
  /* Dynamic Moderation: a little to 0.1 Hz, the rest by 0.2 Hz */
  dm: [[0, 0], [0.015, 0], [0.1, 0.05], [0.2, 1]],
  /* Dynamic Regulation: steadily across ±0.2 Hz */
  dr: [[0, 0], [0.015, 0], [0.2, 0.95], [0.5, 1]],
  /* Generators' governors: in proportion to the deviation, full at 0.5 Hz */
  gov: [[0, 0], [0.015, 0], [0.5, 1]]
};
/* Delay before acting, then the time constant of a first-order rise (s) */
export const SPEED = { dc: [0.5, 0.15], dm: [0.5, 0.15], dr: [2, 2.5], gov: [2, 4] };

function mw(v) { return Math.round(v).toLocaleString('en-GB') + ' MW'; }

export const DEFAULTS = {
  inertia: 150,           // GVA·s before the loss
  loss: 1000,             // MW lost at t = 0
  lossSync: true,         // the lost machine was spinning, so its inertia goes too
  lossH: 5,               // its inertia constant (s)
  demand: 28000,          // MW
  damping: 2,             // % of demand per Hz
  held: {},               // MW: DCL DML DRL DCH DMH DRH PQR NQR
  governors: 300,         // MW of governor response from other generators
  reserveStart: 10,       // s: control room instructs reserve
  reserveFull: 60,        // s: reserve fully delivered
  lomRocof: 1.0,          // Hz/s at which small generators' loss-of-mains protection trips
  lomMW: 0,               // MW of small generation that trips at that rate
  lomJolt: 0,             // MW: a loss at least this big also trips it at once (old vector shift relays); 0 = never
  ufHz: 49.0,             // Hz at which some small generators' under-frequency protection trips
  ufMW: 0,                // MW of small generation that trips there
  later: [],              // further losses: [{ t: s, mw: MW, sync: bool, label: '' }]
  seconds: 90,
  dt: 0.02
};

export function simulate(options) {
  var p = Object.assign({}, DEFAULTS, options || {});
  var held = p.held || {};
  var lowMW = { dc: held.DCL || 0, dm: held.DML || 0, dr: held.DRL || 0, gov: p.governors };
  var highMW = { dc: held.DCH || 0, dm: held.DMH || 0, dr: held.DRH || 0, gov: p.governors };
  var reserve = Math.min(held.PQR || 0, p.loss + p.lomMW + p.ufMW + (p.later || []).reduce(function (a, l) { return a + l.mw; }, 0));
  var E = Math.max(10, p.inertia - (p.lossSync ? p.lossH * p.loss / 0.9 / 1000 : 0));
  var dt = p.dt, steps = Math.round(p.seconds / dt);
  var df = 0, rocofMax = 0, lomTripped = 0, shed = 0, stage = 0, reserveOut = 0;
  var out = { dc: 0, dm: 0, dr: 0, gov: 0 };
  var history = [];              // df at each step, for the delays
  var keep = Math.max(1, Math.round(0.1 / dt));
  var res = { t: [], f: [], dc: [], dm: [], dr: [], gov: [], reserve: [], damping: [], shed: [], lost: [], notes: [] };
  var nadir = { f: F0, t: 0 }, back495 = null, peaks = { dc: 0, dm: 0, dr: 0, gov: 0 };
  var later = (p.later || []).slice().sort(function (a, b) { return a.t - b.t; }), extra = 0, ufTripped = 0;
  for (var i = 0; i <= steps; i++) {
    var t = i * dt;
    history.push(df);
    var services = 0;
    ['dc', 'dm', 'dr', 'gov'].forEach(function (k) {
      var lag = Math.round(SPEED[k][0] / dt);
      var seen = history[Math.max(0, history.length - 1 - lag)];
      var target = seen < 0 ? lowMW[k] * ramp(-seen, CURVES[k]) : -highMW[k] * ramp(seen, CURVES[k]);
      out[k] += (target - out[k]) * Math.min(1, dt / SPEED[k][1]);
      services += out[k];
    });
    Object.keys(peaks).forEach(function (k) { if (out[k] > peaks[k]) peaks[k] = out[k]; });
    while (later.length && t >= later[0].t) {
      var next = later.shift();
      extra += next.mw;
      if (next.sync) E = Math.max(10, E - p.lossH * next.mw / 0.9 / 1000);
      res.notes.push([t, (next.label || 'A further unit tripped') + ', losing ' + mw(next.mw) + '.']);
    }
    if (!ufTripped && p.ufMW > 0 && F0 + df <= p.ufHz) {
      ufTripped = p.ufMW;
      res.notes.push([t, 'Frequency reached ' + p.ufHz + ' Hz: ' + mw(p.ufMW) + ' of small generators disconnected themselves.']);
    }
    // Reserve: instructed after the trip, ramps while frequency is still low
    if (t >= p.reserveStart && reserveOut < reserve && df < -0.015) {
      reserveOut = Math.min(reserve, reserveOut + reserve * dt / Math.max(1, p.reserveFull - p.reserveStart));
    }
    var damping = p.damping / 100 * p.demand * -df;
    // Loss-of-mains protection on small generators, measured over half a second
    var halfBack = history[Math.max(0, history.length - 1 - Math.round(0.5 / dt))];
    var rocof = (df - halfBack) / 0.5;
    if (t > 0.05 && Math.abs(rocof) > rocofMax) rocofMax = Math.abs(rocof);
    if (!lomTripped && p.lomMW > 0 && p.lomJolt > 0 && p.loss >= p.lomJolt && t >= 0.04) {
      lomTripped = p.lomMW;
      res.notes.push([t, 'The jolt of the fault set off old loss-of-mains protection on small generators: ' + mw(p.lomMW) + ' disconnected.']);
    }
    if (!lomTripped && p.lomMW > 0 && t > 0.05 && -rocof >= p.lomRocof) {
      lomTripped = p.lomMW;
      res.notes.push([t, 'Small generators’ loss-of-mains protection tripped ' + mw(p.lomMW) + ' as frequency fell faster than ' + p.lomRocof + ' Hz/s.']);
    }
    // Automatic demand disconnection
    while (stage < LFDD.length && F0 + df <= LFDD[stage][0]) {
      shed += LFDD[stage][1] * p.demand;
      res.notes.push([t, 'Frequency reached ' + LFDD[stage][0] + ' Hz: networks disconnected ' + mw(LFDD[stage][1] * p.demand) + ' of demand.']);
      stage++;
    }
    var balance = -p.loss - lomTripped - extra - ufTripped + services + reserveOut + damping + shed;   // MW
    if (i % keep === 0) {
      res.t.push(+t.toFixed(2)); res.f.push(+(F0 + df).toFixed(4));
      res.dc.push(out.dc); res.dm.push(out.dm); res.dr.push(out.dr); res.gov.push(out.gov);
      res.reserve.push(reserveOut); res.damping.push(damping); res.shed.push(shed); res.lost.push(p.loss + lomTripped + extra + ufTripped);
    }
    if (F0 + df < nadir.f) nadir = { f: F0 + df, t: t };
    if (back495 === null && t > nadir.t && nadir.f < 49.5 && F0 + df >= 49.5) back495 = t;
    df += F0 * (balance / 1000) / (2 * E) * dt;
  }
  var n = res.f.length;
  res.summary = {
    inertiaAfter: E, rocof: rocofMax, nadir: nadir.f, nadirAt: nadir.t,
    at60: res.f[Math.min(n - 1, Math.round(60 / (keep * dt)))], end: res.f[n - 1],
    shed: shed, lom: lomTripped, uf: ufTripped, extra: extra, back495: back495, peaks: peaks,
    reserve: reserve, below495: nadir.f < 49.5, below492: nadir.f < 49.2, lfdd: shed > 0
  };
  return res;
}

/* GVA·s from the synchronous generation running (GW), using a fitted line */
export function estimateInertia(syncGW, fit) {
  fit = fit || { a: 6.5, b: 30 };
  return Math.max(40, fit.a * syncGW + fit.b);
}
