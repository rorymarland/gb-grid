/* Year in review: /review. The grid's year and your home's (gridlive/review.py) */
import G from '/static/js/gb.js';
import { FUEL_ORDER, FUEL_NAMES, fuelColour } from '/static/js/charts.js';
import { renderParts } from '/static/js/summary.js';

var h = G.h, s = G.s, byId = G.byId, clear = G.clear, num = G.num, niceStep = G.niceStep, makeTip = G.makeTip;
var state = { data: null, mode: 'energy', year: null };
var params = new URLSearchParams(location.search);
if (/^\d{4}$/.test(params.get('year') || '')) state.year = Number(params.get('year'));
try {
  var savedMode = localStorage.getItem('gb-review-home');
  if (['energy', 'cost', 'carbon'].indexOf(savedMode) >= 0) state.mode = savedMode;
} catch (e) { /* storage unavailable */ }

var MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
var MONTHS_LONG = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
var longDate = new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'long', year: 'numeric', timeZone: G.TZ });
var dayMonth = new Intl.DateTimeFormat('en-GB', { weekday: 'short', day: 'numeric', month: 'long', timeZone: G.TZ });
function fromIso(text) { return new Date(text + 'T12:00:00Z'); }
function at(t) { return new Date(t * 1000); }

/* ---------- formatting ---------- */

function pct(v, dp) { return num(v) ? (v * 100).toFixed(dp === undefined ? 0 : dp) + '%' : '–'; }
function points(diff) {
  var p = Math.round(diff * 1000) / 10;
  return (p > 0 ? '+' : p < 0 ? '−' : '') + Math.abs(p).toFixed(1) + (Math.abs(p) === 1 ? ' point' : ' points');
}
function signed(v, unit) { var r = Math.round(v); return (r > 0 ? '+' : r < 0 ? '−' : '') + G.int(Math.abs(r)) + (unit ? ' ' + unit : ''); }
function twh(v) { return num(v) ? v.toLocaleString('en-GB', { maximumFractionDigits: v >= 100 ? 0 : 1, minimumFractionDigits: v >= 100 ? 0 : 1 }) : '–'; }
function pounds(pence) { return num(pence) ? '£' + G.int(pence / 100) : '–'; }
function kg(v) { return v >= 1000 ? (v / 1000).toFixed(2) + ' t' : G.int(v) + ' kg'; }
function bigMoney(p) { return p >= 1e9 ? '£' + (p / 1e9).toFixed(2) + 'bn' : p >= 1e6 ? '£' + Math.round(p / 1e6) + 'm' : G.money(p); }
function figure(dl, label, value, unit, note, cls) {
  dl.appendChild(h('div', { cls: cls || null }, [h('dt', { text: label }), h('dd', null, [value, unit ? h('small', { text: unit }) : null,
    note ? h('span', { cls: 'more', text: note }) : null])]));
}

/* ---------- the year so far, or saved ---------- */

function oddMonths(g) {
  var odd = (g.ci_check && g.ci_check.odd) || [];
  if (!odd.length) return null;
  // Runs of months: "March to October", "March and May"
  var runs = [], start = odd[0], prev = odd[0];
  odd.slice(1).concat([null]).forEach(function (m) {
    if (m === prev + 1) { prev = m; return; }
    runs.push(start === prev ? MONTHS_LONG[start - 1] : MONTHS_LONG[start - 1] + (prev === start + 1 ? ' and ' : ' to ') + MONTHS_LONG[prev - 1]);
    start = prev = m;
  });
  return runs.length > 1 ? runs.slice(0, -1).join(', ') + ' and ' + runs[runs.length - 1] : runs[0];
}

function describeSpan(g, year) {
  if (g.whole) return String(year);
  return year + ' so far, to ' + longDate.format(at(g.to - 1)).replace(' ' + year, '');
}

