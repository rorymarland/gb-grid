/* The station map: Great Britain's outline, every power station as a circle
   sized by output, and the interconnectors with animated flows. Used by the
   Map page (interactive) and the Display view (static).

     var map = createStationMap({ stage, svg, interactive: true, ... });
     map.setData(apiMapPayload);

   Options, all optional except stage and svg:
     interactive  pan, zoom, hover and click (default false)
     scale        multiplies circle sizes
     maxLabels    station labels at the default zoom (14)
     focus        area to fit, in map km: { x0, x1, y0, y1 }
     visible(st)  whether to draw a station
     selected()   id of the selected station
     dimmed(st)   whether to fade a station
     sizeKey      element for the output key
     active()     false while the map is hidden
     onHover({ station } | { link } | null, point), onClick(same, point)
     onInteract() the person panned, zoomed or dragged
     onKey(e)     page keys first; return true if handled
     onMessage(text or null), onResize()
*/

import { s, clear } from './dom.js';
import { num, clamp, textWidth, gw } from './format.js';

/* Great Britain plus its offshore wind farms, in map km (y down) */
export const FOCUS = { x0: -430, x1: 340, y0: -1330, y1: -5 };
/* Where each interconnector's far-end label sits: dx, dy (px), anchor */
const LINK_LABEL = {
  INTFR: [9, 16, 'start'], INTELEC: [9, -10, 'start'], INTNEM: [8, 4, 'start'], INTNED: [8, 4, 'start'],
  INTIFA2: [0, 20, 'middle'], INTNSL: [8, 4, 'start'], INTVKL: [8, 4, 'start'], INTIRL: [-8, 4, 'end'],
  INTEW: [-8, 4, 'end'], INTGRNL: [-8, 4, 'end']
};
const LINK_BEND = { INTFR: -0.16, INTELEC: 0.16, INTNEM: 0.06, INTNED: -0.06 };

export function isIdle(st) { return Math.abs(st.mw) < 1; }
export function linkWords(link) {
  if (!num(link.mw) || Math.abs(link.mw) < 10) return 'idle';
  return gw(Math.abs(link.mw)) + ' GW ' + (link.mw > 0 ? 'in' : 'out');
}
function stationsOf(data) { return (data && data.stations) || []; }

