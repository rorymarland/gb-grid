/* GB Grid: helpers shared by every page, as one namespace.

     import G from '/static/js/gb.js';

   The modules behind it can also be imported on their own. Importing this
   file also starts the page frame (theme switch, tabs, warning banner) and
   the glossary popovers. */

import * as dom from './dom.js';
import * as format from './format.js';
import * as tip from './tip.js';
import * as net from './net.js';
import * as chrome from './chrome.js';
import * as glossary from './glossary.js';

const G = Object.assign({}, dom, format, tip, net, { isDark: chrome.isDark, term: glossary.term, TERMS: glossary.TERMS });
Object.defineProperty(G, 'theme', { get: function () { return chrome.state.theme; }, enumerable: true });
Object.defineProperty(G, 'alerts', { get: function () { return chrome.state.alerts; }, enumerable: true });
window.GB = G;  /* for quick poking from the browser console */

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', glossary.install);
else glossary.install();

export default G;
