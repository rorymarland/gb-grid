import G from '/static/js/gb.js';
import { GROUPS, TERMS } from '/static/js/glossary.js';

var h = G.h, byId = G.byId, clear = G.clear;
var host = byId('glossary'), input = byId('gl-q');

function render() {
  var q = input.value.trim().toLowerCase();
  clear(host);
  var shown = 0;
  GROUPS.forEach(function (g) {
    var items = Object.keys(TERMS).map(function (k) { return TERMS[k]; }).filter(function (t) {
      return t.group === g[0] && (!q || (t.name + ' ' + t.text + ' ' + t.phrases.join(' ')).toLowerCase().indexOf(q) >= 0);
    });
    if (!items.length) return;
    shown += items.length;
    host.appendChild(h('section', { cls: 'panel gl-group', 'aria-labelledby': 'g-' + g[0] }, [
      h('h2', { id: 'g-' + g[0], text: g[1] }),
      h('dl', null, items.map(function (t) {
        return [h('dt', { id: t.key }, [t.name, h('a', { cls: 'gl-anchor', href: '#' + t.key, 'aria-label': 'Link to ' + t.name, text: '#' })]),
          h('dd', null, [t.text, t.page ? ' ' : null, t.page ? h('a', { href: t.page, text: { '/': 'See the Overview', '/map': 'See the map', '/regions': 'See the regions', '/balancing': 'See balancing', '/outages': 'See outages', '/history': 'See the history' }[t.page] || 'More' }) : null])];
      }).reduce(function (a, b) { return a.concat(b); }, []))
    ]));
  });
  if (!shown) host.appendChild(h('p', { cls: 'empty', text: 'No terms match “' + input.value.trim() + '”.' }));
}

input.addEventListener('input', render);
render();
G.setLive('good', G.plural(Object.keys(TERMS).length, 'term', 'terms') + ' explained.');
if (location.hash) {
  var target = document.getElementById(location.hash.slice(1));
  if (target) { target.classList.add('hit'); target.scrollIntoView({ block: 'center' }); }
}
