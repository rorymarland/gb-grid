(function () {
  'use strict';
  var G = window.GB, h = G.h, byId = G.byId, clear = G.clear, num = G.num;
  var ORDER = ['nuclear', 'wind', 'solar', 'hydro', 'biomass', 'imports', 'storage', 'coal', 'gas', 'other'];
  var NAMES = { nuclear: 'Nuclear', wind: 'Wind', solar: 'Solar', hydro: 'Hydro', biomass: 'Biomass', imports: 'Imports', storage: 'Storage', coal: 'Coal', gas: 'Gas', other: 'Other' };
  var DAY = 86400, START_2009 = 1230768000;
  var state = { data: null, summary: null, preset: '365', zoomed: false, mode: 'gw', plots: {}, loading: 0, poll: null };

  var fmt = function (opts) { return new Intl.DateTimeFormat('en-GB', Object.assign({ timeZone: G.TZ }, opts)); };
  var F = {
    hm: fmt({ hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }),
    dm: fmt({ day: 'numeric', month: 'short' }),
    my: fmt({ month: 'short', year: 'numeric' }),
    y: fmt({ year: 'numeric' }),
    full: fmt({ weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' })
  };
  function d(t) { return new Date(t * 1000); }
  function when(t, bucket) {
    if (bucket >= 7 * DAY) return 'Week of ' + G.date(t);
    if (bucket >= DAY) return F.full.format(d(t));
    if (bucket > 1800) return F.full.format(d(t)) + ', ' + F.hm.format(d(t)) + ' to ' + F.hm.format(d(t + bucket));
    return F.full.format(d(t)) + ' ' + F.hm.format(d(t));
  }
  function colour(key) { return G.css('--c-' + key); }
  function bodyFont(size, weight) { return (weight || 400) + ' ' + size + 'px ' + getComputedStyle(document.body).fontFamily; }

  /* ---------- data ---------- */

  function presetRange() {
    var ext = state.data && state.data.extent, end = ext && ext.last ? ext.last + 1800 : Math.floor(Date.now() / 1000);
    if (state.preset === 'all') return [ext && ext.first || START_2009, end];
    return [end - Number(state.preset) * DAY, end];
  }

  function load(from, to) {
    var ticket = ++state.loading;
    var url = '/api/longterm' + (num(from) ? '?from=' + Math.floor(from) + '&to=' + Math.ceil(to) : '');
    return G.fetchJSON(url).then(function (data) {
      if (ticket !== state.loading) return;
      state.data = data;
      render();
      updateHeader();
    }).catch(function () { if (ticket === state.loading) G.setLive('bad', 'Can’t reach the dashboard server. Retrying.'); });
  }

  function loadPreset() {
    state.zoomed = false;
    byId('zoom-reset').hidden = true;
    if (!state.data || !state.data.ready) return load();
    var r = presetRange();
    load(r[0], r[1]);
  }

  function zoomTo(from, to) {
    var ext = state.data.extent;
    from = Math.max(ext.first, from);
    to = Math.min(ext.last + 1800, to);
    if (to - from < 6 * 3600) { var mid = (from + to) / 2; from = mid - 3 * 3600; to = mid + 3 * 3600; }
    state.zoomed = true;
    byId('zoom-reset').hidden = false;
    load(from, to);
  }

  /* ---------- charts ---------- */

  function timeValues(u, splits, axisIdx, space, incr) {
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

  function axes(yValues, ySize) {
    var ink = G.css('--muted'), grid = G.css('--hair');
    return [
      { stroke: ink, font: bodyFont(11.5), grid: { show: false }, ticks: { stroke: grid, size: 4 }, values: timeValues, space: 70, gap: 4 },
      { stroke: ink, font: bodyFont(11.5), grid: { stroke: grid, width: 1 }, ticks: { show: false }, values: yValues, size: ySize || 44, gap: 6 }
    ];
  }

  function baseOptions(width, height, yValues, ySize) {
    return {
      width: width, height: height, legend: { show: false }, padding: [8, 18, 0, 0],
      tzDate: function (ts) { return uPlot.tzDate(new Date(ts * 1000), G.TZ); },
      cursor: {
        sync: { key: 'gb-history' }, points: { show: false },
        drag: { x: true, y: false, setScale: false },
        bind: { dblclick: function () { return function () { if (state.zoomed) loadPreset(); return null; }; } }
      },
      scales: { x: { time: true } },
      axes: axes(yValues, ySize),
      hooks: { setSelect: [onSelect], setCursor: [onCursor] }
    };
  }

  function onSelect(u) {
    if (u.select.width < 6) return;
    var from = u.posToVal(u.select.left, 'x'), to = u.posToVal(u.select.left + u.select.width, 'x');
    u.setSelect({ left: 0, width: 0, top: 0, height: 0 }, false);
    zoomTo(from, to);
  }

  function plotWidth(id) { return Math.max(260, byId(id).clientWidth); }
  function heights() { var narrow = window.innerWidth < 640; return { mix: narrow ? 220 : 300, ci: narrow ? 120 : 150, price: narrow ? 120 : 150 }; }

  function mixData(data) {
    var t = data.t, n = t.length, base = new Array(n).fill(0), rows = [t], totals = new Array(n).fill(0);
    ORDER.forEach(function (k) { var s = data.mix[k] || []; for (var i = 0; i < n; i++) totals[i] += Math.max(0, s[i] || 0); });
    ORDER.forEach(function (k) {
      var s = data.mix[k] || [], line = new Array(n);
      for (var i = 0; i < n; i++) {
        var v = Math.max(0, s[i] || 0);
        if (state.mode === 'share') v = totals[i] ? 100 * v / totals[i] : 0;
        base[i] += v;
        line[i] = data.mix.gas[i] === null ? null : base[i];
      }
      rows.push(line);
    });
    return { rows: rows, totals: totals };
  }

  function buildPlots() {
    Object.keys(state.plots).forEach(function (k) { state.plots[k].destroy(); });
    state.plots = {};
    var data = state.data, hs = heights();
    if (!data || !data.ready) return;
    var mixed = mixData(data);
    state.mixTotals = mixed.totals;

    var series = [{}], bands = [];
    ORDER.forEach(function (k, i) {
      var c = colour(k);
      series.push({ label: NAMES[k], stroke: c, width: 0, fill: c, points: { show: false } });
      if (i > 0) bands.push({ series: [i + 1, i], fill: c });
    });
    var mixOpts = baseOptions(plotWidth('plot-mix'), hs.mix, function (u, splits) {
      return splits.map(function (v) { return state.mode === 'share' ? v + '%' : String(Math.round(v / 1000)); });
    });
    mixOpts.series = series;
    mixOpts.bands = bands;
    mixOpts.scales.y = { range: function (u, lo, hi) { return state.mode === 'share' ? [0, 100] : [0, Math.max(10000, Math.ceil(hi / 5000) * 5000)]; } };
    // The bottom series fills to zero; the others fill only their band.
    mixOpts.series.forEach(function (s, i) { if (i > 1) s.fill = null; });
    state.plots.mix = new uPlot(mixOpts, mixed.rows, byId('plot-mix'));

    var ciOpts = baseOptions(plotWidth('plot-ci'), hs.ci, function (u, splits) { return splits.map(function (v) { return String(v); }); });
    ciOpts.series = [{}, { label: 'Carbon intensity', stroke: G.css('--line'), width: 1.5, points: { show: false } }];
    ciOpts.scales.y = { range: function (u, lo, hi) { return [0, Math.max(100, Math.ceil(hi / 50) * 50)]; } };
    state.plots.ci = new uPlot(ciOpts, [data.t, data.ci], byId('plot-ci'));

    var priceOpts = baseOptions(plotWidth('plot-price'), hs.price, function (u, splits) { return splits.map(function (v) { return '£' + v; }); });
    priceOpts.series = [{}, { label: 'Price', stroke: G.css('--c-imports'), width: 1.5, points: { show: false }, spanGaps: false }];
    priceOpts.scales.y = { range: function (u, lo, hi) { lo = Math.min(0, Math.floor((lo || 0) / 50) * 50); return [lo, Math.max(lo + 50, Math.ceil((hi || 100) / 50) * 50)]; } };
    state.plots.price = new uPlot(priceOpts, [data.t, data.price], byId('plot-price'));

    ['mix', 'ci', 'price'].forEach(function (k) {
      var host = byId('plot-' + k);
      if (!host.tip) host.tip = G.makeTip(host);
    });
  }

  function onCursor(u) {
    var idx = u.cursor.idx, data = state.data;
    var host = u.root.parentNode;
    ['mix', 'ci', 'price'].forEach(function (k) { var other = byId('plot-' + k); if (other.tip && other !== host) other.tip.hide(); });
    if (!host.tip) return;
    if (idx === null || idx === undefined || !data || u.cursor.left < 0) { host.tip.hide(); return; }
    var t = data.t[idx], title = when(t, data.bucket), rows = [], note = null;
    if (host.id === 'plot-mix') {
      var total = state.mixTotals[idx] || 0;
      ORDER.slice().reverse().forEach(function (k) {
        var v = data.mix[k][idx];
        if (!num(v) || v <= 0) return;
        rows.push({ color: colour(k), value: state.mode === 'share' ? (100 * v / (total || 1)).toFixed(1) + '%' : G.gw(v) + ' GW', label: NAMES[k] });
      });
      note = 'Total ' + G.gw(total) + ' GW' + (data.bucket > 1800 ? ', averaged' : '');
    } else if (host.id === 'plot-ci') {
      rows.push({ color: G.css('--line'), value: num(data.ci[idx]) ? data.ci[idx] + ' g/kWh' : 'No data', label: 'Carbon intensity' });
    } else {
      rows.push({ color: G.css('--c-imports'), value: num(data.price[idx]) ? '£' + data.price[idx].toFixed(2) : 'No data', label: 'per MWh' });
      if (!num(data.price[idx]) && t < 1514764800) note = 'Prices start in 2018.';
    }
    var o = u.over.getBoundingClientRect(), hb = host.getBoundingClientRect();
    host.tip.show(title, rows, o.left - hb.left + u.cursor.left, o.top - hb.top + u.cursor.top, note);
  }

  function resize() {
    var hs = heights();
    if (state.plots.mix) state.plots.mix.setSize({ width: plotWidth('plot-mix'), height: hs.mix });
    if (state.plots.ci) state.plots.ci.setSize({ width: plotWidth('plot-ci'), height: hs.ci });
    if (state.plots.price) state.plots.price.setSize({ width: plotWidth('plot-price'), height: hs.price });
  }

  function renderLegend() {
    var host = clear(byId('legend'));
    ORDER.slice().reverse().forEach(function (k) {
      host.appendChild(h('li', null, [h('span', { cls: 'sw', style: 'background:' + colour(k) }), NAMES[k]]));
    });
  }

  function renderLoading() {
    var data = state.data, box = byId('loading'), ready = data && data.ready;
    byId('stack').hidden = !ready;
    box.hidden = !!ready;
    if (ready) return;
    clear(box);
    if (data && data.enabled === false) {
      box.appendChild(h('div', { cls: 'loading' }, [h('b', { text: 'Long-term history is turned off.' }),
        h('span', { cls: 'sub', text: 'Set LONG_HISTORY=true to download NESO’s record of the grid since 2009 (about 20 MB).' })]));
      return;
    }
    box.appendChild(h('div', { cls: 'loading' }, [
      h('b', { text: 'Downloading the grid’s history from NESO…' }),
      h('span', { cls: 'sub', text: 'About 300,000 half hours since 2009, 10,000 at a time. This happens once and takes a few minutes; the page fills in when it’s done.' }),
      data && data.extent && data.extent.last ? h('span', { cls: 'sub', text: ' Downloaded up to ' + G.date(data.extent.last) + '.' }) : null,
      h('div', { cls: 'progress' }, h('i', { style: 'width:' + (100 * ((data && data.extent && data.extent.progress) || 0)).toFixed(1) + '%' }))
    ]));
  }

  function render() {
    renderLoading();
    var data = state.data;
    if (!data || !data.ready) return;
    renderLegend();
    buildPlots();
    var bucket = data.bucket, label = bucket === 1800 ? 'half-hourly' : bucket === 6 * 3600 ? '6-hour averages' : bucket === DAY ? 'daily averages' : 'weekly averages';
    byId('range-note').textContent = G.date(Math.max(data.from, data.extent.first)) + ' to ' + G.date(data.to - 1) + ', ' + label + '.';
  }

  /* ---------- summary ---------- */

  function recordValue(r) {
    if (r.unit === 'MW') return [G.gw(r.value), 'GW'];
    if (r.unit === '%') return [r.value.toFixed(1), '%'];
    if (r.unit === '£/MWh') return [(r.value < 0 ? '−£' : '£') + Math.abs(r.value).toFixed(2), 'per MWh'];
    return [String(Math.round(r.value)), r.unit];
  }

  function renderSummary() {
    var sm = state.summary, host = clear(byId('records'));
    if (!sm || !sm.ready) { host.appendChild(h('p', { cls: 'empty', style: 'background:var(--surface);padding:14px 16px;margin:0', text: 'Records appear once the history has downloaded.' })); return; }
    sm.records.forEach(function (r) {
      var v = recordValue(r);
      host.appendChild(h('button', {
        cls: 'record', type: 'button', title: 'Show on the charts',
        onclick: function () { zoomTo(r.t - (r.span === 'day' ? 3 : 2) * DAY, r.t + (r.span === 'day' ? 4 : 2) * DAY); byId('chart-title').scrollIntoView({ behavior: 'smooth', block: 'start' }); }
      }, [
        h('span', { cls: 'k', text: r.title }),
        h('span', { cls: 'v' }, [v[0], h('small', { text: v[1] })]),
        h('span', { cls: 'd', text: r.span === 'day' ? F.full.format(d(r.t)) : F.full.format(d(r.t)) + ' ' + F.hm.format(d(r.t)) }),
        r.recent ? h('span', { cls: 'new', text: 'New this month' }) : null
      ]));
    });
    var coal = byId('coal');
    clear(coal);
    if (num(sm.last_coal) && Date.now() / 1000 - sm.last_coal > 7 * DAY) {
      var days = Math.floor((Date.now() / 1000 - sm.last_coal) / DAY);
      G.add(coal, ['Coal last generated on ', h('b', { text: G.date(sm.last_coal) }), '. Britain has been coal-free for ' + G.int(days) + ' days.']);
    }
    renderVersus();
    renderYears();
  }

  function bar(mix, label) {
    return h('div', { cls: 'bar100', role: 'img', 'aria-label': label + ': ' + ORDER.filter(function (k) { return mix[k] >= 0.005; }).map(function (k) { return NAMES[k] + ' ' + Math.round(mix[k] * 100) + '%'; }).join(', ') },
      ORDER.filter(function (k) { return mix[k] > 0.001; }).map(function (k) {
        return h('i', { style: 'flex:' + mix[k].toFixed(4) + ' 1 0;background:' + colour(k), title: NAMES[k] + ' ' + (mix[k] * 100).toFixed(1) + '%' });
      }));
  }

  function renderVersus() {
    var c = state.summary.compare, host = clear(byId('versus'));
    if (!c || !c.now) return;
    var now = c.now, then = c.then;
    var rows = h('div', { cls: 'versus' }, [
      h('div', { cls: 'vs-row' }, [h('span', { cls: 'lbl' }, [F.full.format(d(now.t)), h('small', { text: 'Latest full day' })]), bar(now.mix, 'Latest day')]),
      then ? h('div', { cls: 'vs-row' }, [h('span', { cls: 'lbl' }, [F.full.format(d(then.t)), h('small', { text: 'A year earlier' })]), bar(then.mix, 'A year earlier')]) : null
    ]);
    host.appendChild(rows);
    function stat(label, a, b, fmtv, lowerBetter, unit) {
      var change = null;
      if (num(a) && num(b) && b !== 0) {
        var diff = a - b, pct = 100 * diff / Math.abs(b), better = lowerBetter === null ? '' : (diff < 0) === lowerBetter ? 'better' : 'worse';
        change = h('div', { cls: 'c ' + (Math.abs(pct) < 1 ? '' : better), text: (diff < 0 ? '−' : '+') + Math.abs(pct).toFixed(0) + '% on a year earlier (' + fmtv(b) + ')' });
      }
      return h('div', { cls: 'vs-stat' }, [h('div', { cls: 'k', text: label }), h('div', { cls: 'v', text: num(a) ? fmtv(a) + (unit || '') : '–' }), change]);
    }
    var share = function (m) { return m ? (m.wind + m.solar + m.nuclear + m.hydro) : null; };
    host.appendChild(h('div', { cls: 'vs-stats' }, [
      stat('Carbon intensity', now.ci, then && then.ci, function (v) { return Math.round(v) + ' g'; }, true, '/kWh'),
      stat('Zero-carbon share', share(now.mix) * 100, then && share(then.mix) * 100, function (v) { return Math.round(v) + '%'; }, false),
      stat('Average price', now.price, then && then.price, function (v) { return '£' + v.toFixed(0); }, true, '/MWh')
    ]));
    byId('vs-sub').textContent = 'The latest full day in NESO’s record against the same weekday a year earlier.' + (then ? '' : ' There is no record for a year earlier yet.');
  }

  function renderYears() {
    var host = clear(byId('years'));
    var years = state.summary.years.slice().reverse();
    if (!years.length) return;
    host.appendChild(h('li', { cls: 'head' }, [h('span', { text: 'Year' }), h('span', { text: 'Share of generation' }), h('span', { cls: 'ci', text: 'g/kWh' })]));
    years.forEach(function (y) {
      host.appendChild(h('li', null, [
        h('span', { cls: 'y' }, [String(y.year), y.complete ? null : h('small', { text: 'so far' })]),
        bar(y.mix, String(y.year)),
        h('span', { cls: 'ci', text: num(y.ci) ? String(y.ci) : '–' })
      ]));
    });
  }

  /* ---------- page ---------- */

  function updateHeader() {
    var data = state.data;
    if (!data) { G.setLive('', 'Connecting to the dashboard server…'); return; }
    if (!data.ready) { G.setLive('warn', 'Downloading the history from NESO…'); return; }
    var ext = data.extent, text = 'History to ' + G.date(ext.last) + '.';
    if (num(ext.price_last) && ext.last - ext.price_last > 3 * DAY) text += ' Prices loaded to ' + G.date(ext.price_last) + ', still catching up.';
    G.setLive('good', text);
  }

  function setPressed(group, attr, value) {
    Array.prototype.forEach.call(byId(group).querySelectorAll('button'), function (b) { b.setAttribute('aria-pressed', String(b.getAttribute(attr) === value)); });
  }
  byId('ranges').addEventListener('click', function (e) {
    var b = e.target.closest('button'); if (!b) return;
    state.preset = b.getAttribute('data-range');
    setPressed('ranges', 'data-range', state.preset);
    loadPreset();
  });
  byId('mix-mode').addEventListener('click', function (e) {
    var b = e.target.closest('button'); if (!b) return;
    state.mode = b.getAttribute('data-mode');
    setPressed('mix-mode', 'data-mode', state.mode);
    byId('mix-unit').textContent = state.mode === 'share' ? 'Share of generation' : 'GW';
    if (state.data && state.data.ready) { var mixed = mixData(state.data); state.mixTotals = mixed.totals; state.plots.mix.setData(mixed.rows); }
  });
  byId('zoom-reset').addEventListener('click', loadPreset);
  window.addEventListener('gb:theme', function () { buildPlots(); renderLegend(); if (state.summary) renderSummary(); });
  window.addEventListener('gb:width', resize);

  // The summary poll also notices when the first download finishes.
  state.poll = G.poll('/api/longterm/summary', {
    onData: function (sm) {
      var wasReady = state.data && state.data.ready;
      state.summary = sm;
      renderSummary();
      if (!wasReady && sm.ready) { var r; state.data = { ready: true, extent: sm.extent }; r = presetRange(); load(r[0], r[1]); }
      else if (!sm.ready) { state.data = sm; render(); updateHeader(); }
    },
    onTick: function (p) { if (G.offline(p)) G.setLive('bad', 'Can’t reach the dashboard server. Retrying.'); else updateHeader(); },
    interval: function (sm) { return sm && sm.ready ? 600 : 15; }
  });
})();
