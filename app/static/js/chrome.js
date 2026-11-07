/* The page frame shared by every page: colour theme, page tabs and the grid
   warning banner. Runs once, when gb.js is first imported. */

import { byId, clear, h } from './dom.js';
import { plural, when, resetChanged } from './format.js';
import { poll } from './net.js';

var themes = ['auto', 'light', 'dark'];
/* Shared, changing page state: the colour theme and the latest grid notices */
export const state = { theme: 'auto', alerts: null };
try { state.theme = localStorage.getItem('gb-grid-theme') || 'auto'; } catch (error) { state.theme = 'auto'; }
var urlTheme = location.search.match(/[?&]theme=(light|dark|auto)/);
if (urlTheme) state.theme = urlTheme[1];  /* the Display view and its frames */
function applyTheme(name) {
  state.theme = themes.indexOf(name) >= 0 ? name : 'auto';
  if (state.theme === 'auto') document.documentElement.removeAttribute('data-theme');
  else document.documentElement.setAttribute('data-theme', state.theme);
  var button = byId('theme-btn');
  if (!button) return;
  clear(button);
  button.appendChild(h('span', { cls: 'theme-word', text: 'Theme: ' }));
  button.appendChild(document.createTextNode(state.theme));
  button.setAttribute('aria-label', 'Colour theme is ' + state.theme + '. Select to change.');
}
function themeChanged() {
  resetChanged();
  window.dispatchEvent(new CustomEvent('gb:theme', { detail: state.theme }));
}
export function isDark() {
  if (state.theme === 'dark') return true;
  if (state.theme === 'light') return false;
  return Boolean(window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches);
}

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
  applyTheme(state.theme);
  var button = byId('theme-btn');
  if (button) {
    button.addEventListener('click', function () {
      applyTheme(themes[(themes.indexOf(state.theme) + 1) % themes.length]);
      try { localStorage.setItem('gb-grid-theme', state.theme); } catch (error) { /* storage unavailable */ }
      themeChanged();
    });
  }
  if (window.matchMedia) {
    var scheme = window.matchMedia('(prefers-color-scheme: dark)');
    var onScheme = function () { if (state.theme === 'auto') themeChanged(); };
    if (scheme.addEventListener) scheme.addEventListener('change', onScheme);
    else if (scheme.addListener) scheme.addListener(onScheme);
  }
  setupNav();
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