function renderHead(d) {
  var g = d.grid, year = d.year;
  byId('title').textContent = year + ' in review';
  document.title = year + ' in review – GB grid';
  var select = clear(byId('year'));
  d.years.forEach(function (y) { select.appendChild(h('option', { value: String(y), text: String(y), selected: y === year ? '' : null })); });
  var status = byId('status');
  if (d.saved) status.textContent = 'Saved on ' + longDate.format(at(d.saved)) + ', so it reads the same from now on.';
  else if (g && oddMonths(g)) status.textContent = (g.whole ? 'The whole year, not yet saved for good: ' : 'The year so far. ') + 'It’s saved once NESO’s figures settle, by 1 April ' + (year + 1) + '.';
  else if (g && !g.whole) status.textContent = 'The year so far. It fills in as the year goes on, and is saved for good in mid-January.';
  else status.textContent = '';
  if (!g) return;
  var f = g.figures, b = g.before, lede = '';
  var prevWhole = g.history.filter(function (x) { return x.year < year; });
  var lowest = prevWhole.length && prevWhole.every(function (x) { return f.ci < x.ci; });
  if (oddMonths(g)) {
    // NESO's carbon figures for part of the year look wrong: lead with the mix instead
    lede = (g.whole ? 'In ' + year : 'So far in ' + year) + ', ' + pct(f.zero) + ' of Britain’s electricity came from zero-carbon sources and ' + pct(f.mix.wind) + ' from wind' +
      (b ? ', against ' + pct(b.zero) + ' and ' + pct(b.mix.wind) + ' ' + (g.whole ? 'in ' + (year - 1) : 'on the same days of ' + (year - 1)) : '') +
      '. NESO’s carbon intensity for ' + oddMonths(g) + ' looks out of line with its own figures for the mix, so it’s marked below.' +
      (d.home ? ' Your home’s year is further down.' : '');
    byId('lede').textContent = lede;
    return;
  }
  lede = (g.whole ? 'In ' + year : 'So far in ' + year) + ', Britain’s electricity averaged ' + f.ci + ' g of CO₂ per kWh';
  if (b && num(b.ci)) {
    var change = (f.ci - b.ci) / b.ci;
    lede += Math.abs(change) < 0.005 ? ', the same as ' + (g.whole ? (year - 1) : 'the same days of ' + (year - 1))
      : ', ' + pct(Math.abs(change)) + (change < 0 ? ' less' : ' more') + ' than ' + (g.whole ? 'in ' + (year - 1) : 'the same days of ' + (year - 1));
  }
  lede += '. ' + pct(f.zero) + ' came from zero-carbon sources and ' + pct(f.mix.wind) + ' from wind' +
    (g.whole && lowest ? ', making it the cleanest year since records here began in 2009.' : '.');
  if (d.home) lede += ' Your home’s year is further down.';
  byId('lede').textContent = lede;
}

/* ---------- the long view ---------- */

