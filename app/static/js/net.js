/* Fetching, polling and the header status line */

import { byId } from './dom.js';

export function fetchText(url) {
  return fetch(url, { cache: 'no-cache', headers: { Accept: 'application/json' } }).then(function (response) {
    if (!response.ok) throw new Error('HTTP ' + response.status);
    return response.text();
  });
}
export function fetchJSON(url) { return fetchText(url).then(JSON.parse); }

/* Poll a JSON endpoint while the page is visible. The server answers
   unchanged data with 304, so polling is cheap. */
export function poll(url, options) {
  var state = { url: url, text: '', data: null, failures: 0, lastOk: 0, timer: null };
  function schedule() {
    clearTimeout(state.timer);
    if (document.hidden) return;
    var seconds = (options.interval && options.interval(state.data)) || 10;
    if (state.failures) seconds = Math.min(60, seconds * Math.pow(2, state.failures - 1));
    state.timer = setTimeout(tick, seconds * 1000);
  }
  function tick() {
    clearTimeout(state.timer);
    fetchText(state.url).then(function (text) {
      state.failures = 0;
      state.lastOk = Date.now();
      if (text !== state.text) {
        state.text = text;
        state.data = JSON.parse(text);
        options.onData(state.data);
      }
    }).catch(function (error) {
      state.failures += 1;
      if (options.onError) options.onError(error);
    }).then(function () {
      if (options.onTick) options.onTick(state);
      schedule();
    });
  }
  state.tick = tick;
  state.setUrl = function (next) { state.url = next; state.text = ''; tick(); };
  document.addEventListener('visibilitychange', function () { if (!document.hidden) tick(); });
  tick();
  return state;
}

/* The status line in the header */
export function setLive(kind, message) {
  var dot = byId('live-dot'), text = byId('live-text');
  if (!dot || !text) return;
  var cls = 'dot' + (kind ? ' ' + kind : '') + (kind === 'good' ? ' pulse' : '');
  if (dot.className !== cls) dot.className = cls;
  if (text.textContent !== message) text.textContent = message;
}
export function offline(poller) {
  return poller.failures > 0 && (!poller.lastOk || Date.now() - poller.lastOk > 25000);
}
