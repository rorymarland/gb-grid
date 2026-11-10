/* The station card: a station's photo (or a locator map), where it is, and
   links to open it in a maps app. Shared by the map's side panel and the
   Stations list. Photos are Wikimedia Commons files cached by this server. */

import { h, s, clear } from '/static/js/dom.js';
import { gridRef } from '/static/js/osgrid.js';

/* ---------- formatting and links ---------- */

export function latLonText(lat, lon) {
  return Math.abs(lat).toFixed(4) + '° ' + (lat >= 0 ? 'N' : 'S') + ', ' + Math.abs(lon).toFixed(4) + '° ' + (lon >= 0 ? 'E' : 'W');
}

export function mapLinks(lat, lon, name) {
  var ll = lat.toFixed(5) + ',' + lon.toFixed(5);
  return [
    ['Google Maps', 'https://www.google.com/maps/search/?api=1&query=' + ll],
    ['Apple Maps', 'https://maps.apple.com/?ll=' + ll + '&q=' + encodeURIComponent(name)],
    ['OpenStreetMap', 'https://www.openstreetmap.org/?mlat=' + lat.toFixed(5) + '&mlon=' + lon.toFixed(5) + '#map=13/' + lat.toFixed(4) + '/' + lon.toFixed(4)]
  ];
}

function copyText(text) {
  if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text);
  // Plain http (a LAN address) has no clipboard API; fall back to a selection
  return new Promise(function (resolve, reject) {
    var area = h('textarea', { 'aria-hidden': 'true', style: 'position:fixed;top:-100px;opacity:0' });
    area.value = text;
    document.body.appendChild(area);
    area.select();
    var ok = false;
    try { ok = document.execCommand('copy'); } catch (e) { ok = false; }
    area.remove();
    if (ok) resolve(); else reject(new Error('copy failed'));
  });
}

/* ---------- locator map, for stations without a photo ---------- */

var geoPromise = null;
function loadGeo() {
  if (!geoPromise) {
    geoPromise = fetch('/geo.json').then(function (r) { return r.json(); }).catch(function () { geoPromise = null; return null; });
  }
  return geoPromise;
}

function locator(station, cat) {
  // km across: wide enough offshore to show the nearest coast
  var W = station.off ? 560 : 240, H = W / 1.6, r = W / 57;
  var svg = s('svg', { viewBox: (station.x - W / 2) + ' ' + (station.y - H / 2) + ' ' + W + ' ' + H, 'class': 'sc-locator', 'aria-hidden': 'true', preserveAspectRatio: 'xMidYMid slice' });
  var land = s('g', { transform: 'scale(0.5)' });   // the outlines are stored at twice the map's scale
  svg.appendChild(land);
  svg.appendChild(s('circle', { cx: station.x, cy: station.y, r: r * 2.1, 'class': 'sc-halo f-' + cat }));
  svg.appendChild(s('circle', { cx: station.x, cy: station.y, r: r, 'class': 'sc-dot f-' + cat }));
  loadGeo().then(function (geo) {
    if (!geo) return;
    geo.units.forEach(function (unit) {
      land.appendChild(s('path', { d: unit.d, 'class': unit.kind === 'gb' ? 'land' : 'land-near', 'fill-rule': 'evenodd' }));
    });
  });
  return svg;
}

/* ---------- the card ---------- */

/* The card's two parts, built once per station: the photo (or locator map)
   with its credit, and where the station is with links to maps apps.
   station: { id, n (name), c (category), x, y } from /api/map.
   place: the "place" object from /api/station. */
export function placeCard(station, place, options) {
  if (!place || typeof place.lat !== 'number') return null;
  var name = station.n;
  var figure = h('figure', { cls: 'sc-figure' });
  var photo = place.photo;
  var showLocator = function (note) {
    clear(figure);
    figure.className = 'sc-figure sc-map';
    figure.appendChild(locator(station, station.c));
    figure.appendChild(h('figcaption', { text: note }));
  };
  if (photo) {
    // Portrait photos are cropped to 4:3 and panoramas to 2.4:1, centred
    var ratio = photo.w && photo.h ? Math.min(2.4, Math.max(4 / 3, photo.w / photo.h)) : 1.6;
    var frame = h('div', { cls: 'sc-frame', style: 'aspect-ratio:' + ratio.toFixed(3) });
    var img = h('img', { src: photo.src, alt: 'Photo of ' + name, decoding: 'async' });
    var shown = function () { frame.classList.add('loaded'); };
    img.addEventListener('load', shown);
    img.addEventListener('error', function () { showLocator('The photo isn’t available right now. The dot marks ' + name + '.'); });
    if (img.complete && img.naturalWidth) shown();
    frame.appendChild(img);
    figure.appendChild(frame);
    var licence = photo.licence_url
      ? h('a', { href: photo.licence_url, target: '_blank', rel: 'noopener', text: photo.licence })
      : photo.licence;
    figure.appendChild(h('figcaption', null, [
      'Photo: ', h('a', { href: photo.page, target: '_blank', rel: 'noopener', text: photo.author }), ', ', licence, ', via Wikimedia Commons'
    ]));
  } else {
    showLocator('No freely licensed photo of ' + name + ' yet. The dot marks where it is.');
  }

  var copy = h('button', { type: 'button', cls: 'sc-copy', 'aria-live': 'polite', title: 'Copy the coordinates', text: 'Copy' });
  copy.addEventListener('click', function () {
    copyText(place.lat.toFixed(5) + ', ' + place.lon.toFixed(5)).then(function () {
      copy.textContent = 'Copied';
    }, function () {
      copy.textContent = 'Couldn’t copy';
    }).then(function () { setTimeout(function () { copy.textContent = 'Copy'; }, 1600); });
  });
  var rows = [
    h('div', null, [h('dt', { text: 'Coordinates' }), h('dd', null, [h('span', { cls: 'sc-num', text: latLonText(place.lat, place.lon) }), copy])])
  ];
  var ref = gridRef(place.lat, place.lon);
  if (ref) rows.push(h('div', null, [h('dt', { text: 'OS grid reference' }), h('dd', null, h('span', { cls: 'sc-num', text: ref }))]));
  var where = h('section', { cls: 'sc-where', 'aria-label': 'Where ' + name + ' is' }, [h('dl', null, rows)]);
  if (place.offshore) where.appendChild(h('p', { cls: 'sc-note', text: 'Offshore, so this is roughly the middle of the wind farm.' }));
  var links = h('p', { cls: 'sc-links' }, mapLinks(place.lat, place.lon, name).map(function (link) {
    return h('a', { href: link[1], target: '_blank', rel: 'noopener', text: link[0] });
  }));
  if (place.wikidata) links.appendChild(h('a', { cls: 'sc-quiet', href: 'https://www.wikidata.org/wiki/' + place.wikidata, target: '_blank', rel: 'noopener', text: 'Wikidata' }));
  where.appendChild(links);
  if (options && options.page) {
    where.appendChild(h('p', { cls: 'sc-more' }, h('a', { href: '/station?id=' + station.id, text: 'More about ' + name })));
  }
  return { figure: figure, where: where };
}