function renderLong(g, year) {
  var rows = g.history, box = clear(byId('long-chart'));
  var first = rows[0], last = rows[rows.length - 1];
  byId('long-sub').textContent = first && last && first.year !== last.year
    ? 'From ' + first.ci + ' g/kWh in ' + first.year + ' to ' + last.ci + ' g/kWh in ' + last.year + (last.so_far ? ' so far' : '') +
      ', as coal closed and wind grew. Each bar is a whole year’s average' + (last.so_far ? ', except this year’s.' : '.') +
      (oddMonths(g) ? ' ' + year + '’s bar includes NESO’s figures for ' + oddMonths(g) + ', which look too high; see the note under the figures.' : '')
    : '';
  var width = Math.max(300, box.clientWidth || 900), height = width < 560 ? 220 : 280, padL = 66, padR = 6, padT = 26, padB = 26;
  var top = Math.max.apply(null, rows.map(function (r) { return r.ci; })), step = niceStep(top, 4), hi = Math.ceil(top / step) * step;
  var n = rows.length, slot = (width - padL - padR) / n, gap = Math.max(2, Math.min(8, slot * 0.22)), barW = slot - gap;
  var y = function (v) { return padT + (1 - v / hi) * (height - padT - padB); };
  var svg = s('svg', { viewBox: '0 0 ' + width + ' ' + height, height: height, role: 'img',
    'aria-label': 'Average carbon intensity of Britain’s electricity each year from ' + first.year + ' to ' + last.year });
  for (var v = 0; v <= hi + 1e-9; v += step) {
    svg.appendChild(s('line', { 'class': 'grid-line', x1: padL, x2: width - padR, y1: y(v).toFixed(1), y2: y(v).toFixed(1) }));
    svg.appendChild(s('text', { 'class': 'axis-text', x: padL - 8, y: (y(v) + 4).toFixed(1), 'text-anchor': 'end', text: G.int(v) + ' g/kWh' }));
  }
  var every = slot < 26 ? 4 : slot < 40 ? 2 : 1;
  rows.forEach(function (r, i) {
    var x0 = padL + i * slot + gap / 2, mine = r.year === year;
    var top0 = y(r.ci), base = y(0);
    // Rounded at the top, square on the baseline
    var rad = Math.min(4, barW / 2), hgt = Math.max(1, base - top0);
    var path = 'M' + x0.toFixed(1) + ' ' + base.toFixed(1) + 'V' + (top0 + rad).toFixed(1) + 'Q' + x0.toFixed(1) + ' ' + top0.toFixed(1) + ' ' + (x0 + rad).toFixed(1) + ' ' + top0.toFixed(1) +
      'H' + (x0 + barW - rad).toFixed(1) + 'Q' + (x0 + barW).toFixed(1) + ' ' + top0.toFixed(1) + ' ' + (x0 + barW).toFixed(1) + ' ' + (top0 + rad).toFixed(1) + 'V' + base.toFixed(1) + 'Z';
    svg.appendChild(s('path', { d: hgt > rad ? path : 'M' + x0 + ' ' + base + 'h' + barW + 'v-' + hgt + 'h-' + barW + 'Z', 'class': 'rv-yr' + (mine ? ' mine' : '') + (r.so_far ? ' so-far' : '') }));
    if (mine || i === 0 || (!rows.some(function (q) { return q.year === year; }) && i === n - 1)) {
      svg.appendChild(s('text', { 'class': 'rv-yr-val' + (mine ? ' mine' : ''), x: (x0 + barW / 2).toFixed(1), y: (top0 - 7).toFixed(1), 'text-anchor': 'middle', text: String(r.ci) }));
    }
    var mineAt = rows.map(function (q) { return q.year; }).indexOf(year);
    var crowded = !mine && mineAt >= 0 && Math.abs(i - mineAt) * slot < 40;   // keep clear of this year's label
    if (mine || ((i % every === 0 || i === n - 1) && !crowded)) {
      svg.appendChild(s('text', { 'class': 'axis-text' + (mine ? ' rv-yr-mine' : ''), x: (x0 + barW / 2).toFixed(1), y: height - 7, 'text-anchor': 'middle',
        text: slot < 30 && !mine ? "’" + String(r.year).slice(2) : String(r.year) }));
    }
  });
  box.appendChild(svg);
  var tip = makeTip(box);
  svg.addEventListener('pointermove', function (e) {
    var b = svg.getBoundingClientRect(), i = Math.floor(((e.clientX - b.left) * width / b.width - padL) / slot);
    if (i < 0 || i >= n) { tip.hide(); return; }
    var r = rows[i];
    tip.show(r.year + (r.so_far ? ' so far' : ''), [
      { value: r.ci + ' g/kWh', label: 'carbon intensity' },
      { cls: 'b-wind', value: pct(r.wind), label: 'wind' },
      { cls: 'b-gas', value: pct(r.gas), label: 'gas' },
      { cls: 'b-coal', value: pct(r.coal), label: 'coal' },
      { value: pct(r.zero), label: 'zero-carbon' }
    ], (padL + (i + 0.5) * slot) * b.width / width, 60);
  });
  svg.addEventListener('pointerleave', function () { tip.hide(); });
}

/* ---------- the year in figures ---------- */

