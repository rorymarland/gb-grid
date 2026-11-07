/* GB Grid: helpers shared by every page (window.GB), the theme switch, the
   page tabs and the grid warning banner. Plain JavaScript, no dependencies. */
(function () {
  'use strict';

  var GB = window.GB = window.GB || {};
  var NS = 'http://www.w3.org/2000/svg';
  GB.TZ = 'Europe/London';

  /* ---------- DOM ---------- */

  GB.byId = function (id) { return document.getElementById(id); };
  GB.clear = function (node) { while (node && node.firstChild) node.removeChild(node.firstChild); return node; };
  GB.add = function (node, kids) {
    if (kids === null || kids === undefined) return node;
    if (!Array.isArray(kids)) kids = [kids];
    kids.forEach(function (kid) {
      if (kid === null || kid === undefined || kid === false) return;
      node.appendChild(typeof kid === 'string' || typeof kid === 'number' ? document.createTextNode(String(kid)) : kid);
    });
    return node;
  };
  GB.h = function (tag, props, kids) {
    var node = document.createElement(tag);
    if (props) {
      Object.keys(props).forEach(function (key) {
        var value = props[key];
        if (value === null || value === undefined || value === false) return;
        if (key === 'text') node.textContent = value;
        else if (key === 'cls') node.className = value;
        else if (key.indexOf('on') === 0) node.addEventListener(key.slice(2), value);
        else node.setAttribute(key, value === true ? '' : value);
      });
    }
    return GB.add(node, kids);
  };
  GB.s = function (tag, attrs, kids) {
    var node = document.createElementNS(NS, tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (key) {
        var value = attrs[key];
        if (value === null || value === undefined) return;
        if (key === 'text') node.textContent = value;
        else if (key.indexOf('on') === 0 && typeof value === 'function') node.addEventListener(key.slice(2), value);
        else node.setAttribute(key, value);
      });
    }
    return GB.add(node, kids);
  };

  /* ---------- numbers and times (always UK time) ---------- */

  GB.num = function (v) { return v !== null && v !== undefined && !isNaN(v); };
  GB.clamp = function (v, lo, hi) { return Math.max(lo, Math.min(hi, v)); };
  var timeFmt = new Intl.DateTimeFormat('en-GB', { hour: '2-digit', minute: '2-digit', hourCycle: 'h23', timeZone: GB.TZ });
  var dayFmt = new Intl.DateTimeFormat('en-GB', { weekday: 'short', day: 'numeric', month: 'short', timeZone: GB.TZ });
  var dateFmt = new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', year: 'numeric', timeZone: GB.TZ });
  var keyFmt = new Intl.DateTimeFormat('en-CA', { year: 'numeric', month: '2-digit', day: '2-digit', timeZone: GB.TZ });
  var intFmt = new Intl.NumberFormat('en-GB', { maximumFractionDigits: 0 });
  GB.hm = function (t) { return timeFmt.format(new Date(t * 1000)); };
  GB.day = function (t) { return dayFmt.format(new Date(t * 1000)); };
  GB.date = function (t) { return dateFmt.format(new Date(t * 1000)); };
  GB.dayKey = function (t) { return keyFmt.format(new Date(t * 1000)); };
  GB.when = function (t, now) {
    /* "14:30", "Yesterday 14:30" or "Thu 5 Nov 14:30" relative to now */
    now = now || Date.now() / 1000;
    var key = GB.dayKey(t);
    if (key === GB.dayKey(now)) return GB.hm(t);
    if (key === GB.dayKey(now - 86400)) return 'Yesterday ' + GB.hm(t);
    if (key === GB.dayKey(now + 86400)) return 'Tomorrow ' + GB.hm(t);
    return GB.day(t) + ' ' + GB.hm(t);
  };
  GB.int = function (v) { return GB.num(v) ? intFmt.format(Math.round(v)) : '–'; };
  GB.mw = GB.int;
  GB.gw = function (vMw) {
    if (!GB.num(vMw)) return '–';
    var v = vMw / 1000;
    return Math.abs(v) >= 1 ? v.toFixed(1) : v.toFixed(2);
  };
  GB.money = function (pounds) {
    if (!GB.num(pounds)) return '–';
    var sign = pounds < 0 ? '−' : '', v = Math.abs(pounds);
    if (v >= 1e6) return sign + '£' + (v / 1e6).toFixed(v >= 1e7 ? 1 : 2) + 'm';
    if (v >= 1e4) return sign + '£' + Math.round(v / 1e3) + 'k';
    if (v >= 1e3) return sign + '£' + (v / 1e3).toFixed(1) + 'k';
    return sign + '£' + Math.round(v);
  };
  GB.ago = function (seconds) {
    seconds = Math.max(0, Math.round(seconds));
    if (seconds < 60) return seconds + ' s ago';
    if (seconds < 3600) return Math.round(seconds / 60) + ' min ago';
    if (seconds < 172800) return Math.round(seconds / 3600) + ' h ago';
    return Math.round(seconds / 86400) + ' days ago';
  };
  GB.plural = function (n, one, many) { return n + ' ' + (n === 1 ? one : many); };
  GB.niceStep = function (span, target) {
    var raw = span / Math.max(1, target);
    var magnitude = Math.pow(10, Math.floor(Math.log(raw) / Math.LN10));
    var norm = raw / magnitude;
    return (norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 2.5 ? 2.5 : norm <= 5 ? 5 : 10) * magnitude;
  };
  var measure = null;
  GB.textWidth = function (text, size, weight) {
    if (!measure) measure = document.createElement('canvas').getContext('2d');
    measure.font = (weight || 600) + ' ' + size + 'px ' + getComputedStyle(document.body).fontFamily;
    return measure.measureText(text).width;
  };
  GB.css = function (name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); };

  /* Re-render only when the inputs to a section change */
  GB.signatures = {};
  GB.changed = function (name, inputs) {
    var signature = JSON.stringify(inputs);
    if (GB.signatures[name] === signature) return false;
    GB.signatures[name] = signature;
    return true;
  };

  /* ---------- tooltips ---------- */

  GB.makeTip = function (container) {
    var tip = GB.h('div', { cls: 'tip', 'aria-hidden': 'true' });
    tip.hidden = true;
    container.appendChild(tip);
    return {
      node: tip,
      show: function (title, rows, x, y, note) {
        GB.clear(tip);
        tip.appendChild(GB.h('div', { cls: 't', text: title }));
        rows.forEach(function (row) {
          if (row.sep) { tip.appendChild(GB.h('div', { cls: 'sep' })); return; }
          tip.appendChild(GB.h('div', { cls: 'row' }, [
            GB.h('span', { cls: 'key ' + (row.cls || 'none'), style: row.color ? 'background:' + row.color : null }),
            GB.h('span', { cls: 'v', text: row.value }),
            GB.h('span', { cls: 'l', text: row.label })
          ]));
        });
        if (note) tip.appendChild(GB.h('div', { cls: 'note', text: note }));
        tip.hidden = false;
        var width = container.clientWidth, height = container.clientHeight;
        var tipWidth = tip.offsetWidth, tipHeight = tip.offsetHeight;
        var left = x + 18;
        if (left + tipWidth > width - 6) left = x - tipWidth - 18;
        var centred = GB.clamp(x - tipWidth / 2, 6, Math.max(6, width - tipWidth - 6));
        if (left < 6) left = centred;
        var top = GB.clamp(y - tipHeight / 2, 6, Math.max(6, height - tipHeight - 6));
        if (left === centred && Math.abs(top - y) < tipHeight) {
          top = y + 22 + tipHeight < height ? y + 22 : Math.max(6, y - tipHeight - 22);
        }
        tip.style.left = left + 'px';
        tip.style.top = top + 'px';
      },
      hide: function () { tip.hidden = true; }
    };
  };

  /* ---------- fetching ---------- */

  GB.fetchText = function (url) {
    return fetch(url, { cache: 'no-cache', headers: { Accept: 'application/json' } }).then(function (response) {
      if (!response.ok) throw new Error('HTTP ' + response.status);
      return response.text();
    });
  };
  GB.fetchJSON = function (url) { return GB.fetchText(url).then(JSON.parse); };

  /* Poll a JSON endpoint while the page is visible. The server answers
     unchanged data with 304, so polling is cheap. */
  GB.poll = function (url, options) {
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
      GB.fetchText(state.url).then(function (text) {
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
  };

  /* The status line in the header */
  GB.setLive = function (kind, message) {
    var dot = GB.byId('live-dot'), text = GB.byId('live-text');
    if (!dot || !text) return;
    var cls = 'dot' + (kind ? ' ' + kind : '') + (kind === 'good' ? ' pulse' : '');
    if (dot.className !== cls) dot.className = cls;
    if (text.textContent !== message) text.textContent = message;
  };
  GB.offline = function (poller) {
    return poller.failures > 0 && (!poller.lastOk || Date.now() - poller.lastOk > 25000);
  };

  /* ---------- theme ---------- */

  var themes = ['auto', 'light', 'dark'];
  GB.theme = 'auto';
  try { GB.theme = localStorage.getItem('gb-grid-theme') || 'auto'; } catch (error) { GB.theme = 'auto'; }
  function applyTheme(name) {
    GB.theme = themes.indexOf(name) >= 0 ? name : 'auto';
    if (GB.theme === 'auto') document.documentElement.removeAttribute('data-theme');
    else document.documentElement.setAttribute('data-theme', GB.theme);
    var button = GB.byId('theme-btn');
    if (!button) return;
    GB.clear(button);
    button.appendChild(GB.h('span', { cls: 'theme-word', text: 'Theme: ' }));
    button.appendChild(document.createTextNode(GB.theme));
    button.setAttribute('aria-label', 'Colour theme is ' + GB.theme + '. Select to change.');
  }
  function themeChanged() {
    GB.signatures = {};
    window.dispatchEvent(new CustomEvent('gb:theme', { detail: GB.theme }));
  }
  GB.isDark = function () {
    if (GB.theme === 'dark') return true;
    if (GB.theme === 'light') return false;
    return Boolean(window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches);
  };

  /* ---------- page tabs ---------- */

  function setupNav() {
    var nav = document.querySelector('.nav');
    if (!nav) return;
    function fades() {
      var max = nav.scrollWidth - nav.clientWidth;
      nav.classList.toggle('fade-l', max > 2 && nav.scrollLeft > 2);
      nav.classList.toggle('fade-r', max > 2 && nav.scrollLeft < max - 2);
    }
    var current = nav.querySelector('[aria-current="page"]');
    if (current && nav.scrollWidth > nav.clientWidth) {
      nav.scrollLeft = Math.max(0, current.offsetLeft - (nav.clientWidth - current.offsetWidth) / 2);
    }
    nav.addEventListener('scroll', fades, { passive: true });
    window.addEventListener('resize', fades);
    fades();
  }

  /* ---------- grid warning banner ---------- */

  var ALERT_ICON = '<svg width="18" height="18" viewBox="0 0 20 20" aria-hidden="true"><path d="M10 2.5 18.5 17h-17z" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/><path d="M10 8v4.2" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/><circle cx="10" cy="14.6" r="1.05" fill="currentColor"/></svg>';
  var dismissed = {};
  try { dismissed = JSON.parse(sessionStorage.getItem('gb-grid-alerts') || '{}') || {}; } catch (error) { dismissed = {}; }

  function renderAlerts(data) {
    var bar = GB.byId('alert-bar');
    if (!bar) return;
    var items = ((data && data.active) || []).filter(function (a) { return !dismissed[a.id]; });
    GB.alerts = data;
    window.dispatchEvent(new CustomEvent('gb:alerts', { detail: data }));
    if (!items.length) { bar.hidden = true; GB.clear(bar); return; }
    var lead = items[0];
    var signature = JSON.stringify(items.map(function (a) { return [a.id, a.summary]; }));
    if (bar.getAttribute('data-sig') === signature && !bar.hidden) return;
    bar.setAttribute('data-sig', signature);
    bar.className = 'alert-bar' + (lead.level === 'strong' ? ' strong' : '');
    var inner = GB.h('div', { cls: 'wrap alert-inner' });
    var icon = GB.h('span');
    icon.innerHTML = ALERT_ICON;
    inner.appendChild(icon);
    var more = items.length > 1 ? ' ' + GB.plural(items.length - 1, 'other notice is', 'other notices are') + ' in force.' : '';
    inner.appendChild(GB.h('p', null, [GB.h('strong', { text: lead.title + '.' }), ' ' + lead.summary + more]));
    var full = items.map(function (a) {
      return GB.h('p', { cls: 'alert-full', hidden: true, text: a.title + ', published ' + GB.when(a.published) + ':\n' + a.text });
    });
    var buttons = GB.h('div', { cls: 'alert-actions' });
    var toggle = GB.h('button', { type: 'button', cls: 'alert-more', 'aria-expanded': 'false', text: 'Details', onclick: function () {
      var open = toggle.getAttribute('aria-expanded') !== 'true';
      toggle.setAttribute('aria-expanded', String(open));
      toggle.textContent = open ? 'Less' : 'Details';
      full.forEach(function (node) { node.hidden = !open; });
    } });
    buttons.appendChild(toggle);
    buttons.appendChild(GB.h('button', { type: 'button', cls: 'alert-more', text: 'Hide', 'aria-label': 'Hide this notice for now', onclick: function () {
      items.forEach(function (a) { dismissed[a.id] = 1; });
      try { sessionStorage.setItem('gb-grid-alerts', JSON.stringify(dismissed)); } catch (error) { /* ignore */ }
      bar.hidden = true;
    } }));
    inner.appendChild(buttons);
    full.forEach(function (node) { inner.appendChild(node); });
    GB.clear(bar);
    bar.appendChild(inner);
    bar.hidden = false;
    window.dispatchEvent(new Event('resize'));
  }

  /* ---------- start ---------- */

  function start() {
    applyTheme(GB.theme);
    var button = GB.byId('theme-btn');
    if (button) {
      button.addEventListener('click', function () {
        applyTheme(themes[(themes.indexOf(GB.theme) + 1) % themes.length]);
        try { localStorage.setItem('gb-grid-theme', GB.theme); } catch (error) { /* storage unavailable */ }
        themeChanged();
      });
    }
    if (window.matchMedia) {
      var scheme = window.matchMedia('(prefers-color-scheme: dark)');
      var onScheme = function () { if (GB.theme === 'auto') themeChanged(); };
      if (scheme.addEventListener) scheme.addEventListener('change', onScheme);
      else if (scheme.addListener) scheme.addListener(onScheme);
    }
    setupNav();
    Array.prototype.forEach.call(document.querySelectorAll('.year'), function (node) { node.textContent = String(new Date().getFullYear()); });
    if (GB.byId('alert-bar')) GB.poll('/api/alerts', { onData: renderAlerts, interval: function () { return 60; } });

    /* Debounced width changes for pages that redraw on resize */
    var lastWidth = document.documentElement.clientWidth, timer = null;
    window.addEventListener('resize', function () {
      clearTimeout(timer);
      timer = setTimeout(function () {
        var width = document.documentElement.clientWidth;
        if (Math.abs(width - lastWidth) < 8) return;
        lastWidth = width;
        GB.signatures = {};
        window.dispatchEvent(new CustomEvent('gb:width', { detail: width }));
      }, 160);
    });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
