/* The page frame shared by every page: colour theme, menus, search and the
   grid warning banner. Runs once, when gb.js is first imported. */

import { byId, clear, h } from './dom.js';
import { plural, when, resetChanged } from './format.js';
import { poll } from './net.js';
import { TERMS as GLOSSARY } from './glossary.js';

/* Colour theme. Light unless the viewer picks otherwise: dark, or whatever
   their device is set to. "theme" is what is showing, "choice" what they picked. */
var CHOICES = ['light', 'dark', 'system'];
export const state = { choice: 'light', theme: 'light', alerts: null };
try { state.choice = localStorage.getItem('gb-theme') || 'light'; } catch (error) { state.choice = 'light'; }
var urlTheme = location.search.match(/[?&]theme=(light|dark|system|auto)/);
if (urlTheme) state.choice = urlTheme[1] === 'auto' ? 'system' : urlTheme[1];  /* the Display view and its frames */
if (CHOICES.indexOf(state.choice) < 0) state.choice = 'light';
var deviceDark = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null;
var THEME_WORDS = { light: 'Light', dark: 'Dark', system: 'Device' };

function resolve() { return state.choice === 'dark' || (state.choice === 'system' && deviceDark && deviceDark.matches) ? 'dark' : 'light'; }
function applyTheme() {
  var before = state.theme;
  state.theme = resolve();
  document.documentElement.setAttribute('data-theme', state.theme);
  var icon = byId('theme-icon'), word = byId('theme-word'), button = byId('theme-btn');
  if (icon) {
    var source = document.querySelector('[data-theme-choice="' + state.choice + '"] svg');
    if (source) { clear(icon); icon.appendChild(source.cloneNode(true)); }
  }
  if (word) word.textContent = THEME_WORDS[state.choice];
  if (button) button.setAttribute('aria-label', 'Colour theme: ' + (state.choice === 'system' ? 'match my device' : state.choice) + '. Select to change.');
  Array.prototype.forEach.call(document.querySelectorAll('[data-theme-choice]'), function (b) {
    b.setAttribute('aria-checked', String(b.getAttribute('data-theme-choice') === state.choice));
  });
  return before !== state.theme;
}
function choose(choice) {
  state.choice = CHOICES.indexOf(choice) >= 0 ? choice : 'light';
  try { localStorage.setItem('gb-theme', state.choice); } catch (error) { /* storage unavailable */ }
  if (applyTheme()) themeChanged();
}
function themeChanged() {
  resetChanged();
  window.dispatchEvent(new CustomEvent('gb:theme', { detail: state.theme }));
}
export function isDark() { return state.theme === 'dark'; }

function setupTheme() {
  var button = byId('theme-btn'), menu = byId('theme-menu');
  if (button && menu) {
    var close = function () { menu.hidden = true; button.setAttribute('aria-expanded', 'false'); };
    var open = function () {
      menu.hidden = false; button.setAttribute('aria-expanded', 'true');
      var current = menu.querySelector('[aria-checked="true"]') || menu.querySelector('button');
      if (current) current.focus();
    };
    button.addEventListener('click', function () { if (menu.hidden) open(); else close(); });
    menu.addEventListener('keydown', function (ev) {
      var items = Array.prototype.slice.call(menu.querySelectorAll('button')), i = items.indexOf(document.activeElement);
      if (ev.key === 'Escape') { close(); button.focus(); }
      else if (ev.key === 'ArrowDown' || ev.key === 'ArrowRight') { ev.preventDefault(); items[(i + 1) % items.length].focus(); }
      else if (ev.key === 'ArrowUp' || ev.key === 'ArrowLeft') { ev.preventDefault(); items[(i + items.length - 1) % items.length].focus(); }
    });
    document.addEventListener('click', function (ev) { if (!menu.hidden && !ev.target.closest('.theme-wrap')) close(); });
    menu.addEventListener('focusout', function (ev) { if (!menu.hidden && ev.relatedTarget && !ev.relatedTarget.closest('.theme-wrap')) close(); });
    menu.addEventListener('click', function (ev) {
      var b = ev.target.closest('[data-theme-choice]');
      if (b) { choose(b.getAttribute('data-theme-choice')); close(); button.focus(); }
    });
  }
  var sheetSeg = document.querySelector('.sheet .theme-seg');
  if (sheetSeg) sheetSeg.addEventListener('click', function (ev) {
    var b = ev.target.closest('[data-theme-choice]');
    if (b) choose(b.getAttribute('data-theme-choice'));
  });
  if (deviceDark) {
    var onDevice = function () { if (state.choice === 'system' && applyTheme()) themeChanged(); };
    if (deviceDark.addEventListener) deviceDark.addEventListener('change', onDevice);
    else if (deviceDark.addListener) deviceDark.addListener(onDevice);
  }
}

/* ---------- page menus ---------- */

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