function renderFigures(g, year) {
  var f = g.figures, b = g.before, dl = clear(byId('grid-figures'));
  var vs = b ? (g.whole ? 'on ' + (year - 1) : 'on the same days of ' + (year - 1)) : null;
  byId('figures-sub').textContent = describeSpan(g, year) + (b ? ', with the change ' + vs + '.' : '.');
  figure(dl, 'Carbon intensity', String(f.ci), 'g/kWh', oddMonths(g) ? 'NESO’s figure, which looks too high: see below' : b && num(b.ci) ? signed(f.ci - b.ci, 'g/kWh ' + vs) : null);
  figure(dl, 'Zero-carbon', pct(f.zero), null, b ? points(f.zero - b.zero) + ' ' + vs : 'Nuclear, wind, solar and hydro');
  figure(dl, 'Wind', pct(f.mix.wind), null, b ? points(f.mix.wind - b.mix.wind) + ' ' + vs : null);
  figure(dl, 'Gas', pct(f.mix.gas), null, b ? points(f.mix.gas - b.mix.gas) + ' ' + vs : null);
  figure(dl, 'Electricity used', twh(f.twh), 'TWh', b ? signed((f.twh - b.twh) / b.twh * 100, '') + '% ' + vs : null);
  figure(dl, 'Average wholesale price', num(f.price) ? '£' + f.price.toFixed(2) : '–', num(f.price) ? 'per MWh' : null,
    num(f.negative_hours) ? (f.negative_hours ? G.int(f.negative_hours) + ' hours below zero' : 'Never below zero') : null);
  var check = byId('ci-note');
  if (oddMonths(g)) {
    check.hidden = false;
    check.textContent = 'NESO’s carbon intensity for ' + oddMonths(g) + ' is about ' + pct(g.ci_check.excess) + ' higher than its own generation mix suggests, ' +
      'where in the two years before the two agreed to within a few per cent. It looks like an error in NESO’s historic data, which NESO says it revises as it cleans it. ' +
      'The dashboard reads the last year again every week, so these figures will change if NESO corrects them, and ' + year + ' isn’t saved for good until 1 April ' + (year + 1) + '.';
  } else check.hidden = true;
  var coal = byId('coal-note');
  if (num(g.last_coal) && g.last_coal < g.from) coal.textContent = 'No coal all year. Britain’s last coal-fired electricity was on ' + longDate.format(at(g.last_coal)) + '.';
  else if (num(g.last_coal) && g.whole && g.history.length && g.figures.mix.coal > 0) coal.textContent = 'Coal still made ' + pct(g.figures.mix.coal, 1) + ' of the year’s electricity.';
  else coal.textContent = '';
}

/* ---------- the mix ---------- */

function renderMix(g) {
  var f = g.figures, bar = clear(byId('year-mix')), legend = clear(byId('mix-legend'));
  var order = FUEL_ORDER.filter(function (k) { return f.mix[k] > 0.0005; });
  bar.setAttribute('aria-label', order.map(function (k) { return FUEL_NAMES[k] + ' ' + pct(f.mix[k]); }).join(', '));
  order.forEach(function (k) {
    bar.appendChild(h('i', { cls: 'b-' + k, style: 'flex:' + f.mix[k].toFixed(4) + ' 1 0', title: FUEL_NAMES[k] + ': ' + pct(f.mix[k], 1) }));
    legend.appendChild(h('li', null, [h('i', { cls: 'sw b-' + k }), h('span', null, [FUEL_NAMES[k], ' ', h('b', { text: pct(f.mix[k], f.mix[k] < 0.01 ? 1 : 0) }),
      h('small', { text: twh(f.fuel_twh[k]) + ' TWh' })])]));
  });
  renderMonths(g);
}

