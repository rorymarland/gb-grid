(function () {
  'use strict';
  var G = window.GB, h = G.h, byId = G.byId, clear = G.clear, num = G.num;
  var params = new URLSearchParams(location.search);
  var VIEWS = {
    now: { name: 'Right now' },
    map: { name: 'Power stations', src: '/map' },
    regions: { name: 'Regions', src: '/regions' },
    balancing: { name: 'Balancing', src: '/balancing' },
    outages: { name: 'Outages', src: '/outages' },
    history: { name: 'History', src: '/history' }
  };
  var order = (params.get('views') || 'now,map,regions,balancing').split(',').filter(function (k) { return VIEWS[k]; });
  if (!order.length) order = ['now'];
  var every = G.clamp(Number(params.get('every')) || 20, 5, 600);
  var nightSpec = (params.get('night') || '23-7').match(/^(\d{1,2})-(\d{1,2})$/);
  var nightFrom = nightSpec ? Number(nightSpec[1]) : null, nightTo = nightSpec ? Number(nightSpec[2]) : null;
  var theme = params.get('theme') || (G.theme === 'auto' ? '' : G.theme);
  var state = { index: 0, paused: false, started: Date.now(), live: null, alerts: null, peekUntil: 0, wake: null };
  var NAMES = { nuclear: 'Nuclear', wind: 'Wind', solar: 'Solar', hydro: 'Hydro', biomass: 'Biomass', imports: 'Imports', storage: 'Storage', gas: 'Gas', coal: 'Coal', other: 'Other' };
  var CI_LEVEL = { 'very low': '--ci-very-low', low: '--ci-low', moderate: '--ci-moderate', high: '--ci-high', 'very high': '--ci-very-high' };
  var clockFmt = new Intl.DateTimeFormat('en-GB', { weekday: 'long', day: 'numeric', month: 'long', timeZone: G.TZ });
  var hourFmt = new Intl.DateTimeFormat('en-GB', { hour: 'numeric', hourCycle: 'h23', timeZone: G.TZ });

  /* ---------- slides ---------- */

  var slides = byId('slides'), catcher = byId('catcher');
  order.forEach(function (key) {
    if (key === 'now') return;
    var src = VIEWS[key].src + '?display' + (theme ? '&theme=' + theme : '');
    var slide = h('section', { cls: 'slide', 'data-view': key, 'aria-label': VIEWS[key].name });
    slide.setAttribute('data-src', src);
    slides.insertBefore(slide, catcher);
  });
  var nowSlide = slides.querySelector('[data-view="now"]');
  if (order.indexOf('now') < 0) nowSlide.remove();
  var dots = byId('dots');
  order.forEach(function () { dots.appendChild(h('i')); });

  function slideFor(key) { return slides.querySelector('[data-view="' + key + '"]'); }
  function ensureFrame(key) {
    var slide = slideFor(key);
    if (!slide || slide.querySelector('iframe') || !slide.getAttribute('data-src')) return;
    slide.appendChild(h('iframe', { src: slide.getAttribute('data-src'), title: VIEWS[key].name, tabindex: '-1', loading: 'eager' }));
  }

  function show(index) {
    state.index = (index + order.length) % order.length;
    state.started = Date.now();
    var key = order[state.index];
    ensureFrame(key);
    ensureFrame(order[(state.index + 1) % order.length]);  // warm the next one so it's ready
    Array.prototype.forEach.call(slides.querySelectorAll('.slide'), function (s) { s.classList.toggle('on', s.getAttribute('data-view') === key); });
    Array.prototype.forEach.call(dots.children, function (d, i) { d.classList.toggle('on', i === state.index); });
    byId('view-name').textContent = VIEWS[key].name;
    renderState();
  }
  function step(delta) { show(state.index + delta); }

  /* ---------- the "now" slide ---------- */

  function inkFor(name) {
    var hex = G.css(name).replace('#', '');
    if (hex.length !== 6) return 'ink-light';
    var c = [0, 2, 4].map(function (i) { var v = parseInt(hex.substr(i, 2), 16) / 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); });
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2] > 0.3 ? 'ink-dark' : 'ink-light';
  }

  function cleanestWindow(forecast, hours) {
    var n = hours * 2, now = Date.now() / 1000, best = null;
    var points = (forecast || []).filter(function (p) { return p[0] >= now - 1800 && p[0] <= now + 86400; });
    for (var i = 0; i + n <= points.length; i++) {
      var sum = 0;
      for (var j = i; j < i + n; j++) sum += points[j][1];
      if (!best || sum / n < best.avg) best = { start: points[i][0], end: points[i + n - 1][0] + 1800, avg: sum / n };
    }
    return best;
  }

  function fact(colourVar, value, unit, label) {
    return h('div', { cls: 'fact' }, [
      h('i', { style: colourVar ? 'background:' + G.css(colourVar) : 'visibility:hidden' }),
      h('span', { cls: 'v' }, [value, unit ? h('small', { text: unit }) : null]),
      h('span', { cls: 'k', text: label })
    ]);
  }

  function renderNow() {
    var live = state.live;
    if (!live || !nowSlide.isConnected) return;
    var demand = byId('demand');
    clear(demand);
    G.add(demand, [num(live.demand_gw) ? live.demand_gw.toFixed(1) : '–', h('small', { text: 'GW' })]);
    var sources = (live.sources || []).filter(function (s) { return s.gw > 0; });
    var top = sources.slice().sort(function (a, b) { return b.gw - a.gw; })[0];
    var groups = live.groups || {};
    byId('lead-note').textContent = top ? (top.label + ' is the biggest source at ' + Math.round(top.pct) + '%. ' + Math.round(groups.low_carbon || 0) + '% is low-carbon.') : '';

    var facts = clear(byId('facts')), c = live.carbon || {}, p = live.price || {};
    if (num(c.value)) facts.appendChild(fact(CI_LEVEL[c.index], String(c.value), 'g/kWh', 'Carbon intensity, ' + (c.index || '') ));
    if (num(p.value)) facts.appendChild(fact(null, '£' + p.value.toFixed(0), '/MWh', 'Wholesale price, ' + (100 * p.value / 1000).toFixed(1) + 'p per kWh'));
    var wind = sources.filter(function (s) { return s.key === 'wind'; })[0], solar = sources.filter(function (s) { return s.key === 'solar'; })[0];
    if (wind) facts.appendChild(fact('--c-wind', wind.gw.toFixed(1), 'GW', 'Wind' + (solar && solar.gw >= 0.5 ? ', with ' + solar.gw.toFixed(1) + ' GW of solar' : '')));

    var mix = clear(byId('mix')), total = sources.reduce(function (a, s) { return a + s.gw; }, 0) || 1;
    var mixOrder = ['nuclear', 'wind', 'solar', 'hydro', 'biomass', 'imports', 'storage', 'coal', 'gas', 'other'];
    var byKey = {}, width = mix.clientWidth || 600, size = parseFloat(getComputedStyle(mix).fontSize) || 16;
    var padding = 12 + window.innerWidth * 0.01 + 4;
    sources.forEach(function (s) { byKey[s.key] = s; });
    mix.setAttribute('aria-label', 'Generation mix: ' + sources.map(function (s) { return s.label + ' ' + Math.round(s.pct) + '%'; }).join(', '));
    mixOrder.forEach(function (k) {
      var s = byKey[k];
      if (!s) return;
      var share = s.gw / total, cls = inkFor('--c-' + k), pct = Math.round(share * 100) + '%';
      var room = share * width - padding, name = NAMES[k] || s.label, label = null;
      if (G.textWidth(name + ' ' + pct, size, 650) <= room) label = [name, h('em', { text: pct })];
      else if (G.textWidth(pct, size, 650) <= room) label = [pct];
      mix.appendChild(h('span', { cls: cls, style: 'flex:' + share.toFixed(4) + ' 1 0;background:' + G.css('--c-' + k) }, label));
    });

    var later = clear(byId('later')), best = cleanestWindow(live.forecast, 3);
    if (best) later.appendChild(h('span', null, ['Cleanest 3 hours: ', h('b', { text: G.when(best.start).replace('Tomorrow', 'tomorrow') + ' to ' + G.hm(best.end) }), ', about ' + Math.round(best.avg) + ' g/kWh']));
    var m = state.alerts && state.alerts.margin;
    if (m) later.appendChild(h('span', null, ['Spare capacity: ', h('b', { text: G.gw(m.margin) + ' GW' }), ', ' + m.label.toLowerCase() + ' over the next 12 hours']));

    var notice = clear(byId('notice')), active = (state.alerts && state.alerts.active) || [];
    if (active.length) {
      var lead = active[0];
      notice.appendChild(h('div', { cls: 'notice' + (lead.level === 'strong' ? ' strong' : '') }, [h('b', { text: lead.title + '. ' }), lead.summary]));
    }
    renderNight();
  }

  /* ---------- clock, night and state ---------- */

  function isNight() {
    if (nightFrom === null || nightFrom === nightTo) return false;
    var hour = Number(hourFmt.format(new Date()));
    return nightFrom > nightTo ? (hour >= nightFrom || hour < nightTo) : (hour >= nightFrom && hour < nightTo);
  }
  function renderNight() {
    var live = state.live;
    byId('night-ci').textContent = live && live.carbon && num(live.carbon.value) ? live.carbon.value + ' g/kWh' : '';
    byId('night-demand').textContent = live && num(live.demand_gw) ? live.demand_gw.toFixed(1) + ' GW' : '';
  }
  function renderClock() {
    var now = Date.now() / 1000, clock = byId('clock');
    clear(clock);
    G.add(clock, [G.hm(now), h('small', { text: clockFmt.format(new Date()) })]);
    byId('night-clock').textContent = G.hm(now);
    document.body.classList.toggle('is-night', isNight() && Date.now() > state.peekUntil);
  }
  function renderState() {
    var text = byId('state');
    clear(text);
    if (state.paused) text.appendChild(h('span', { cls: 'paused', text: 'Paused. Tap to carry on.' }));
    else if (state.offline) text.appendChild(h('span', { text: 'Reconnecting…' }));
  }

  function tick() {
    renderClock();
    var bar = byId('progress');
    if (order.length < 2 || document.body.classList.contains('is-night')) { bar.style.width = '0'; return; }
    if (state.paused) { state.started = Date.now() - (state.pausedAt || 0); return; }
    var elapsed = (Date.now() - state.started) / 1000;
    bar.style.width = Math.min(100, 100 * elapsed / every).toFixed(2) + '%';
    if (elapsed >= every) step(1);
  }

  function togglePause() {
    state.paused = !state.paused;
    state.pausedAt = state.paused ? Date.now() - state.started : 0;
    renderState();
  }

  /* Taps pause; swipes and arrow keys move between views */
  var down = null;
  catcher.addEventListener('pointerdown', function (e) { down = { x: e.clientX, y: e.clientY, t: Date.now() }; });
  catcher.addEventListener('pointerup', function (e) {
    if (!down) return;
    var dx = e.clientX - down.x, dy = e.clientY - down.y;
    down = null;
    if (Math.abs(dx) > 60 && Math.abs(dx) > Math.abs(dy)) step(dx < 0 ? 1 : -1);
    else if (Math.abs(dx) < 12 && Math.abs(dy) < 12) togglePause();
  });
  byId('night').addEventListener('click', function () { state.peekUntil = Date.now() + 120000; renderClock(); });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'ArrowRight') step(1);
    else if (e.key === 'ArrowLeft') step(-1);
    else if (e.key === ' ') { e.preventDefault(); togglePause(); }
  });

  /* Full screen where the browser allows it, and keep the screen awake */
  var root = document.documentElement, fsBtn = byId('fs-btn');
  if (root.requestFullscreen && !window.matchMedia('(display-mode: fullscreen), (display-mode: standalone)').matches) {
    fsBtn.hidden = false;
    fsBtn.addEventListener('click', function () {
      if (document.fullscreenElement) document.exitFullscreen(); else root.requestFullscreen().catch(function () {});
    });
    document.addEventListener('fullscreenchange', function () { fsBtn.textContent = document.fullscreenElement ? 'Leave full screen' : 'Full screen'; });
  }
  function keepAwake() {
    if (!('wakeLock' in navigator) || document.hidden) return;
    navigator.wakeLock.request('screen').then(function (lock) { state.wake = lock; }).catch(function () {});
  }
  document.addEventListener('visibilitychange', function () { if (!document.hidden) keepAwake(); });
  keepAwake();

  window.addEventListener('gb:theme', renderNow);
  window.addEventListener('gb:width', renderNow);
  G.poll('/api/live', {
    onData: function (d) { state.live = d; renderNow(); },
    onTick: function (p) { var off = G.offline(p); if (off !== state.offline) { state.offline = off; renderState(); } },
    interval: function () { return 30; }
  });
  G.poll('/api/alerts', { onData: function (d) { state.alerts = d; renderNow(); }, interval: function () { return 60; } });
  // Reload once a day so a long-running screen picks up new versions of the pages
  setTimeout(function () { location.reload(); }, (20 + Math.random() * 8) * 3600 * 1000);

  show(0);
  renderClock();
  setInterval(tick, 250);
})();
