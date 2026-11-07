/* The next 48 hours: four small charts sharing one time axis and one
   cursor. Used on the Overview and in the Display view.

     var outlook = mountOutlook(element, { height: 92 });   // or a function,
                                                          // or { fill: true }
     outlook.render();                                    // after a resize

   The styles (.ol and friends) live in base.css. */

import G from './gb.js';

var h = G.h, sv = G.s, num = G.num;
var DEFS = [
  { key: 'demand', title: 'Demand', unit: 'GW', colour: '--line', scale: 1000, digits: 1, peak: 'max' },
  { key: 'wind', title: 'Wind', unit: 'GW', colour: '--c-wind', scale: 1000, digits: 1, peak: 'max' },
  { key: 'solar', title: 'Solar', unit: 'GW', colour: '--c-solar', scale: 1000, digits: 1, peak: 'max' },
  { key: 'carbon', title: 'Carbon intensity', unit: 'g/kWh', colour: '--ci-moderate', scale: 1, digits: 0, peak: 'min' }
];
function fmtv(def, v) { return num(v) ? (v / def.scale).toFixed(def.digits) : '–'; }
function valueAt(series, t) {
  var best = null;
  series.forEach(function (p) { if (p[0] <= t) best = p; });
  if (!best && series.length && series[0][0] - t <= 3600) best = series[0];  // hourly forecasts start on the hour
  return best ? best[1] : null;
}
export function mountOutlook(host, opts) {
opts = opts || {};
var data = null, hoverT = null;
function chartHeight() { return typeof opts.height === 'function' ? opts.height() : (opts.height || 92); }
function render() {
  G.clear(host);
  if (!data) return;
  var from = data.from, to = data.to, span = to - from || 1;
  DEFS.forEach(function (def) {
    var series = data[def.key] || [], card = h('div', { cls: 'ol' });
    host.appendChild(card);
    card.appendChild(h('h3', { text: def.key === 'demand' && data.demand_kind === 'transmission' ? 'Transmission demand' : def.title }));
    if (series.length < 2) {
      card.appendChild(h('p', { cls: 'peak', text: 'Waiting for the forecast.' }));
      return;
    }
    var t = num(hoverT) ? hoverT : Date.now() / 1000, v = valueAt(series, t);
    card.appendChild(h('p', { cls: 'now' }, [fmtv(def, v), h('small', { text: def.unit + (num(hoverT) ? ' at ' + G.when(hoverT) : ' now') })]));
    var pick = series.reduce(function (a, p) { return (def.peak === 'max' ? p[1] > a[1] : p[1] < a[1]) ? p : a; });
    card.appendChild(h('p', { cls: 'peak', text: (def.peak === 'max' ? 'Peaks at ' : 'Lowest ') + fmtv(def, pick[1]) + ' ' + def.unit + ', ' + G.when(pick[0]) }));
    var svg = sv('svg', { 'aria-hidden': 'true' });
    card.appendChild(svg);
    var W = svg.clientWidth || 240, top = 4, bottom = 16;
    /* fill: the chart takes whatever height its card has left */
    var H = opts.fill ? Math.max(60, Math.floor(card.getBoundingClientRect().bottom - svg.getBoundingClientRect().top - 2)) : chartHeight();
    svg.style.height = H + 'px';
    svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H);
    var hi = Math.max.apply(null, series.map(function (p) { return p[1]; }));
    hi = hi <= 0 ? 1 : hi * 1.08;
    var X = function (tt) { return W * (tt - from) / span; }, Y = function (vv) { return top + (H - top - bottom) * (1 - Math.max(0, vv) / hi); };
    // Midnights (UK time)
    for (var m = from + 1800; m < to; m += 1800) {
      if (G.hm(m) === '00:00') {
        svg.appendChild(sv('line', { x1: X(m), x2: X(m), y1: top, y2: H - bottom + 3, class: 'day' }));
        svg.appendChild(sv('text', { x: X(m) + 4, y: H - 3, class: 'dl', text: G.day(m).split(' ')[0] }));
      }
    }
    var colour = G.css(def.colour), d = '';
    series.forEach(function (p, i) { d += (i ? 'L' : 'M') + X(p[0]).toFixed(1) + ' ' + Y(p[1]).toFixed(1); });
    var base = Y(0).toFixed(1);
    svg.appendChild(sv('path', { d: d + 'L' + X(series[series.length - 1][0]).toFixed(1) + ' ' + base + 'L' + X(series[0][0]).toFixed(1) + ' ' + base + 'Z', class: 'area', style: 'fill:' + colour }));
    svg.appendChild(sv('path', { d: d, class: 'ln', style: 'stroke:' + colour }));
    svg.appendChild(sv('line', { x1: 0, x2: W, y1: Y(0), y2: Y(0), class: 'day' }));
    if (num(v)) {
      svg.appendChild(sv('line', { x1: X(t), x2: X(t), y1: top, y2: H - bottom, class: 'cur' }));
      svg.appendChild(sv('circle', { cx: X(t), cy: Y(v), r: 4, class: 'cur-dot', style: 'fill:' + colour }));
    }
    svg.addEventListener('pointermove', function (e) {
      var r = svg.getBoundingClientRect();
      var tt = from + span * G.clamp((e.clientX - r.left) / r.width, 0, 1);
      tt = Math.floor(tt / 1800) * 1800;
      if (tt !== hoverT) { hoverT = tt; render(); }
    });
    svg.addEventListener('pointerleave', function () { hoverT = null; render(); });
  });
}
window.addEventListener('gb:theme', render);
window.addEventListener('gb:width', render);
G.poll('/api/outlook', { onData: function (d) { data = d; render(); if (opts.onData) opts.onData(d); }, interval: function () { return 300; } });
setInterval(function () { if (!num(hoverT)) render(); }, 60000);
return { render: render, data: function () { return data; } };
}