function renderMonths(g) {
  var box = clear(byId('months-chart')), months = g.months;
  if (!months.length) return;
  var width = Math.max(300, box.clientWidth || 900), height = width < 560 ? 230 : 280, padL = 44, padR = 4, padT = 10, padB = 48;
  var slot = (width - padL - padR) / 12, gap = Math.max(3, slot * 0.2), barW = slot - gap;
  var y = function (v) { return padT + (1 - v) * (height - padT - padB); };
  var svg = s('svg', { viewBox: '0 0 ' + width + ' ' + height, height: height, role: 'img', 'aria-label': 'Each month’s generation mix, with its carbon intensity underneath' });
  [0, 0.25, 0.5, 0.75, 1].forEach(function (v) {
    svg.appendChild(s('line', { 'class': 'grid-line', x1: padL, x2: width - padR, y1: y(v).toFixed(1), y2: y(v).toFixed(1) }));
    svg.appendChild(s('text', { 'class': 'axis-text', x: padL - 8, y: (y(v) + 4).toFixed(1), 'text-anchor': 'end', text: Math.round(v * 100) + '%' }));
  });
  months.forEach(function (m) {
    var i = m.m - 1, x0 = padL + i * slot + gap / 2, acc = 0;
    FUEL_ORDER.forEach(function (k) {
      var v = m.mix[k] || 0;
      if (v <= 0.0005) return;
      var y1 = y(acc), y0 = y(acc + v), hgt = Math.max(0.5, y1 - y0 - 1);
      svg.appendChild(s('rect', { 'class': 'f-' + k + (m.whole ? '' : ' rv-partial'), x: x0.toFixed(1), y: (y1 - hgt).toFixed(1), width: barW.toFixed(1), height: hgt.toFixed(1) }));
      acc += v;
    });
    svg.appendChild(s('text', { 'class': 'axis-text', x: (x0 + barW / 2).toFixed(1), y: height - padB + 16, 'text-anchor': 'middle', text: MONTHS[i] + (m.whole ? '' : '*') }));
    var odd = g.ci_check && g.ci_check.odd.indexOf(m.m) >= 0;
    svg.appendChild(s('text', { 'class': 'rv-ci-row' + (odd ? ' odd' : ''), x: (x0 + barW / 2).toFixed(1), y: height - 8, 'text-anchor': 'middle', text: num(m.ci) ? m.ci + (odd ? '†' : '') : '–' }));
  });
  svg.appendChild(s('text', { 'class': 'axis-text', x: padL - 8, y: height - 8, 'text-anchor': 'end', text: 'g/kWh' }));
  box.appendChild(svg);
  var tip = makeTip(box);
  svg.addEventListener('pointermove', function (e) {
    var b = svg.getBoundingClientRect(), i = Math.floor(((e.clientX - b.left) * width / b.width - padL) / slot);
    var m = months.filter(function (q) { return q.m === i + 1; })[0];
    if (!m) { tip.hide(); return; }
    var rows = FUEL_ORDER.slice().reverse().filter(function (k) { return (m.mix[k] || 0) > 0.0005; })
      .map(function (k) { return { cls: 'b-' + k, value: pct(m.mix[k]), label: FUEL_NAMES[k].toLowerCase() }; });
    rows.push({ sep: true }, { value: m.ci + ' g/kWh', label: 'carbon intensity' + (g.ci_check && g.ci_check.odd.indexOf(m.m) >= 0 ? ', looks too high' : '') }, { value: twh(m.twh) + ' TWh', label: 'used' });
    if (num(m.price)) rows.push({ value: '£' + m.price.toFixed(2), label: 'per MWh, average' });
    tip.show(MONTHS_LONG[i] + (m.whole ? '' : ', so far'), rows, (padL + (i + 0.5) * slot) * b.width / width, 40);
  });
  svg.addEventListener('pointerleave', function () { tip.hide(); });
}

/* ---------- highs and lows ---------- */

function bestValue(b) {
  if (b.unit === 'MW') return [G.gw(b.value), 'GW'];
  if (b.unit === '%') return [b.value.toFixed(1), '%'];
  if (b.unit === '£/MWh') return [(b.value < 0 ? '−£' : '£') + Math.abs(b.value).toFixed(2), 'per MWh'];
  return [G.int(b.value), b.unit];
}

function renderBests(g) {
  var dl = clear(byId('bests'));
  g.bests.forEach(function (b) {
    var v = bestValue(b);
    var when = b.span === 'day' ? dayMonth.format(at(b.t + 43200)) + (b.unit === 'MW' ? ', on average' : ', the day’s average') : dayMonth.format(at(b.t)) + ', ' + G.hm(b.t);
    dl.appendChild(h('div', { cls: 'rv-best' + (b.record ? ' record' : '') }, [
      h('dt', { text: b.title }),
      h('dd', null, [h('b', { text: v[0] }), h('small', { text: v[1] }), h('span', { cls: 'when', text: when }),
        b.record ? h('span', { cls: 'rv-badge', text: (b.better === 'low' ? 'Lowest' : 'Highest') + ' since 2009' }) : null])
    ]));
  });
}

/* ---------- wind and constraints ---------- */