export function createStationMap(opts) {
  var focus = opts.focus || FOCUS;

  var M = {
    ready: false, s: 1, tx: 0, ty: 0, W: 0, H: 0, minS: 0.1, maxS: 10, fitS: 1, moved: false,
    nodes: {}, order: [], screen: [], links: [], linkNodes: {}, hoverId: null, hoverLink: null, raf: 0, K: 0.42
  };

  function init() {
    M.stage = opts.stage;
    M.svg = opts.svg;
    M.gLand = s('g');
    M.gGeoLabels = s('g');
    M.gLinks = s('g');
    M.gStations = s('g');
    M.gLabels = s('g');
    M.gEnds = s('g');
    [M.gLand, M.gGeoLabels, M.gLinks, M.gStations, M.gLabels, M.gEnds].forEach(function (g) { M.svg.appendChild(g); });
    fetch('/geo.json').then(function (r) { return r.json(); }).then(function (geo) {
      M.geo = geo;
      var inner = s('g', { transform: 'scale(0.5)' });
      geo.units.forEach(function (unit) {
        inner.appendChild(s('path', { d: unit.d, 'class': unit.kind === 'gb' ? 'land' : 'land-near', 'fill-rule': 'evenodd' }));
      });
      M.gLand.appendChild(inner);
      M.geoLabels = geo.labels.map(function (label) {
        var node = s('text', { 'class': 'geo-label ' + (label.k === 'gb' ? 'gb' : label.k === 'sea' ? 'sea' : ''), text: label.t });
        M.gGeoLabels.appendChild(node);
        return { node: node, x: label.x, y: label.y };
      });
      M.ready = true;
      resize();
      draw();
    }).catch(function () {
      message('Couldn’t load the map outline. Reload the page to try again.');
    });
    if (!opts.interactive) {
      observe();
      return;
    }

    M.svg.addEventListener('wheel', onWheel, { passive: false });
    M.svg.addEventListener('pointerdown', onDown);
    M.svg.addEventListener('pointermove', onMove);
    M.svg.addEventListener('pointerup', onUp);
    M.svg.addEventListener('pointercancel', onUp);
    /* If a lift is never reported (Safari, when the element under the finger is redrawn), forget the finger */
    M.svg.addEventListener('lostpointercapture', function (e) { if (pointers[e.pointerId]) onUp(e); });
    M.svg.addEventListener('pointerleave', function (e) { if (e.pointerType === 'mouse' && !drag) setHover(null, null); });
    /* Mouse double-click zooms in; touch double-taps are handled in onDown and onUp */
    M.svg.addEventListener('dblclick', function (e) {
      e.preventDefault();
      if (Date.now() - lastTouch > 1000) zoomAt(pos(e), M.s * 2);
    });
    /* Safari's own pinch gestures would zoom the whole page instead of the map */
    ['gesturestart', 'gesturechange', 'gestureend'].forEach(function (type) {
      M.stage.addEventListener(type, function (e) { e.preventDefault(); });
    });
    M.stage.addEventListener('keydown', onKey);
    observe();
  }

  function observe() {
    var after = function () { resize(); if (opts.onResize) opts.onResize(); };
    if (window.ResizeObserver) new ResizeObserver(after).observe(M.stage);
    else window.addEventListener('resize', after);
  }

  function message(text) { if (opts.onMessage) opts.onMessage(text); }
  function interacted() { if (opts.onInteract) opts.onInteract(); }
  function active() { return !opts.active || opts.active(); }

  function resize() {
    if (!M.stage || !active()) return;
    var W = M.stage.clientWidth, H = M.stage.clientHeight;
    if (!W || !H) return;
    var first = !M.W;
    var centre = !first && M.moved ? [(M.W / 2 - M.tx) / M.s, (M.H / 2 - M.ty) / M.s] : null;
    M.W = W; M.H = H;
    M.K = 0.42 * (opts.scale || 1) * clamp(Math.min(W, H * 0.8) / 760, 0.72, 1.12);
    if (!M.geo) return;
    var box = M.geo.box;
    M.minS = Math.max(W / box[2], H / box[3]);
    if (first || !M.moved || !centre) fitDefault();
    else {
      M.s = clamp(M.s, M.minS, M.maxS);
      M.tx = W / 2 - centre[0] * M.s;
      M.ty = H / 2 - centre[1] * M.s;
      clampView();
    }
    requestRedraw();
  }
  function fitDefault() {
    var pad = 18;
    var sx = (M.W - 2 * pad) / (focus.x1 - focus.x0), sy = (M.H - 2 * pad) / (focus.y1 - focus.y0);
    M.s = M.fitS = Math.max(M.minS, Math.min(sx, sy));
    M.maxS = M.fitS * 10;
    M.tx = M.W / 2 - (focus.x0 + focus.x1) / 2 * M.s;
    M.ty = M.H / 2 - (focus.y0 + focus.y1) / 2 * M.s;
    clampView();
  }

  function clampView() {
    if (!M.geo) return;
    var b = M.geo.box, x0 = b[0], y0 = b[1], w = b[2] * M.s, hgt = b[3] * M.s;
    if (w <= M.W) M.tx = (M.W - w) / 2 - x0 * M.s;
    else M.tx = clamp(M.tx, M.W - (x0 * M.s + w), -x0 * M.s);
    if (hgt <= M.H) M.ty = (M.H - hgt) / 2 - y0 * M.s;
    else M.ty = clamp(M.ty, M.H - (y0 * M.s + hgt), -y0 * M.s);
  }

  function zoomAt(p, target) {
    var next = clamp(target, M.minS, M.maxS);
    var mx = (p[0] - M.tx) / M.s, my = (p[1] - M.ty) / M.s;
    M.s = next;
    M.tx = p[0] - mx * next;
    M.ty = p[1] - my * next;
    M.moved = true;
    clampView();
    requestRedraw();
  }

  function pos(e) {
    var rect = M.svg.getBoundingClientRect();
    return [e.clientX - rect.left, e.clientY - rect.top];
  }

  /* Touch: one finger pans; two fingers pinch to zoom; a double tap zooms in,
     and a double tap held and slid down or up zooms in or out around that
     point, as in Google and Apple Maps. Mouse: drag, wheel and double-click. */
  var pointers = {}, drag = null, pinch = null, tapZoom = null, lastTap = null, lastTouch = 0;
  var DOUBLE_TAP_MS = 320, DOUBLE_TAP_PX = 40;
  function onWheel(e) {
    e.preventDefault();
    interacted();
    var factor = Math.exp(-e.deltaY * (e.deltaMode === 1 ? 0.05 : 0.0016));
    zoomAt(pos(e), M.s * factor);
  }
  function startPinch() {
    var ids = Object.keys(pointers), a = pointers[ids[0]], b = pointers[ids[1]];
    var mid = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
    pinch = { d: Math.max(10, Math.hypot(a[0] - b[0], a[1] - b[1])), s: M.s, mx: (mid[0] - M.tx) / M.s, my: (mid[1] - M.ty) / M.s };
  }
  function onDown(e) {
    if (e.button && e.button !== 0) return;
    var p = pos(e), touch = e.pointerType !== 'mouse';
    if (touch) lastTouch = Date.now();
    // A first finger means nothing else is down: drop any finger whose lift went unreported
    if (e.isPrimary) { pointers = {}; pinch = null; tapZoom = null; drag = null; }
    pointers[e.pointerId] = p;
    try { M.svg.setPointerCapture(e.pointerId); } catch (error) { /* ignore */ }
    var ids = Object.keys(pointers);
    if (ids.length === 1) {
      if (touch && lastTap && Date.now() - lastTap.t < DOUBLE_TAP_MS && Math.hypot(p[0] - lastTap.p[0], p[1] - lastTap.p[1]) < DOUBLE_TAP_PX) {
        var at = lastTap.p;
        tapZoom = { y: p[1], s: M.s, at: at, mx: (at[0] - M.tx) / M.s, my: (at[1] - M.ty) / M.s, moved: false };
        lastTap = null;
        interacted();
      } else {
        drag = { start: p, last: p, moved: false, type: e.pointerType };
      }
    } else if (ids.length === 2) {
      tapZoom = null;
      startPinch();
      if (drag) drag.moved = true;
    }
  }
  function onMove(e) {
    var p = pos(e);
    if (!pointers[e.pointerId]) {
      if (e.pointerType === 'mouse') hoverAt(p);
      return;
    }
    pointers[e.pointerId] = p;
    var ids = Object.keys(pointers);
    if (tapZoom) {
      var dy = p[1] - tapZoom.y;
      if (!tapZoom.moved && Math.abs(dy) > 6) tapZoom.moved = true;
      if (tapZoom.moved) {
        // Slide down to zoom in, up to zoom out: doubling for every 150 px or so
        M.s = clamp(tapZoom.s * Math.exp(dy / 216), M.minS, M.maxS);
        M.tx = tapZoom.at[0] - tapZoom.mx * M.s;
        M.ty = tapZoom.at[1] - tapZoom.my * M.s;
        M.moved = true;
        clampView();
        requestRedraw();
      }
    } else if (ids.length >= 2 && pinch) {
      var a = pointers[ids[0]], b = pointers[ids[1]];
      var mid = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
      M.s = clamp(pinch.s * Math.hypot(a[0] - b[0], a[1] - b[1]) / pinch.d, M.minS, M.maxS);
      M.tx = mid[0] - pinch.mx * M.s;
      M.ty = mid[1] - pinch.my * M.s;
      M.moved = true;
      clampView();
      requestRedraw();
    } else if (drag && ids.length === 1) {
      if (!drag.moved && Math.hypot(p[0] - drag.start[0], p[1] - drag.start[1]) > 5) {
        drag.moved = true;
        M.stage.classList.add('dragging');
        interacted();
      }
      if (drag.moved) {
        M.tx += p[0] - drag.last[0];
        M.ty += p[1] - drag.last[1];
        M.moved = true;
        clampView();
        requestRedraw();
      }
      drag.last = p;
    }
  }
  function onUp(e) {
    var p = pos(e), touch = e.pointerType !== 'mouse', up = e.type === 'pointerup';
    var wasClick = drag && !drag.moved && up;
    if (tapZoom && up && !tapZoom.moved) zoomAt(tapZoom.at, M.s * 2);   // a plain double tap
    delete pointers[e.pointerId];
    var ids = Object.keys(pointers);
    if (ids.length < 2) pinch = null;
    if (ids.length === 2) startPinch();
    if (ids.length === 1) {
      // Back to one finger after a pinch: carry on panning from where that finger is now
      var rest = pointers[ids[0]];
      drag = { start: rest, last: rest, moved: true, type: e.pointerType };
    }
    if (!ids.length) {
      M.stage.classList.remove('dragging');
      if (wasClick) {
        clickAt(p);
        if (touch) lastTap = { t: Date.now(), p: p };
      } else if (touch) lastTap = null;
      drag = null;
      tapZoom = null;
    }
  }
  function onKey(e) {
    if (e.target !== M.stage) return;
    if (opts.onKey && opts.onKey(e)) return;
    var step = 70, handled = true;
    if (e.key === 'ArrowLeft') M.tx += step;
    else if (e.key === 'ArrowRight') M.tx -= step;
    else if (e.key === 'ArrowUp') M.ty += step;
    else if (e.key === 'ArrowDown') M.ty -= step;
    else if (e.key === '+' || e.key === '=') { zoomAt([M.W / 2, M.H / 2], M.s * 1.5); return e.preventDefault(); }
    else if (e.key === '-' || e.key === '_') { zoomAt([M.W / 2, M.H / 2], M.s / 1.5); return e.preventDefault(); }
    else if (e.key === '0') { M.moved = false; fitDefault(); requestRedraw(); return e.preventDefault(); }
    else handled = false;
    if (handled) { e.preventDefault(); interacted(); M.moved = true; clampView(); requestRedraw(); }
  }

  function hitStation(p) {
    var best = null;
    M.screen.forEach(function (item) {
      var d = Math.hypot(item.sx - p[0], item.sy - p[1]);
      var reach = Math.max(item.r + 2, 8);
      if (d <= reach && (!best || item.r < best.r || (item.r === best.r && d < best.d))) best = { id: item.id, r: item.r, d: d, item: item };
    });
    return best;
  }
  function hitLink(p) {
    var best = null;
    M.links.forEach(function (link) {
      if (!link.pts) return;
      for (var i = 1; i < link.pts.length; i++) {
        var a = link.pts[i - 1], b = link.pts[i];
        var dx = b[0] - a[0], dy = b[1] - a[1];
        var t = clamp(((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (dx * dx + dy * dy || 1), 0, 1);
        var d = Math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy));
        if (d < 7 && (!best || d < best.d)) best = { link: link, d: d };
      }
    });
    return best && best.link;
  }
  function hoverAt(p) {
    var hit = hitStation(p);
    if (hit) { setHover(hit.id, null); if (opts.onHover) opts.onHover({ station: hit.item.st }, p); return; }
    var link = hitLink(p);
    if (link) { setHover(null, link.code); if (opts.onHover) opts.onHover({ link: link }, p); return; }
    setHover(null, null);
  }
  function clickAt(p) {
    var hit = hitStation(p);
    var link = hit ? null : hitLink(p);
    if (link) setHover(null, link.code);
    else if (!hit) setHover(null, null);
    if (opts.onClick) opts.onClick(hit ? { station: hit.item.st } : link ? { link: link } : null, p);
  }
  function setHover(id, code) {
    if (M.hoverId === id && M.hoverLink === code) return;
    if (M.hoverId && M.nodes[M.hoverId]) M.nodes[M.hoverId].node.classList.remove('hover');
    M.hoverId = id;
    M.hoverLink = code;
    if (id && M.nodes[id]) M.nodes[id].node.classList.add('hover');
    M.links.forEach(function (link) { link.g.classList.toggle('hover', link.code === code); });
    M.stage.classList.toggle('pointing', Boolean(id || code));
    if (!id && !code && opts.onHover) opts.onHover(null);
  }

  function radius(st) {
    if (isIdle(st)) return 3;
    return Math.max(2.5, Math.sqrt(Math.abs(st.mw)) * M.K);
  }

  function draw() {
    var data = M.data;
    if (!M.ready || !data || !active()) return;
    if (!data.model) {
      var feed = data.status && data.status.units;
      message(feed && feed.last_error ? 'Station data unavailable: ' + feed.last_error : 'Collecting the first power station data. This takes about a minute after the dashboard starts.');
    } else {
      message(null);
    }
    refreshStations();
    drawLinks(data.links || []);
    requestRedraw();
  }

  function refreshStations() {
    var data = M.data;
    if (!M.ready || !data) return;
    var list = stationsOf(data);
    var seen = {};
    list.forEach(function (st) {
      seen[st.id] = true;
      var entry = M.nodes[st.id];
      if (!entry) {
        entry = M.nodes[st.id] = { node: s('circle', { 'class': 'st' }) };
      }
      entry.st = st;
      var cls = 'st ';
      if (st.mw <= -1) cls += 'using k-' + st.c;
      else if (isIdle(st)) cls += 'idle k-' + st.c;
      else cls += 'f-' + st.c + (st.off ? ' sea-st' : '');
      if (opts.selected && opts.selected() === st.id) cls += ' sel';
      if (M.hoverId === st.id) cls += ' hover';
      if (opts.dimmed && opts.dimmed(st)) cls += ' dim';
      entry.node.setAttribute('class', cls);
      var visible = !opts.visible || opts.visible(st);
      entry.node.style.display = visible ? '' : 'none';
      entry.visible = visible;
    });
    Object.keys(M.nodes).forEach(function (id) {
      if (!seen[id]) { M.nodes[id].node.remove(); delete M.nodes[id]; }
    });
    M.order = list.map(function (st) { return M.nodes[st.id]; }).sort(function (a, b) { return radius(b.st) - radius(a.st); });
    M.order.forEach(function (entry) { M.gStations.appendChild(entry.node); });
  }

  function drawLinks(links) {
    var seen = {};
    M.links = links.filter(function (link) { return link.gb && link.far; }).map(function (data) {
      seen[data.code] = true;
      var link = M.linkNodes[data.code];
      if (!link) {
        link = M.linkNodes[data.code] = { code: data.code, g: s('g', { 'class': 'link' }) };
        link.base = s('path');
        link.flow = s('path', { 'class': 'link-flow' });
        link.arrow = s('path', { 'class': 'link-arrow' });
        link.g.appendChild(link.base);
        link.g.appendChild(link.flow);
        link.g.appendChild(link.arrow);
        M.gLinks.appendChild(link.g);
        link.end = s('circle', { 'class': 'link-end', r: 3.5 });
        link.gbEnd = s('circle', { 'class': 'link-gb', r: 3 });
        link.label = s('text', { 'class': 'link-label' }, [s('tspan'), s('tspan', { 'class': 'v' })]);
        M.gEnds.appendChild(link.end);
        M.gEnds.appendChild(link.gbEnd);
        M.gEnds.appendChild(link.label);
      }
      ['name', 'country', 'mw', 'cap', 'gb', 'far'].forEach(function (key) { link[key] = data[key]; });
      link.flowing = num(link.mw) && Math.abs(link.mw) >= 10;
      link.width = link.flowing ? 1.5 + 5 * Math.min(1, Math.abs(link.mw) / 1400) : 1.5;
      link.base.setAttribute('class', link.flowing ? 'link-base' : 'link-idle');
      link.base.style.strokeWidth = link.width + 'px';
      link.flow.style.display = link.flowing ? '' : 'none';
      link.arrow.style.display = link.flowing ? '' : 'none';
      if (link.flowing) {
        link.flow.style.strokeWidth = (link.width + 1.5) + 'px';
        var share = link.cap ? Math.min(1, Math.abs(link.mw) / link.cap) : 0.5;
        var duration = (2.6 - 1.6 * share).toFixed(1) + 's';
        if (link.flow.style.animationDuration !== duration) link.flow.style.animationDuration = duration;
      }
      link.label.childNodes[0].textContent = link.name;
      link.label.childNodes[1].textContent = linkWords(link);
      return link;
    });
    Object.keys(M.linkNodes).forEach(function (code) {
      if (seen[code]) return;
      var link = M.linkNodes[code];
      [link.g, link.end, link.gbEnd, link.label].forEach(function (node) { node.remove(); });
      delete M.linkNodes[code];
    });
  }

  function requestRedraw() {
    if (M.raf) return;
    M.raf = requestAnimationFrame(function () { M.raf = 0; redraw(); });
  }

  function sx(x) { return M.tx + x * M.s; }
  function sy(y) { return M.ty + y * M.s; }

  function redraw() {
    if (!M.ready || !active()) return;
    var W = M.W, H = M.H;
    M.gLand.setAttribute('transform', 'translate(' + M.tx.toFixed(2) + ' ' + M.ty.toFixed(2) + ') scale(' + M.s.toFixed(5) + ')');
    M.screen = [];
    M.order.forEach(function (entry) {
      var st = entry.st, r = radius(st);
      var x = sx(st.x), y = sy(st.y);
      entry.node.setAttribute('cx', x.toFixed(1));
      entry.node.setAttribute('cy', y.toFixed(1));
      entry.node.setAttribute('r', r.toFixed(2));
      if (entry.visible && x > -40 && x < W + 40 && y > -40 && y < H + 40) M.screen.push({ id: st.id, sx: x, sy: y, r: r, st: st });
    });
    M.links.forEach(redrawLink);
    var taken = [];
    placeLinkLabels(taken);
    drawStationLabels(taken);
    placeGeoLabels(taken);
    drawSizeKey();
  }

  function overlaps(box, boxes) {
    return boxes.some(function (b) { return box[0] < b[2] && box[2] > b[0] && box[1] < b[3] && box[3] > b[1]; });
  }

  /* On a phone-sized map, fewer labels until you zoom in: they'd otherwise
     cover the coast and each other */
  function small() { return M.W < 520 && M.s / M.fitS < 1.8; }

  /* Interconnector labels first (largest flows win), then stations, then place names */
  function placeLinkLabels(taken) {
    var room = small() ? 3 : Infinity;
    M.links.slice().sort(function (a, b) { return Math.abs(b.mw || 0) - Math.abs(a.mw || 0); }).forEach(function (link, rank) {
      var end = [sx(link.far[0]), sy(link.far[1])];
      var place = LINK_LABEL[link.code] || [8, 4, 'start'];
      var value = linkWords(link);
      var width = Math.max(textWidth(link.name, 11.5, 400), textWidth(value, 11.5, 650));
      var onScreen = rank < room && end[0] > -30 && end[0] < M.W + 30 && end[1] > -30 && end[1] < M.H + 30;
      var chosen = null;
      if (onScreen) {
        [place[1], place[1] - 24, place[1] + 24].some(function (dy) {
          var x = end[0] + place[0], y = end[1] + dy;
          var left = place[2] === 'start' ? x : place[2] === 'end' ? x - width : x - width / 2;
          var shift = clamp(left, 6, M.W - width - 6) - left;
          var box = [left + shift - 2, y - 19, left + shift + width + 2, y + 11];
          if (box[1] < 2 || box[3] > M.H - 2 || overlaps(box, taken)) return false;
          chosen = { x: x + shift, y: y, box: box };
          return true;
        });
      }
      link.label.style.display = chosen ? '' : 'none';
      if (!chosen) return;
      taken.push(chosen.box);
      link.label.setAttribute('text-anchor', place[2]);
      var spans = link.label.childNodes;
      spans[0].setAttribute('x', chosen.x.toFixed(1));
      spans[0].setAttribute('y', (chosen.y - 7).toFixed(1));
      spans[1].setAttribute('x', chosen.x.toFixed(1));
      spans[1].setAttribute('y', (chosen.y + 7).toFixed(1));
      spans[1].textContent = value;
    });
  }

  function placeGeoLabels(taken) {
    var few = small();
    M.geoLabels.forEach(function (label) {
      if (few && !/\bgb\b/.test(label.node.getAttribute('class'))) { label.node.style.display = 'none'; return; }
      var x = sx(label.x), y = sy(label.y);
      var width = textWidth(label.node.textContent, 12.5, 400);
      var box = [x - width / 2 - 2, y - 12, x + width / 2 + 2, y + 4];
      var show = x > -width && x < M.W + width && y > 0 && y < M.H + 10 && !overlaps(box, taken);
      label.node.style.display = show ? '' : 'none';
      if (show) {
        label.node.setAttribute('x', x.toFixed(1));
        label.node.setAttribute('y', y.toFixed(1));
      }
    });
  }

  function redrawLink(link) {
    var a = [sx(link.gb[0]), sy(link.gb[1])], b = [sx(link.far[0]), sy(link.far[1])];
    var importing = num(link.mw) && link.mw > 0;
    var p0 = importing ? b : a, p2 = importing ? a : b;
    var dx = b[0] - a[0], dy = b[1] - a[1], len = Math.hypot(dx, dy) || 1;
    var bend = (LINK_BEND[link.code] || 0.05) * len;
    var c = [(a[0] + b[0]) / 2 - dy / len * bend, (a[1] + b[1]) / 2 + dx / len * bend];
    var d = 'M' + p0[0].toFixed(1) + ' ' + p0[1].toFixed(1) + 'Q' + c[0].toFixed(1) + ' ' + c[1].toFixed(1) + ' ' + p2[0].toFixed(1) + ' ' + p2[1].toFixed(1);
    link.base.setAttribute('d', d);
    if (link.flow) link.flow.setAttribute('d', d);
    link.pts = [];
    for (var i = 0; i <= 20; i++) {
      var t = i / 20, u = 1 - t;
      link.pts.push([u * u * p0[0] + 2 * u * t * c[0] + t * t * p2[0], u * u * p0[1] + 2 * u * t * c[1] + t * t * p2[1]]);
    }
    if (link.arrow) {
      var m = link.pts[10], ang = Math.atan2(p2[1] - p0[1], p2[0] - p0[0]) * 180 / Math.PI;
      var size = 4 + link.width;
      link.arrow.setAttribute('d', 'M' + size + ' 0L' + (-size * 0.8) + ' ' + (size * 0.75) + 'L' + (-size * 0.4) + ' 0L' + (-size * 0.8) + ' ' + (-size * 0.75) + 'Z');
      link.arrow.setAttribute('transform', 'translate(' + m[0].toFixed(1) + ' ' + m[1].toFixed(1) + ') rotate(' + ang.toFixed(1) + ')');
    }
    link.end.setAttribute('cx', b[0].toFixed(1));
    link.end.setAttribute('cy', b[1].toFixed(1));
    link.gbEnd.setAttribute('cx', a[0].toFixed(1));
    link.gbEnd.setAttribute('cy', a[1].toFixed(1));
  }

  function shortName(name) {
    var suffix = ' Wind Farm';
    return name.slice(-suffix.length) === suffix ? name.slice(0, -suffix.length) : name;
  }

  function drawStationLabels(taken) {
    clear(M.gLabels);
    var zoom = M.s / M.fitS;
    var minMw = 800 / Math.pow(Math.max(1, zoom), 1.6);
    var circles = [];
    M.screen.forEach(function (item) { if (item.r >= 6) circles.push([item.sx - item.r * 0.7, item.sy - item.r * 0.7, item.sx + item.r * 0.7, item.sy + item.r * 0.7]); });
    var selected = opts.selected ? opts.selected() : null;
    var candidates = M.screen.filter(function (item) { return item.st.mw >= minMw || item.id === selected; })
      .sort(function (a, b) { return (b.id === selected) - (a.id === selected) || b.st.mw - a.st.mw; });
    var placed = 0;
    candidates.forEach(function (item) {
      if (placed >= (small() ? 4 + zoom * 2 : (opts.maxLabels || 14) + zoom * 4)) return;
      var text = shortName(item.st.n);
      var width = textWidth(text, 12, 600), tall = 15;
      var options = [
        [item.sx + item.r + 4, item.sy + 4, 'start'],
        [item.sx - item.r - 4, item.sy + 4, 'end'],
        [item.sx, item.sy - item.r - 5, 'middle'],
        [item.sx, item.sy + item.r + 14, 'middle']
      ];
      for (var i = 0; i < options.length; i++) {
        var o = options[i];
        var left = o[2] === 'start' ? o[0] : o[2] === 'end' ? o[0] - width : o[0] - width / 2;
        var box = [left - 2, o[1] - tall + 3, left + width + 2, o[1] + 4];
        if (box[0] < 4 || box[2] > M.W - 4 || box[1] < 4 || box[3] > M.H - 4) continue;
        if (overlaps(box, taken) || overlaps(box, circles)) continue;
        taken.push(box);
        M.gLabels.appendChild(s('text', { x: o[0].toFixed(1), y: o[1].toFixed(1), 'text-anchor': o[2], 'class': 'st-label' + (item.st.off ? ' on-sea' : ''), text: text }));
        placed++;
        break;
      }
    });
  }

  function drawSizeKey() {
    var host = opts.sizeKey;
    if (!host) return;
    var signature = M.K.toFixed(3);
    if (host.getAttribute('data-k') === signature) return;
    host.setAttribute('data-k', signature);
    clear(host);
    var sizes = [100, 500, 2000];
    var rMax = Math.sqrt(sizes[2]) * M.K;
    var x = 0, items = [];
    sizes.forEach(function (size) {
      var r = Math.sqrt(size) * M.K;
      var text = size >= 1000 ? (size / 1000) + ' GW' : size + ' MW';
      var width = Math.max(2 * r, textWidth(text, 11.5, 400));
      items.push({ r: r, cx: x + width / 2, text: text });
      x += width + 12;
    });
    var svg = s('svg', { width: Math.ceil(x - 12), height: Math.ceil(2 * rMax + 34) });
    svg.appendChild(s('text', { x: 0, y: 11, text: 'Output', 'font-weight': '600' }));
    items.forEach(function (item) {
      var base = 18 + 2 * rMax;
      svg.appendChild(s('circle', { cx: item.cx.toFixed(1), cy: (base - item.r).toFixed(1), r: item.r.toFixed(1) }));
      svg.appendChild(s('text', { x: item.cx.toFixed(1), y: base + 14, 'text-anchor': 'middle', text: item.text }));
    });
    host.appendChild(svg);
  }

  init();
  return {
    state: M,
    setData: function (data) { M.data = data; draw(); },
    draw: draw,
    restyle: function () { refreshStations(); requestRedraw(); },
    redraw: requestRedraw,
    resize: resize,
    reset: function () { M.moved = false; fitDefault(); requestRedraw(); },
    zoomBy: function (factor) { interacted(); zoomAt([M.W / 2, M.H / 2], M.s * factor); },
    setHover: setHover,
    nodeFor: function (id) { return M.nodes[id] ? M.nodes[id].node : null; }
  };
}
