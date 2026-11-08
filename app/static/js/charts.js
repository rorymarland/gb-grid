/* Chart helpers shared by the History page and the Display view: fuel
   order, names and colours, stacking for area charts, and uPlot options
   that match the dashboard (UK time, 24-hour clock, theme colours). */

import { TZ, num } from './format.js';
import { css } from './format.js';

export const DAY = 86400;
/* Bottom to top in stacked charts: steady low-carbon first, fossil last */
export const FUEL_ORDER = ['nuclear', 'wind', 'solar', 'hydro', 'biomass', 'imports', 'storage', 'coal', 'gas', 'other'];
export const FUEL_NAMES = { nuclear: 'Nuclear', wind: 'Wind', solar: 'Solar', hydro: 'Hydro', biomass: 'Biomass', imports: 'Imports', storage: 'Storage', coal: 'Coal', gas: 'Gas', other: 'Other' };
export function fuelColour(key) { return css('--c-' + key); }

function fmt(opts) { return new Intl.DateTimeFormat('en-GB', Object.assign({ timeZone: TZ }, opts)); }
export const F = {
  hm: fmt({ hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }),
  dm: fmt({ day: 'numeric', month: 'short' }),
  wd: fmt({ weekday: 'short' }),
  my: fmt({ month: 'short', year: 'numeric' }),
  y: fmt({ year: 'numeric' }),
  full: fmt({ weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' })
};
function d(t) { return new Date(t * 1000); }

/* Running totals for a stacked area chart. mix maps fuel -> values (any
   unit); with share, each column is scaled to 100. Returns uPlot rows
   [t, layer1, layer1+2, ...] and the column totals. */
export function stack(t, mix, order, share) {
  order = order || FUEL_ORDER;
  var n = t.length, base = new Array(n).fill(0), totals = new Array(n).fill(0), rows = [t];
  order.forEach(function (k) { var s = mix[k] || []; for (var i = 0; i < n; i++) totals[i] += Math.max(0, s[i] || 0); });
  order.forEach(function (k) {
    var s = mix[k] || [], line = new Array(n);
    for (var i = 0; i < n; i++) {
      var v = Math.max(0, s[i] || 0);
      if (share) v = totals[i] ? 100 * v / totals[i] : 0;
      base[i] += v;
      line[i] = totals[i] ? base[i] : null;
    }
    rows.push(line);
  });
  return { rows: rows, totals: totals };
}

/* uPlot pieces */
export function tzDate(ts) { return uPlot.tzDate(new Date(ts * 1000), TZ); }
export function timeValues(u, splits, axisIdx, space, incr) {
  return splits.map(function (v) {
    if (v === null) return null;
    if (incr < DAY) {
      var label = F.hm.format(d(v));
      return label === '00:00' ? F.dm.format(d(v)) : label;
    }
    if (incr < 28 * DAY) return F.dm.format(d(v));
    if (incr < 365 * DAY) return F.my.format(d(v));
    return F.y.format(d(v));
  });
}
export function bodyFont(size, weight) { return (weight || 400) + ' ' + size + 'px ' + getComputedStyle(document.body).fontFamily; }
export function axes(yValues, opts) {
  opts = opts || {};
  var ink = css('--muted'), grid = css('--hair'), size = opts.font || 11.5;
  return [
    { stroke: ink, font: bodyFont(size), grid: { show: false }, ticks: { stroke: grid, size: 4 }, values: timeValues, space: opts.space || 70, gap: 4, size: opts.xSize || 50 },
    { stroke: ink, font: bodyFont(size), grid: { stroke: grid, width: 1 }, ticks: { show: false }, values: yValues, size: opts.ySize || 44, gap: 6 }
  ];
}
/* Series and bands for a stacked fuel chart drawn from stack() rows */
export function stackedSeries(order) {
  order = order || FUEL_ORDER;
  var series = [{}], bands = [];
  order.forEach(function (k, i) {
    var c = fuelColour(k);
    series.push({ label: FUEL_NAMES[k], stroke: c, width: 0, fill: i === 0 ? c : null, points: { show: false } });
    if (i > 0) bands.push({ series: [i + 1, i], fill: c });
  });
  return { series: series, bands: bands };
}
export { num };