function renderWind(g, year) {
  var f = g.figures, c = g.constraints, dl = clear(byId('wind-figures')), note = byId('wind-note');
  byId('wind-sub').textContent = 'What wind farms made in ' + describeSpan(g, year) + ', and what NESO paid them not to make when the network couldn’t carry it.';
  figure(dl, 'Wind generated', twh(f.fuel_twh.wind), 'TWh', pct(f.mix.wind) + ' of the electricity used');
  if (c && num(c.down_twh)) {
    figure(dl, 'Wind turned down', twh(c.down_twh), 'TWh', num(c.down_share) ? pct(c.down_share, 1) + ' of what transmission wind farms could have made'
      : 'From ' + dayMonth.format(fromIso(c.down_from)));
  }
  if (c && num(c.cost)) {
    var partial = c.cost_from > year + '-01-01';
    figure(dl, 'Constraint costs', bigMoney(c.cost), null, partial ? 'From ' + dayMonth.format(fromIso(c.cost_from)) + ' to ' + dayMonth.format(fromIso(c.cost_to))
      : 'To ' + dayMonth.format(fromIso(c.cost_to)) + ', what NESO publishes so far');
  }
  note.innerHTML = '';
  if (!c) note.textContent = 'Wind turned down and constraint costs appear here once the Constraints page has data for the year.';
  else note.appendChild(h('span', null, ['Day by day, and boundary by boundary, on the ', h('a', { href: '/constraints', text: 'Constraints' }), ' page.']));
}

/* ---------- moments ---------- */

function renderMoments(g) {
  var list = clear(byId('moments')), sub = byId('moments-sub');
  clear(sub);
  sub.appendChild(document.createTextNode('The year’s major events from the '));
  sub.appendChild(h('a', { href: '/timeline', text: 'timeline' }));
  sub.appendChild(document.createTextNode(g.moments_from ? ', which began recording on ' + longDate.format(at(g.moments_from)) + '.' : '.'));
  if (g.frequency_events > 1) {
    sub.appendChild(document.createTextNode(' There were ' + G.int(g.frequency_events) + ' major frequency events; the biggest is listed.'));
  }
  if (!g.moments.length) {
    list.appendChild(h('li', { cls: 'rv-none', text: 'No major events were recorded.' }));
    return;
  }
  g.moments.forEach(function (m) {
    var title = h('span', { cls: 'what' });
    renderParts(m.title).forEach(function (n) { title.appendChild(n); });
    list.appendChild(h('li', null, [h('time', { datetime: new Date(m.t * 1000).toISOString(), text: dayMonth.format(at(m.t)) }), title]));
  });
}

/* ---------- your home ---------- */

var HOME_MODES = {
  energy: { e: 'e', g: 'g', unit: 'kWh', axis: function (v) { return G.int(v) + ' kWh'; }, fmt: function (v) { return G.int(v) + ' kWh'; } },
  cost: { e: 'e_cost', g: 'g_cost', unit: '£', axis: function (v) { return '£' + G.int(v / 100); }, fmt: pounds },
  carbon: { e: 'e_kg', g: 'g_kg', unit: 'kg', axis: function (v) { return G.int(v) + ' kg'; }, fmt: function (v) { return G.int(v) + ' kg'; } }
};

