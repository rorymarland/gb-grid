/* "Today on the grid": the server's plain-English account of the day
   (/api/summary), with glossary terms and links. Used on the Overview and
   in the Display view.

     mountSummary(element, { plain: false, points: 9 }); */

import { h, clear } from './dom.js';
import { poll, fetchJSON } from './net.js';
import { term } from './glossary.js';
import { ago } from './format.js';

/* Text parts from the server: "text" | { t, term } | { t, href } */
export function renderParts(parts, plain) {
  return (parts || []).map(function (p) {
    if (typeof p === 'string') return document.createTextNode(p);
    if (p.term && !plain) return term(p.term, p.t);
    if (p.href && !plain) return h('a', { href: p.href, text: p.t });
    return document.createTextNode(p.t);
  });
}

export function mountSummary(host, opts) {
  opts = opts || {};
  var state = { day: 'today', data: {}, poller: null };
  var toggle = opts.toggle === false ? null : h('div', { cls: 'toggle', role: 'group', 'aria-label': 'Which day' }, [
    h('button', { type: 'button', 'data-day': 'today', 'aria-pressed': 'true', text: 'Today so far' }),
    h('button', { type: 'button', 'data-day': 'yesterday', 'aria-pressed': 'false', text: 'Yesterday' })
  ]);
  var headline = h('p', { cls: 'sum-head' }), list = h('ul', { cls: 'sum-points' }), note = h('p', { cls: 'sum-note' });
  if (toggle && opts.toggleHost) opts.toggleHost.appendChild(toggle);
  host.appendChild(headline);
  host.appendChild(list);
  if (!opts.plain) host.appendChild(note);

  function render() {
    var d = state.data[state.day];
    clear(headline); clear(list); clear(note);
    if (!d) return;
    if (!d.ready) {
      headline.appendChild(document.createTextNode(state.day === 'today' ? 'The day has only just started. Check back in an hour or two.' : 'Not enough history for yesterday yet.'));
      return;
    }
    renderParts(d.headline, opts.plain).forEach(function (n) { headline.appendChild(n); });
    d.points.slice(0, opts.points || 9).forEach(function (p) {
      var li = h('li', { 'data-key': p.key });
      renderParts(p.parts, opts.plain).forEach(function (n) { li.appendChild(n); });
      list.appendChild(li);
    });
    note.textContent = 'Written from the dashboard’s data, ' + (d.day === 'today' ? 'up to ' + new Date(d.covered_to * 1000).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', hourCycle: 'h23', timeZone: 'Europe/London' }) : 'for the whole day') + '. Updated ' + ago(Date.now() / 1000 - d.generated) + '.';
    if (opts.onRender) opts.onRender(d);
  }
  function setDay(day) {
    state.day = day;
    if (toggle) Array.prototype.forEach.call(toggle.children, function (b) { b.setAttribute('aria-pressed', String(b.getAttribute('data-day') === day)); });
    if (state.data[day]) render();
    else fetchJSON('/api/summary?day=' + day).then(function (d) { state.data[day] = d; if (state.day === day) render(); }).catch(function () {});
  }
  if (toggle) toggle.addEventListener('click', function (e) { var b = e.target.closest('button'); if (b) setDay(b.getAttribute('data-day')); });
  state.poller = poll('/api/summary?day=today', { onData: function (d) { state.data.today = d; if (state.day === 'today') render(); }, interval: function () { return 300; } });
  return { render: render, data: function () { return state.data.today; } };
}
