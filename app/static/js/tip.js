/* Hover tooltips */

import { h, clear } from './dom.js';
import { clamp } from './format.js';

export function makeTip(container) {
  var tip = h('div', { cls: 'tip', 'aria-hidden': 'true' });
  tip.hidden = true;
  container.appendChild(tip);
  return {
    node: tip,
    show: function (title, rows, x, y, note) {
      clear(tip);
      tip.appendChild(h('div', { cls: 't', text: title }));
      rows.forEach(function (row) {
        if (row.sep) { tip.appendChild(h('div', { cls: 'sep' })); return; }
        tip.appendChild(h('div', { cls: 'row' }, [
          h('span', { cls: 'key ' + (row.cls || 'none'), style: row.color ? 'background:' + row.color : null }),
          h('span', { cls: 'v', text: row.value }),
          h('span', { cls: 'l', text: row.label })
        ]));
      });
      if (note) tip.appendChild(h('div', { cls: 'note', text: note }));
      tip.hidden = false;
      var width = container.clientWidth, height = container.clientHeight;
      var tipWidth = tip.offsetWidth, tipHeight = tip.offsetHeight;
      var left = x + 18;
      if (left + tipWidth > width - 6) left = x - tipWidth - 18;
      var centred = clamp(x - tipWidth / 2, 6, Math.max(6, width - tipWidth - 6));
      if (left < 6) left = centred;
      var top = clamp(y - tipHeight / 2, 6, Math.max(6, height - tipHeight - 6));
      if (left === centred && Math.abs(top - y) < tipHeight) {
        top = y + 22 + tipHeight < height ? y + 22 : Math.max(6, y - tipHeight - 22);
      }
      tip.style.left = left + 'px';
      tip.style.top = top + 'px';
    },
    hide: function () { tip.hidden = true; }
  };
}