function renderHome(d) {
  var home = d.home, panel = byId('home-panel');
  panel.hidden = !home;
  if (!home) return;
  var t = home.total, b = home.before, year = d.year, dl = clear(byId('home-figures'));
  var vs = b ? 'on the same days of ' + (year - 1) : null;
  function change(now, then, unit) {
    if (!b || !num(now) || !num(then) || !then) return null;
    var p = (now - then) / then * 100;
    return (Math.abs(p) < 0.5 ? 'The same as ' : signed(p, '') + '% ') + vs + (unit ? '' : '');
  }
  byId('home-sub').textContent = 'From your meter readings, ' + (home.partial_start ? 'which begin on ' + dayMonth.format(fromIso(home.from)) + ', ' : '') +
    'to ' + dayMonth.format(fromIso(home.to)) + '. Costs include standing charges.';
  figure(dl, 'Electricity', G.int(t.e), 'kWh', change(t.e, b && b.e));
  if (num(t.g)) figure(dl, 'Gas', G.int(t.g), 'kWh', change(t.g, b && b.g));
  figure(dl, 'Cost', pounds(t.cost), null, num(t.cost) ? change(t.cost, b && b.cost) : 'Not every day could be costed');
  if (num(home.kg)) figure(dl, 'Carbon', kg(home.kg).split(' ')[0], kg(home.kg).split(' ')[1], 'Electricity ' + kg(t.e_kg || 0) + (num(t.g_kg) ? ', gas ' + kg(t.g_kg) : ''));
  Array.prototype.forEach.call(byId('home-mode').querySelectorAll('button'), function (btn) {
    btn.setAttribute('aria-pressed', String(btn.dataset.mode === state.mode));
  });
  renderHomeChart(home);
  var list = clear(byId('home-points'));
  if (home.timing) {
    var diff = home.timing.yours - home.timing.flat;
    list.appendChild(h('li', { text: 'Your electricity averaged ' + home.timing.yours.toFixed(0) + ' g/kWh, against ' + home.timing.flat.toFixed(0) +
      ' g/kWh if you’d used it evenly through the day. ' + (Math.abs(diff) < 1 ? 'Your timing made little difference.'
        : diff < 0 ? 'Using more when the grid was cleaner saved ' + G.int(Math.abs(diff) / home.timing.flat * 100) + '% of the carbon.'
          : 'Using more when the grid was dirtier added ' + G.int(diff / home.timing.flat * 100) + '% to the carbon.') }));
  }
  if (home.busiest) list.appendChild(h('li', { text: 'Your busiest day for electricity was ' + dayMonth.format(fromIso(home.busiest.d)) + ', at ' + home.busiest.e.toFixed(1) + ' kWh.' }));
  if (home.sessions) {
    var sn = home.sessions;
    list.appendChild(h('li', { text: 'You joined ' + G.plural(sn.joined, 'Saving Session', 'Saving Sessions') +
      (num(sn.points) ? ', earning ' + G.int(sn.points) + ' Octopoints (£' + (sn.points / 800).toFixed(2) + ')' : '') +
      (sn.waiting ? '; ' + G.plural(sn.waiting, 'is', 'are') + ' still waiting for points' : '') + '.' }));
  }
  byId('gas-factor').textContent = String(home.gas_factor);
}

function renderHomeChart(home) {
  var mode = HOME_MODES[state.mode], box = clear(byId('home-chart')), months = home.months;
  var legend = clear(byId('home-legend'));
  if (!months.length) return;
  var hasGas = months.some(function (m) { return num(m[mode.g]); });
  var width = Math.max(300, box.clientWidth || 900), height = width < 560 ? 200 : 240, padL = 62, padR = 4, padT = 10, padB = 26;
  var slot = (width - padL - padR) / 12, gap = Math.max(3, slot * 0.2), barW = slot - gap;
  var top = Math.max.apply(null, months.map(function (m) { return (m[mode.e] || 0) + (m[mode.g] || 0); }).concat([1]));
  var step = niceStep(top, 4), hi = Math.ceil(top / step) * step;
  var y = function (v) { return padT + (1 - v / hi) * (height - padT - padB); };
  var svg = s('svg', { viewBox: '0 0 ' + width + ' ' + height, height: height, role: 'img', 'aria-label': 'Your home’s ' + state.mode + ' each month' });
  for (var v = 0; v <= hi + 1e-9; v += step) {
    svg.appendChild(s('line', { 'class': 'grid-line', x1: padL, x2: width - padR, y1: y(v).toFixed(1), y2: y(v).toFixed(1) }));
    svg.appendChild(s('text', { 'class': 'axis-text', x: padL - 8, y: (y(v) + 4).toFixed(1), 'text-anchor': 'end', text: mode.axis(v) }));
  }
  months.forEach(function (m) {
    var i = m.m - 1, x0 = padL + i * slot + gap / 2, base = y(0);
    [['e', 'rv-elec'], ['g', 'rv-gas']].forEach(function (part) {
      var value = m[mode[part[0]]];
      if (!num(value) || value <= 0) return;
      var hgt = Math.max(1, base - y(value) - (part[0] === 'g' ? 2 : 0));
      svg.appendChild(s('rect', { 'class': part[1] + (m.days < m.length ? ' rv-partial' : ''), x: x0.toFixed(1), y: (base - hgt).toFixed(1), width: barW.toFixed(1), height: hgt.toFixed(1), rx: 2 }));
      base -= hgt + 2;
    });
    svg.appendChild(s('text', { 'class': 'axis-text', x: (x0 + barW / 2).toFixed(1), y: height - 8, 'text-anchor': 'middle', text: MONTHS[i] + (m.days < m.length ? '*' : '') }));
  });
  box.appendChild(svg);
  var tip = makeTip(box);
  svg.addEventListener('pointermove', function (e) {
    var b = svg.getBoundingClientRect(), i = Math.floor(((e.clientX - b.left) * width / b.width - padL) / slot);
    var m = months.filter(function (q) { return q.m === i + 1; })[0];
    if (!m) { tip.hide(); return; }
    var rows = [{ cls: 'rv-elec', value: num(m[mode.e]) ? mode.fmt(m[mode.e]) : '–', label: 'electricity' }];
    if (hasGas) rows.push({ cls: 'rv-gas', value: num(m[mode.g]) ? mode.fmt(m[mode.g]) : '–', label: 'gas' });
    tip.show(MONTHS_LONG[i] + (m.days < m.length ? ' (' + m.days + ' of ' + m.length + ' days)' : ''), rows, (padL + (i + 0.5) * slot) * b.width / width, 40);
  });
  svg.addEventListener('pointerleave', function () { tip.hide(); });
  legend.appendChild(h('li', null, [h('i', { cls: 'sw rv-elec' }), 'Electricity']));
  if (hasGas) legend.appendChild(h('li', null, [h('i', { cls: 'sw rv-gas' }), 'Gas']));
  if (months.some(function (m) { return m.days < m.length; })) legend.appendChild(h('li', { cls: 'rv-legend-note', text: '* Part of the month' }));
}

