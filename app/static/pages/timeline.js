import G from '/static/js/gb.js';
import { renderParts } from '/static/js/summary.js';

var h = G.h, s = G.s, byId = G.byId, clear = G.clear, num = G.num;
var ORDER = ['frequency', 'outage', 'price', 'notice', 'record', 'clean', 'balancing', 'demand'];
var state = { days: 7, data: null, poll: null, off: {}, daily: false };
try {
  var saved = JSON.parse(localStorage.getItem('gb-timeline') || '{}');
  state.off = saved.off || {};
  state.daily = !!saved.daily;
} catch (e) { /* storage unavailable */ }
byId('daily').checked = state.daily;

function remember() {
  try { localStorage.setItem('gb-timeline', JSON.stringify({ off: state.off, daily: state.daily })); } catch (e) { /* ignore */ }
}

function renderKinds() {
  var d = state.data, host = clear(byId('kinds'));
  ORDER.forEach(function (k) {
    if (!d.kinds[k]) return;
    var n = d.counts[k] || 0;
    var b = h('button', { type: 'button', cls: 'chip k-' + k, 'aria-pressed': String(!state.off[k]), 'data-kind': k }, [
      h('i', { cls: 'mark' }), d.kinds[k], h('span', { cls: 'n', text: String(n) })
    ]);
    host.appendChild(b);
  });
}

function visible(e) {
  if (state.off[e.kind]) return false;
  return e.level >= 2 || state.daily;
}

function renderList() {
  var d = state.data, host = clear(byId('timeline'));
  var events = d.events.filter(visible);
  if (!events.length) {
    host.appendChild(h('p', { cls: 'empty', text: d.events.length ? 'Nothing to show with these filters. Turn on more kinds of event, or the daily markers.' : 'Nothing yet. Events appear here as the dashboard spots them; give it an hour or two after a first start.' }));
    return;
  }
  var day = null, list = null;
  events.forEach(function (e) {
    var key = G.dayKey(e.t);
    if (key !== day) {
      day = key;
      host.appendChild(h('h2', { cls: 'tl-day', text: dayTitle(e.t) }));
      list = h('ol', { cls: 'tl' });
      host.appendChild(list);
    }
    list.appendChild(item(e));
  });
}

function dayTitle(t) {
  var now = Date.now() / 1000;
  if (G.dayKey(t) === G.dayKey(now)) return 'Today';
  if (G.dayKey(t) === G.dayKey(now - 86400)) return 'Yesterday';
  return new Intl.DateTimeFormat('en-GB', { weekday: 'long', day: 'numeric', month: 'long', year: G.dayKey(t).slice(0, 4) === G.dayKey(now).slice(0, 4) ? undefined : 'numeric', timeZone: G.TZ }).format(new Date(t * 1000));
}

function item(e) {
  var li = h('li', { cls: 'tl-item k-' + e.kind + ' lv' + e.level, id: e.id });
  li.appendChild(h('time', { datetime: new Date(e.t * 1000).toISOString(), text: G.hm(e.t) }));
  li.appendChild(h('i', { cls: 'mark', 'aria-hidden': 'true' }));
  var body = h('div', { cls: 'tl-body' }, [
    h('p', { cls: 'tl-kind', text: state.data.kinds[e.kind] + (e.level >= 3 ? ', major' : '') }),
    h('h3', null, renderParts(e.title))
  ]);
  if (e.freq && e.freq.series) body.appendChild(spark(e.freq));
  if (e.detail && e.detail.length) body.appendChild(h('p', { cls: 'tl-detail' }, renderParts(e.detail)));
  if (e.links && e.links.length) body.appendChild(h('p', { cls: 'tl-links' }, e.links.map(function (l) { return h('a', { href: l.href, text: l.t }); })));
  li.appendChild(body);
  return li;
}

function spark(f) {
  var se = f.series, W = 220, H = 48;
  var vs = se.map(function (p) { return p[1]; });
  var lo = Math.min(49.85, Math.min.apply(null, vs)), hi = Math.max(50.15, Math.max.apply(null, vs));
  var t0 = se[0][0], t1 = se[se.length - 1][0];
  var x = function (t) { return 2 + (t - t0) / Math.max(1, t1 - t0) * (W - 4); };
  var y = function (v) { return 3 + (hi - v) / (hi - lo) * (H - 6); };
  var svg = s('svg', { 'class': 'tl-spark', viewBox: '0 0 ' + W + ' ' + H, width: W, height: H, role: 'img',
    'aria-label': 'Frequency around the event, from ' + (f.pre || 50).toFixed(2) + ' Hz to ' + (f.peak || 50).toFixed(2) + ' Hz' });
  svg.appendChild(s('line', { 'class': 'zero-line', x1: 0, x2: W, y1: y(50), y2: y(50) }));
  if (lo < 49.8) svg.appendChild(s('line', { 'class': 'limit', x1: 0, x2: W, y1: y(49.8), y2: y(49.8) }));
  if (hi > 50.2) svg.appendChild(s('line', { 'class': 'limit', x1: 0, x2: W, y1: y(50.2), y2: y(50.2) }));
  svg.appendChild(s('path', { 'class': 'f-line', d: se.map(function (p, i) { return (i ? 'L' : 'M') + x(p[0]).toFixed(1) + ' ' + y(p[1]).toFixed(1); }).join('') }));
  return svg;
}

function onData(d) {
  state.data = d;
  renderKinds();
  renderList();
  if (location.hash && !state.jumped) {
    var el = document.getElementById(location.hash.slice(1));
    if (el) { state.jumped = true; el.classList.add('selected'); el.scrollIntoView({ block: 'center' }); }
  }
}
function updateHeader(poll) {
  if (G.offline(poll)) G.setLive('bad', 'Can’t reach the dashboard server. Retrying.');
  else if (!state.data) G.setLive('', 'Connecting to the dashboard server…');
  else {
    var n = state.data.events.length;
    G.setLive('good', 'Live. ' + G.plural(n, 'event', 'events') + ' in the last ' + (state.days === 7 ? 'week' : state.days === 30 ? 'month' : 'year') + '.');
  }
}

byId('kinds').addEventListener('click', function (ev) {
  var b = ev.target.closest('button[data-kind]');
  if (!b) return;
  var k = b.dataset.kind;
  state.off[k] = !state.off[k];
  if (!state.off[k]) delete state.off[k];
  remember();
  renderKinds();
  renderList();
});
byId('daily').addEventListener('change', function () { state.daily = this.checked; remember(); renderList(); });
byId('span').addEventListener('click', function (ev) {
  var b = ev.target.closest('button[data-days]');
  if (!b) return;
  state.days = +b.dataset.days;
  byId('span').querySelectorAll('button').forEach(function (x) { x.setAttribute('aria-pressed', String(x === b)); });
  state.poll.setUrl('/api/events?days=' + state.days);
});
state.poll = G.poll('/api/events?days=' + state.days, { onData: onData, onTick: updateHeader, interval: function () { return 60; } });