/* The phone menu: a sheet listing every page by group */
function setupSheet() {
  var sheet = byId('menu-sheet'), button = byId('menu-btn');
  if (!sheet || !button || !sheet.showModal) return;
  button.addEventListener('click', function () {
    sheet.showModal();
    var current = sheet.querySelector('[aria-current="page"]');
    if (current) current.focus();
  });
  sheet.addEventListener('click', function (ev) {
    if (ev.target === sheet || ev.target.closest('[data-close]')) sheet.close();
  });
  sheet.addEventListener('close', function () { button.focus(); });
}

/* Skip link: focus whichever main region is showing */
function setupSkip() {
  var skip = byId('skip');
  if (!skip) return;
  skip.addEventListener('click', function (ev) {
    var main = Array.prototype.filter.call(document.querySelectorAll('main'), function (m) { return !m.hidden; })[0];
    if (!main) return;
    ev.preventDefault();
    main.setAttribute('tabindex', '-1');
    main.focus();
  });
}

/* ---------- search: pages and glossary terms ---------- */

var palette = { items: null, results: [], active: 0 };

function paletteItems() {
  if (palette.items) return palette.items;
  var items = [];
  Array.prototype.forEach.call(document.querySelectorAll('.nav a'), function (a) {
    items.push({ kind: 'Page', title: a.textContent, text: a.getAttribute('data-desc') || '', href: a.getAttribute('href') });
  });
  items.push({ kind: 'Page', title: 'Display', text: 'Full-screen slides for a spare screen or tablet', href: '/display' });
  Object.keys(GLOSSARY).forEach(function (key) {
    var t = GLOSSARY[key];
    items.push({ kind: 'Term', title: t.name, text: t.text, href: '/glossary#' + key, extra: t.phrases.join(' ') });
  });
  palette.items = items;
  return items;
}

function score(item, words) {
  var title = item.title.toLowerCase(), all = (item.title + ' ' + item.text + ' ' + (item.extra || '')).toLowerCase(), total = 0;
  for (var i = 0; i < words.length; i++) {
    var w = words[i];
    if (title.indexOf(w) === 0) total += 30;
    else if (new RegExp('\\b' + w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).test(title)) total += 20;
    else if (title.indexOf(w) >= 0) total += 12;
    else if (all.indexOf(w) >= 0) total += 4;
    else return 0;
  }
  return total + (item.kind === 'Page' ? 3 : 0);
}

function renderPalette() {
  var q = byId('palette-q'), list = clear(byId('palette-list'));
  var words = q.value.toLowerCase().trim().split(/\s+/).filter(Boolean);
  var items = paletteItems();
  palette.results = words.length
    ? items.map(function (it) { return [score(it, words), it]; }).filter(function (x) { return x[0] > 0; })
      .sort(function (a, b) { return b[0] - a[0]; }).slice(0, 12).map(function (x) { return x[1]; })
    : items.filter(function (it) { return it.kind === 'Page'; });
  palette.active = Math.min(palette.active, Math.max(0, palette.results.length - 1));
  if (!palette.results.length) {
    list.appendChild(h('li', { cls: 'palette-none', text: 'Nothing matches “' + q.value.trim() + '”.' }));
    q.removeAttribute('aria-activedescendant');
    return;
  }
  palette.results.forEach(function (it, i) {
    var li = h('li', { id: 'pal-' + i, role: 'option', 'aria-selected': String(i === palette.active), cls: 'palette-item' }, [
      h('span', { cls: 'pk', text: it.kind }),
      h('span', { cls: 'pt' }, [h('b', { text: it.title }), h('span', { text: it.text.length > 110 ? it.text.slice(0, 108).replace(/\s+\S*$/, '') + '…' : it.text })])
    ]);
    li.addEventListener('click', function () { go(it); });
    li.addEventListener('pointermove', function () { if (palette.active !== i) { palette.active = i; mark(); } });
    list.appendChild(li);
  });
  mark();
}
function mark() {
  var q = byId('palette-q');
  Array.prototype.forEach.call(byId('palette-list').children, function (li, i) { li.setAttribute('aria-selected', String(i === palette.active)); });
  var el = byId('pal-' + palette.active);
  if (el) { q.setAttribute('aria-activedescendant', el.id); el.scrollIntoView({ block: 'nearest' }); }
}
function go(it) { location.href = it.href; }