/* ---------- the page ---------- */

function render(d) {
  state.data = d;
  var g = d.grid;
  renderHead(d);
  var waiting = byId('waiting');
  byId('content').hidden = !g;
  waiting.hidden = Boolean(g);
  if (!g) {
    waiting.textContent = d.years && d.years.indexOf(d.year) < 0 ? 'There’s no history for ' + d.year + '.'
      : 'Waiting for the grid’s history. The first download of NESO’s record since 2009 takes a few minutes.';
    G.setLive('', 'Waiting for history');
    return;
  }
  renderLong(g, d.year);
  renderFigures(g, d.year);
  renderMix(g);
  renderBests(g);
  renderWind(g, d.year);
  renderMoments(g);
  renderHome(d);
  G.setLive('good', d.saved ? d.year + ', as saved' : 'Up to ' + longDate.format(at(g.to - 1)));
}

function url() { return '/api/review' + (state.year ? '?year=' + state.year : ''); }

byId('year').addEventListener('change', function (e) {
  state.year = Number(e.target.value);
  var u = new URL(location.href);
  u.searchParams.set('year', String(state.year));
  history.replaceState(null, '', u);
  poller.setUrl(url());
});
byId('home-mode').addEventListener('click', function (e) {
  var b = e.target.closest('button[data-mode]');
  if (!b || !state.data) return;
  state.mode = b.dataset.mode;
  try { localStorage.setItem('gb-review-home', state.mode); } catch (err) { /* storage unavailable */ }
  renderHome(state.data);
});
byId('print').addEventListener('click', function () { window.print(); });
/* Always print in light: dark ink on white paper */
var printTheme = null;
window.addEventListener('beforeprint', function () {
  printTheme = document.documentElement.getAttribute('data-theme');
  if (printTheme === 'dark') { document.documentElement.setAttribute('data-theme', 'light'); if (state.data && state.data.grid) render(state.data); }
});
window.addEventListener('afterprint', function () {
  if (printTheme === 'dark') { document.documentElement.setAttribute('data-theme', 'dark'); if (state.data && state.data.grid) render(state.data); }
  printTheme = null;
});

var poller = G.poll(url(), {
  interval: function () { return 1800; },
  onData: render,
  onTick: function (p) { if (G.offline(p)) G.setLive('bad', 'Can’t reach the dashboard server'); }
});

var resizeTimer = null, lastWidth = window.innerWidth;
window.addEventListener('resize', function () {
  if (window.innerWidth === lastWidth) return;
  lastWidth = window.innerWidth;
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(function () { if (state.data && state.data.grid) render(state.data); }, 150);
});
