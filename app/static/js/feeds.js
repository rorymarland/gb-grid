/* Data sources: the health of each upstream feed, in every page's footer.
   A one-line summary ("All 9 data sources working") that opens to the list. */

import { h, s, clear, byId } from './dom.js';
import { hm, ago, dayKey } from './format.js';

var ORDER = ['generation', 'frequency', 'carbon', 'region', 'price', 'solar', 'embedded', 'ddu', 'units', 'home'];
/* Seconds after which a feed's newest data counts as delayed */
var STALE_AFTER = { generation: 1200, frequency: 600, price: 3 * 3600, solar: 2 * 3600, units: 1800, home: 1800 };
var weekday = new Intl.DateTimeFormat('en-GB', { weekday: 'long', timeZone: 'Europe/London' });

function relDay(t) {
  var now = Date.now() / 1000, day = dayKey(t);
  if (day === dayKey(now)) return 'today';
  if (day === dayKey(now - 86400)) return 'yesterday';
  return 'on ' + weekday.format(new Date(t * 1000));
}

export function statusIcon(kind) {
  var colour = kind === 'good' ? 'var(--good)' : kind === 'warn' ? 'var(--warn)' : kind === 'bad' ? 'var(--bad)' : 'var(--muted)';
  var svg = s('svg', { viewBox: '0 0 16 16', 'aria-hidden': 'true', 'class': 'status-icon' });
  if (kind === 'warn') {
    svg.appendChild(s('path', { d: 'M8 1.5 15 14.5H1Z', fill: colour }));
    svg.appendChild(s('path', { d: 'M8 6v4M8 12v.5', stroke: '#14202b', 'stroke-width': 1.6, 'stroke-linecap': 'round' }));
  } else {
    svg.appendChild(s('circle', { cx: 8, cy: 8, r: 7, fill: colour }));
    var mark = kind === 'good' ? 'M4.8 8.3 7 10.4 11.3 5.8' : kind === 'bad' ? 'M5.5 5.5l5 5M10.5 5.5l-5 5' : 'M5 8h6';
    svg.appendChild(s('path', { d: mark, fill: 'none', stroke: '#ffffff', 'stroke-width': 1.7, 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }));
  }
  return svg;
}

/* Each feed's state: good, warn (retrying or delayed), bad (never worked) or idle (starting) */
export function feedStates(sources) {
  var now = Date.now() / 1000;
  return ORDER.filter(function (key) { return sources && sources[key]; }).map(function (key) {
    var feed = sources[key], kind = 'good', word = 'Working';
    if (!feed.last_success) {
      kind = feed.failures ? 'bad' : 'idle';
      word = feed.failures ? 'Unavailable' : 'Starting';
    } else if (feed.failures) {
      kind = 'warn'; word = 'Retrying';
    } else if (STALE_AFTER[key] && feed.data_time && now - feed.data_time > STALE_AFTER[key]) {
      kind = 'warn'; word = 'Delayed';
    }
    var details = word;
    if (feed.last_success) details += ', updated ' + ago(now - feed.last_success);
    if (feed.data_time) details += '; latest data for ' + hm(feed.data_time) + (relDay(feed.data_time) === 'today' ? '' : ' ' + relDay(feed.data_time));
    if (feed.interval) details += '. Checked every ' + (feed.interval >= 3600 ? Math.round(feed.interval / 3600) + ' h' : feed.interval >= 60 ? Math.round(feed.interval / 60) + ' min' : feed.interval + ' s') + '.';
    return { key: key, label: feed.label, kind: kind, word: word, details: details, error: feed.last_error };
  });
}

function render(data) {
  var box = byId('feeds-box');
  if (!box || !data) return;
  var states = feedStates(data.sources);
  var list = clear(byId('feeds'));
  states.forEach(function (f) {
    list.appendChild(h('li', { cls: 'feed' }, [
      statusIcon(f.kind), h('span', { cls: 'name', text: f.label }), h('span', { cls: 'state', text: f.details }),
      f.error && f.kind !== 'good' ? h('span', { cls: 'err', text: f.error }) : null
    ]));
  });
  if (data.persistent === false) {
    list.appendChild(h('li', { cls: 'feed' }, [statusIcon('warn'), h('span', { cls: 'name', text: 'Storage' }),
      h('span', { cls: 'state', text: 'The data folder isn’t writable, so history resets when the container restarts.' })]));
  }
  // The summary names what needs attention, worst first
  var bad = states.filter(function (f) { return f.kind === 'bad'; });
  var warn = states.filter(function (f) { return f.kind === 'warn'; });
  var starting = states.filter(function (f) { return f.kind === 'idle'; });
  var names = function (items) { return items.map(function (f) { return f.label.replace(/ \(.*\)$/, ''); }).join(', '); };
  var kind = 'good', text;
  if (bad.length) { kind = 'bad'; text = names(bad) + ' unavailable'; }
  else if (warn.length) { kind = 'warn'; text = names(warn) + ': ' + warn[0].word.toLowerCase(); }
  else if (data.persistent === false) { kind = 'warn'; text = 'History isn’t being saved'; }
  else if (starting.length === states.length) { kind = 'idle'; text = 'Starting up'; }
  else text = 'All ' + states.length + ' working';
  var summary = clear(byId('feeds-summary'));
  summary.appendChild(statusIcon(kind));
  summary.appendChild(h('span', null, [h('b', { text: 'Data sources: ' }), text]));
  box.dataset.state = kind;
}

var timer = null;
function load() {
  clearTimeout(timer);
  fetch('/api/status', { cache: 'no-store' })
    .then(function (r) { return r.json(); })          // 503 still carries the details
    .then(render)
    .catch(function () { /* the page's own status line reports a lost connection */ })
    .then(function () { if (!document.hidden) timer = setTimeout(load, 60000); });
}

export function mountFeeds() {
  if (!byId('feeds-box')) return;
  load();
  document.addEventListener('visibilitychange', function () { if (!document.hidden) load(); });
}