function setupPalette() {
  var dialog = byId('palette'), q = byId('palette-q'), button = byId('search-btn');
  if (!dialog || !q || !dialog.showModal) { if (button) button.hidden = true; return; }
  function open() {
    if (dialog.open) return;
    var sheet = byId('menu-sheet');
    if (sheet && sheet.open) sheet.close();
    q.value = ''; palette.active = 0;
    renderPalette();
    dialog.showModal();
    q.focus();
  }
  if (button) button.addEventListener('click', open);
  document.addEventListener('keydown', function (ev) {
    var typing = /^(INPUT|TEXTAREA|SELECT)$/.test(ev.target.tagName) || ev.target.isContentEditable;
    if ((ev.key === 'k' || ev.key === 'K') && (ev.metaKey || ev.ctrlKey)) { ev.preventDefault(); open(); }
    else if (ev.key === '/' && !typing && !ev.metaKey && !ev.ctrlKey && !ev.altKey) { ev.preventDefault(); open(); }
  });
  q.addEventListener('input', function () { palette.active = 0; renderPalette(); });
  q.addEventListener('keydown', function (ev) {
    var n = palette.results.length;
    if (ev.key === 'ArrowDown' && n) { ev.preventDefault(); palette.active = (palette.active + 1) % n; mark(); }
    else if (ev.key === 'ArrowUp' && n) { ev.preventDefault(); palette.active = (palette.active + n - 1) % n; mark(); }
    else if (ev.key === 'Enter' && n) { ev.preventDefault(); go(palette.results[palette.active]); }
    else if (ev.key === 'Escape') { ev.preventDefault(); dialog.close(); }
  });
  dialog.addEventListener('click', function (ev) { if (ev.target === dialog) dialog.close(); });
  var sheetSearch = document.querySelector('[data-open-search]');
  if (sheetSearch) sheetSearch.addEventListener('click', open);
}

/* ---------- grid warning banner ---------- */

var ALERT_ICON = '<svg width="18" height="18" viewBox="0 0 20 20" aria-hidden="true"><path d="M10 2.5 18.5 17h-17z" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/><path d="M10 8v4.2" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/><circle cx="10" cy="14.6" r="1.05" fill="currentColor"/></svg>';
var dismissed = {};
try { dismissed = JSON.parse(sessionStorage.getItem('gb-grid-alerts') || '{}') || {}; } catch (error) { dismissed = {}; }

function renderAlerts(data) {
  var bar = byId('alert-bar');
  if (!bar) return;
  var items = ((data && data.active) || []).filter(function (a) { return !dismissed[a.id]; });
  state.alerts = data;
  window.dispatchEvent(new CustomEvent('gb:alerts', { detail: data }));
  if (!items.length) { bar.hidden = true; clear(bar); return; }
  var lead = items[0];
  var signature = JSON.stringify(items.map(function (a) { return [a.id, a.summary]; }));
  if (bar.getAttribute('data-sig') === signature && !bar.hidden) return;
  bar.setAttribute('data-sig', signature);
  bar.className = 'alert-bar' + (lead.level === 'strong' ? ' strong' : '');
  var inner = h('div', { cls: 'wrap alert-inner' });
  var icon = h('span');
  icon.innerHTML = ALERT_ICON;
  inner.appendChild(icon);
  var more = items.length > 1 ? ' ' + plural(items.length - 1, 'other notice is', 'other notices are') + ' in force.' : '';
  inner.appendChild(h('p', null, [h('strong', { text: lead.title + '.' }), ' ' + lead.summary + more]));
  var full = items.map(function (a) {
    return h('p', { cls: 'alert-full', hidden: true, text: a.title + ', published ' + when(a.published) + ':\n' + a.text });
  });
  var buttons = h('div', { cls: 'alert-actions' });
  var toggle = h('button', { type: 'button', cls: 'alert-more', 'aria-expanded': 'false', text: 'Details', onclick: function () {
    var open = toggle.getAttribute('aria-expanded') !== 'true';
    toggle.setAttribute('aria-expanded', String(open));
    toggle.textContent = open ? 'Less' : 'Details';
    full.forEach(function (node) { node.hidden = !open; });
  } });
  buttons.appendChild(toggle);
  buttons.appendChild(h('button', { type: 'button', cls: 'alert-more', text: 'Hide', 'aria-label': 'Hide this notice for now', onclick: function () {
    items.forEach(function (a) { dismissed[a.id] = 1; });
    try { sessionStorage.setItem('gb-grid-alerts', JSON.stringify(dismissed)); } catch (error) { /* ignore */ }
    bar.hidden = true;
  } }));
  inner.appendChild(buttons);
  full.forEach(function (node) { inner.appendChild(node); });
  clear(bar);
  bar.appendChild(inner);
  bar.hidden = false;
  window.dispatchEvent(new Event('resize'));
}

/* ---------- start ---------- */

function start() {
  applyTheme();
  setupTheme();
  setupNav();
  setupSheet();
  setupSkip();
  setupPalette();
  Array.prototype.forEach.call(document.querySelectorAll('.year'), function (node) { node.textContent = String(new Date().getFullYear()); });
  if (byId('alert-bar')) poll('/api/alerts', { onData: renderAlerts, interval: function () { return 60; } });

  /* Debounced width changes for pages that redraw on resize */
  var lastWidth = document.documentElement.clientWidth, timer = null;
  window.addEventListener('resize', function () {
    clearTimeout(timer);
    timer = setTimeout(function () {
      var width = document.documentElement.clientWidth;
      if (Math.abs(width - lastWidth) < 8) return;
      lastWidth = width;
      resetChanged();
      window.dispatchEvent(new CustomEvent('gb:width', { detail: width }));
    }, 160);
  });
}
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
else start();
