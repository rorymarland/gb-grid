/* DOM building helpers */

const NS = 'http://www.w3.org/2000/svg';

export function byId(id) { return document.getElementById(id); }
export function clear(node) { while (node && node.firstChild) node.removeChild(node.firstChild); return node; }
export function add(node, kids) {
  if (kids === null || kids === undefined) return node;
  if (!Array.isArray(kids)) kids = [kids];
  kids.forEach(function (kid) {
    if (kid === null || kid === undefined || kid === false) return;
    node.appendChild(typeof kid === 'string' || typeof kid === 'number' ? document.createTextNode(String(kid)) : kid);
  });
  return node;
}
export function h(tag, props, kids) {
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
  return add(node, kids);
}
export function s(tag, attrs, kids) {
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
  return add(node, kids);
}

/* A station's name, linked to its page when it has one (sid is its station id) */
export function stationName(name, sid, cls) {
  var classes = 'name' + (cls ? ' ' + cls : '');
  if (typeof sid !== 'number') return h('span', { cls: classes, text: name });
  return h('a', { cls: classes + ' st-link', href: '/station?id=' + sid, text: name });
}
