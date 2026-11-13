/* The plain-English layer: what the grid's jargon means.

   Explanatory text on every page (explain boxes, subtitles, ledes and
   anything marked data-glossary) gets its first mention of each term
   underlined (organisation names and units only once a page); hovering, focusing or tapping it shows a short definition.
   Code can also mark a term directly with term(key, text). The full list
   is the Glossary page, /glossary. */

import { h, clear } from './dom.js';

export const GROUPS = [
  ['measures', 'Measuring the grid'],
  ['sources', 'Where the power comes from'],
  ['network', 'Moving the power'],
  ['money', 'Markets and money'],
  ['security', 'Keeping the lights on'],
  ['who', 'Who does what']
];

/* key: [group, name, definition, phrases to recognise in text (regular
   expression source, case-insensitive), page that shows it] */
const T = {
  gw: ['measures', 'Gigawatt (GW) and megawatt (MW)', 'Units of power: how fast electricity is being made or used at a moment. 1 GW is 1,000 MW. Britain uses roughly 20 to 45 GW depending on the time of day and the season, and a large power station produces around 1 GW.', ['gigawatts?', 'megawatts?']],
  mwh: ['measures', 'Megawatt-hour (MWh)', 'A unit of energy: 1 MW kept up for an hour. A household uses around 3 MWh of electricity a year. Gigawatt-hours (GWh) are 1,000 MWh, and the price of electricity in the market is quoted per MWh.', ['megawatt-hours?', 'MWh', 'GWh']],
  demand: ['measures', 'Demand', 'How much electricity Britain is using at a moment. It peaks on winter evenings between about 17:00 and 18:30, and is lowest overnight and on sunny summer afternoons, when rooftop solar cuts what the grid has to supply.', ['demand'], '/'],
  'national-demand': ['measures', 'National demand and transmission demand', 'The forecasts and figures that NESO and Elexon publish count only what the high-voltage transmission network supplies. National demand leaves out power from small generators on local networks, so it falls when rooftop solar is generating. Transmission demand adds power stations’ own use, pumping at pumped storage and exports.', ['national demand', 'transmission demand']],
  'carbon-intensity': ['measures', 'Carbon intensity', 'How much carbon dioxide is emitted to make each kilowatt-hour of electricity, in grams (g/kWh), averaged across everything generating at the time. It falls below 50 g/kWh on windy nights and can pass 250 g/kWh when gas is doing most of the work. NESO’s figures count the emissions at the power station only.', ['carbon intensity', 'g/kWh', 'gCO2/kWh'], '/regions'],
  'low-carbon': ['measures', 'Low-carbon and renewable', 'On this dashboard, low-carbon means wind, solar, hydro and nuclear: sources with almost no emissions when they generate. Renewable means wind, solar, hydro and biomass. Biomass counts as renewable but not low-carbon here, because burning wood emits carbon dioxide at the chimney even if the forests regrow.', ['low-carbon', 'zero-carbon', 'renewables?']],
  frequency: ['measures', 'Grid frequency', 'Britain’s mains electricity alternates 50 times a second (50 Hz). When demand runs ahead of supply the frequency falls, and when supply runs ahead it rises. NESO must keep it between 49.5 and 50.5 Hz, and normally holds it within 49.8 to 50.2 Hz.', ['grid frequency', 'frequency'], '/frequency'],
  'settlement-period': ['measures', 'Settlement period', 'The electricity market works in half hours. Each day has 48 settlement periods, or 46 and 50 on the days the clocks change. Prices, payments and many figures here are per half hour.', ['settlement periods?']],
  'capacity-factor': ['measures', 'Capacity and load factor', 'A power station’s capacity is the most it can produce. Its load factor is what it is actually producing as a share of that. Wind farms typically average 30 to 45% over a year; nuclear stations run close to full when they are working.', ['capacity factor', 'load factor', 'of capacity']],

  wind: ['sources', 'Wind', 'Britain’s largest source of electricity in many months. Large wind farms, most of them offshore, are metered every five minutes. Smaller ones on local networks are not, so their output is NESO’s estimate.', ['wind farms?', 'offshore wind']],
  embedded: ['sources', 'Embedded generation', 'Generators connected to the local distribution networks rather than the high-voltage transmission network: most solar panels and smaller wind farms. They are not metered in real time, so their output is estimated, and to the transmission network they look like lower demand.', ['embedded', 'small(er)? wind farms', 'local networks?']],
  solar: ['sources', 'Solar (PV_Live)', 'Almost all of Britain’s solar panels sit on local networks and are not metered in real time. The figures here are Sheffield Solar’s PV_Live estimates, scaled up from a sample of systems.', ['PV_Live', 'rooftop solar', 'solar panels']],
  nuclear: ['sources', 'Nuclear', 'Britain’s nuclear stations run steadily close to full output and cannot change output quickly. Refuelling and inspections take reactors offline for weeks or months, which shows on the Outages page.', ['nuclear fleet', 'reactors?'], '/outages'],
  ccgt: ['sources', 'Combined-cycle gas (CCGT)', 'The main kind of gas power station: a gas turbine, plus a steam turbine driven by its waste heat. About 55 to 60% efficient, and the main flexible source of power, ramping up and down as wind and demand change.', ['combined-cycle', 'CCGTs?']],
  ocgt: ['sources', 'Open-cycle gas and peaking plants', 'Gas turbines and engines without the steam stage. Less efficient, so more carbon-intensive, but they start in minutes, so they run at peaks and when the grid needs power quickly.', ['open-cycle', 'OCGTs?', 'peaking plants?', 'fast-start']],
  biomass: ['sources', 'Biomass', 'Power stations burning wood pellets, mostly Drax in North Yorkshire. Counted as renewable, because the accounting assumes the forests regrow; its emissions at the chimney are similar to coal’s.', ['biomass']],
  coal: ['sources', 'Coal', 'Britain’s last coal power station, Ratcliffe-on-Soar, closed on 30 September 2024, ending 142 years of coal power. It still appears in the history.', ['coal-free', 'coal']],
  'pumped-storage': ['sources', 'Pumped storage', 'Hydro stations with two reservoirs. They pump water uphill when power is cheap and plentiful, and release it through turbines at peaks or when a power station fails. Dinorwig in Snowdonia can reach full output in well under a minute.', ['pumped storage', 'pumped hydro', 'pumping']],
  battery: ['sources', 'Battery storage', 'Grid batteries charge when power is cheap and discharge at peaks, and can respond within a second to protect the grid’s frequency. Most hold one to two hours at full output. Elexon’s figures show them discharging but not charging.', ['batter(y|ies)']],

  transmission: ['network', 'Transmission network', 'The high-voltage network (400,000 and 275,000 volts, and 132,000 in Scotland) that moves power long distances from large power stations and wind farms. NESO operates it; National Grid, SP Energy Networks and SSEN own the wires.', ['transmission network', 'transmission']],
  distribution: ['network', 'Distribution networks', 'The lower-voltage local networks that carry power to homes and businesses, run by six companies across 14 licence areas. NESO’s regional figures follow those 14 areas.', ['distribution networks?', 'network companies', 'DNOs?']],
  interconnector: ['network', 'Interconnectors', 'High-voltage cables linking Britain to France, Belgium, the Netherlands, Norway, Denmark and the island of Ireland, most of them under the sea. Power usually flows towards whichever side has the higher price. Together they can carry around 10 GW.', ['interconnectors?'], '/map'],
  boundary: ['network', 'Network boundaries and constraints', 'Where the transmission network can only carry so much power from one area to another, most often between Scotland and England. When Scottish wind outstrips what those lines can carry, NESO pays wind farms to turn down and gas plants further south to turn up.', ['boundar(y|ies)', 'constraints?', 'bottlenecks?'], '/constraints'],

  'wholesale-price': ['money', 'Wholesale price (market index)', 'The price at which electricity is traded for delivery in each half hour, in pounds per megawatt-hour. The figure here is Elexon’s market index, an average of short-term trades on the EPEX SPOT exchange. £100/MWh is 10p per kWh, before network costs, levies and VAT.', ['wholesale price', 'market index', 'wholesale'], '/history'],
  'negative-price': ['money', 'Negative prices', 'Sometimes there is more power than anyone wants, usually when it is windy and demand is low. Some generators earn subsidies for each unit they produce, so they can still come out ahead while paying to keep exporting, and the market price goes below zero.', ['negative prices?', 'below zero']],
  'merit-order': ['money', 'Merit order', 'The order in which power sources are used: cheapest running cost first. Wind, solar and nuclear cost almost nothing to run, so they go first; gas plants follow, cheapest and most efficient first. The most expensive source still needed sets the wholesale price for everyone, which is why gas usually sets Britain’s price.', ['merit order', 'supply curve'], '/sandbox'],
  'balancing-mechanism': ['money', 'The Balancing Mechanism', 'NESO’s tool for matching supply and demand second by second and keeping power flows within what the network can carry. After the market closes, an hour before each half hour, generators and large users offer to change their output for a price, and NESO accepts what it needs.', ['balancing mechanism', 'balancing'], '/balancing'],
  bid: ['money', 'Bids', 'A price a generator asks to reduce its output (or a storage unit to take more power). Wind farms usually bid negative prices: NESO pays them to turn down, because they lose the subsidy they would have earned.', ['bids?', 'turned down', 'turn(ing)? down']],
  offer: ['money', 'Offers', 'A price a generator asks to increase its output. Gas plants and storage provide most of the offers NESO accepts.', ['offers?', 'turned up', 'turn(ing)? up']],
  boa: ['money', 'Bid-offer acceptances (BOAs)', 'NESO’s instructions to individual units through the Balancing Mechanism: change output to this level, from this time. Elexon publishes each one within minutes, which is how the map shows instructed units.', ['bid-offer acceptances?', 'BOAs?', 'instructions?']],
  pn: ['money', 'Physical notifications', 'Each unit’s planned output for each half hour, submitted to NESO before the market closes. The map combines these plans with NESO’s instructions to estimate what each station is producing.', ['physical notifications?']],
  curtailment: ['money', 'Curtailment', 'Paying generators, usually wind farms, to produce less than they could, because the network cannot carry the power to where it is needed. It is part of the cost of constraints.', ['curtail(ment|ed|ing)?']],
  'imbalance-price': ['money', 'Imbalance price', 'The price every supplier and generator settles at for any energy it used or made beyond what it contracted for, set each half hour from what NESO paid to balance the system. Also called the system price.', ['imbalance prices?', 'system prices?'], '/balancing'],
  'system-short': ['money', 'Short and long', 'Whether the market as a whole had contracted for less energy than was needed in a half hour (short: NESO bought the rest) or more (long: NESO sold the surplus). The net imbalance volume says by how much.', ['short of energy', 'net imbalance']],
  'constraint-costs': ['money', 'Constraint costs', 'What NESO pays to keep power flows within the network’s limits, mostly by turning wind down in Scotland and gas up in England. NESO publishes a daily breakdown; thermal constraints (lines at their limit) are usually the largest part.', ['constraint costs?'], '/constraints'],

  'derated-margin': ['security', 'De-rated margin', 'How much generation would be spare after allowing for the chance that some units fail. A comfortable margin is several gigawatts. NESO forecasts it ahead of each half hour, and Elexon publishes the forecast.', ['de-rated margin', 'spare capacity', 'margin'], '/outages'],
  lolp: ['security', 'Loss of load probability', 'NESO’s estimate of the chance that available generation will fall short of demand in a half hour. It is almost always tiny; it rises when margins are tight, and it also feeds into the prices of imbalances.', ['loss of load', 'shortfall']],
  'margin-notice': ['security', 'Margin and capacity market notices', 'Warnings NESO sends the market when spare capacity looks lower than it would like, to draw out more generation and imports. An Electricity Margin Notice is a request for more; a Capacity Market Notice is issued automatically when the forecast margin falls below a set threshold. Neither means the lights are about to go out.', ['margin notices?', 'capacity market notices?', 'electricity margin notice'], '/outages'],
  dfs: ['security', 'Demand Flexibility Service', 'A NESO scheme that pays households and businesses, through their suppliers, to use less electricity at set times when the grid is tight.', ['demand flexibility( service)?', 'DFS']],
  inertia: ['security', 'Inertia', 'The spinning mass of large generators’ turbines, which resists sudden changes in frequency and buys time when a power station trips. Wind, solar and batteries connect through electronics and add little, so NESO pays for other ways to provide it.', ['inertia'], '/frequency'],
  'frequency-response': ['security', 'Frequency response', 'Fast services, mostly batteries, that NESO buys to push frequency back towards 50 Hz. They act automatically on the frequency they measure, within a second or so, rather than waiting for an instruction. NESO buys them a day ahead in four-hour blocks.', ['frequency response', 'response services?'], '/frequency'],
  'dynamic-containment': ['security', 'Dynamic Containment', 'NESO’s fastest frequency response. It acts when frequency strays more than 0.2 Hz from 50 Hz, typically after a power station or interconnector trips, and delivers in full within a second. It’s bought separately for falls (low) and rises (high), and sized to cover the largest single loss the grid could suffer.', ['dynamic containment'], '/frequency'],
  'dynamic-moderation': ['security', 'Dynamic Moderation', 'A fast response service for the band between 0.1 and 0.2 Hz from 50 Hz: it catches sudden but smaller imbalances, such as an interconnector changing direction, before they grow.', ['dynamic moderation'], '/frequency'],
  'dynamic-regulation': ['security', 'Dynamic Regulation', 'A slower, continuous response service that nudges frequency back towards 50 Hz through the ordinary minute-to-minute ups and downs of supply and demand, reaching full output within about ten seconds.', ['dynamic regulation'], '/frequency'],
  reserve: ['security', 'Quick and slow reserve', 'Capacity NESO holds ready to change output within minutes of an instruction. Reserve takes over from the fast response services after a fault, so they are free for the next one.', ['quick reserve', 'slow reserve'], '/frequency'],
  trip: ['security', 'Trip', 'When a generator or interconnector disconnects suddenly, usually because its protection detects a fault. The power it was providing vanishes in a moment, so frequency falls until response services and other generators make up the gap.', ['trips?', 'tripped', 'tripping'], '/timeline'],
  remit: ['security', 'REMIT outage notices', 'Market rules that require generators and interconnectors to publish promptly when capacity is unavailable, whether planned (maintenance) or unplanned (a fault), and when they expect it back.', ['REMIT', 'outage notices', 'unplanned', 'planned']],

  neso: ['who', 'NESO', 'The National Energy System Operator, publicly owned since October 2024. It balances supply and demand in real time, operates the transmission network, and publishes the carbon intensity, demand and constraint data used here.', ['NESO', 'National Energy System Operator']],
  elexon: ['who', 'Elexon', 'Runs the rules that settle the electricity market: working out who generated and used what in each half hour, and who owes what. Its BMRS service publishes most of the live grid data on this dashboard.', ['Elexon', 'BMRS']],
  'bm-unit': ['who', 'BM unit', 'A generator, storage site or large user registered in the Balancing Mechanism. A power station is often several BM units, one per turbine or reactor.', ['BM units?', 'units']]
};

export const TERMS = {};
Object.keys(T).forEach(function (key) {
  var t = T[key];
  TERMS[key] = { key: key, group: t[0], name: t[1], text: t[2], phrases: t[3], page: t[4] || null };
});

/* One pattern for every phrase, longest first, on word boundaries */
var lookup = [];
Object.keys(TERMS).forEach(function (key) { TERMS[key].phrases.forEach(function (p) { lookup.push([p, key]); }); });
lookup.sort(function (a, b) { return b[0].length - a[0].length; });
var PATTERN = new RegExp('(^|[^\\w-])(' + lookup.map(function (x) { return x[0]; }).join('|') + ')(?![\\w-])', 'gi');
function keyFor(text) {
  for (var i = 0; i < lookup.length; i++) if (new RegExp('^(?:' + lookup[i][0] + ')$', 'i').test(text)) return lookup[i][1];
  return null;
}

/* A marked term, for code that builds text */
export function term(key, text) {
  return h('span', { cls: 'term', 'data-term': key, tabindex: '0', role: 'button', text: text || (TERMS[key] ? TERMS[key].name : key) });
}

/* ---------- finding terms in explanatory text ---------- */

var SCOPES = '.explain, .sub, .lede, .s-take, [data-glossary]';
var SKIP = 'a, button, input, select, textarea, svg, .term, .tip, .term-pop, [data-no-glossary], th';
/* Names that need explaining once a page, not in every panel */
var ONCE = { neso: 1, elexon: 1, gw: 1, mwh: 1, 'carbon-intensity': 1 };
/* Words too general to underline on their own; their longer phrases still match */
var NOISY = { units: 1, planned: 1, unplanned: 1, margin: 1, instructions: 1, instruction: 1, wholesale: 1, frequency: 1, demand: 1, transmission: 1, balancing: 1, coal: 1, pumping: 1, constraints: 1, constraint: 1 };

function shownElsewhere(key) {
  return Array.prototype.some.call(document.querySelectorAll('.term[data-term="' + key + '"]'), function (n) { return !n.closest('[hidden]'); });
}

function annotate(scope) {
  if (scope.closest && scope.closest('[data-no-glossary]')) return;
  var done = {};
  Array.prototype.forEach.call(scope.querySelectorAll('.term'), function (n) { done[n.getAttribute('data-term')] = true; });
  var walker = document.createTreeWalker(scope, NodeFilter.SHOW_TEXT, {
    acceptNode: function (node) {
      if (!node.nodeValue.trim()) return NodeFilter.FILTER_REJECT;
      var parent = node.parentElement;
      return parent && !parent.closest(SKIP) ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
    }
  });
  var nodes = [];
  while (walker.nextNode()) nodes.push(walker.currentNode);
  nodes.forEach(function (node) {
    var text = node.nodeValue, out = [], last = 0, m;
    PATTERN.lastIndex = 0;
    while ((m = PATTERN.exec(text))) {
      var word = m[2], key = keyFor(word), start = m.index + m[1].length;
      if (!key || done[key] || NOISY[word.toLowerCase()] || (ONCE[key] && shownElsewhere(key))) continue;
      done[key] = true;
      out.push(document.createTextNode(text.slice(last, start)), term(key, word));
      last = start + word.length;
    }
    if (!out.length) return;
    out.push(document.createTextNode(text.slice(last)));
    var parent = node.parentNode;
    out.forEach(function (n) { parent.insertBefore(n, node); });
    parent.removeChild(node);
  });
}

function scan(root) {
  if (!root || root.nodeType !== 1) return;
  if (root.matches && root.matches(SCOPES)) annotate(root);
  Array.prototype.forEach.call(root.querySelectorAll(SCOPES), annotate);
  if (root.closest) { var scope = root.closest(SCOPES); if (scope && scope !== root) annotate(scope); }
}

/* ---------- the popover ---------- */

var pop = null, current = null, hoverTimer = null, hideTimer = null, shownAt = 0;
function ensurePop() {
  if (pop) return pop;
  pop = h('div', { cls: 'term-pop', id: 'term-pop', role: 'tooltip', hidden: true });
  pop.addEventListener('pointerenter', function () { clearTimeout(hideTimer); });
  pop.addEventListener('pointerleave', function (e) { if (e.pointerType === 'mouse') hideSoon(); });
  document.body.appendChild(pop);
  return pop;
}
function show(node) {
  var t = TERMS[node.getAttribute('data-term')];
  if (!t) return;
  clearTimeout(hideTimer);
  ensurePop();
  if (current && current !== node) current.removeAttribute('aria-describedby');
  current = node;
  clear(pop);
  pop.appendChild(h('b', { text: t.name }));
  pop.appendChild(h('p', { text: t.text }));
  var links = [];
  if (t.page && t.page !== location.pathname) links.push(h('a', { href: t.page, text: pageName(t.page) }));
  if (location.pathname !== '/glossary') links.push(h('a', { href: '/glossary#' + t.key, text: 'All terms' }));
  if (links.length) pop.appendChild(h('div', { cls: 'links' }, links));
  if (pop.hidden) shownAt = Date.now();
  pop.hidden = false;
  node.setAttribute('aria-describedby', 'term-pop');
  place(node);
}
function pageName(path) {
  return { '/': 'Overview', '/map': 'See the map', '/regions': 'See the regions', '/balancing': 'See balancing', '/outages': 'See outages', '/history': 'See the history' }[path] || 'More';
}
function place(node) {
  var r = node.getBoundingClientRect(), vw = document.documentElement.clientWidth, vh = window.innerHeight;
  pop.style.maxWidth = Math.min(360, vw - 24) + 'px';
  var w = pop.offsetWidth, ph = pop.offsetHeight;
  var left = Math.max(12, Math.min(vw - w - 12, r.left + r.width / 2 - w / 2));
  var top = r.bottom + 8;
  if (top + ph > vh - 8 && r.top - ph - 8 > 8) top = r.top - ph - 8;
  pop.style.left = (left + window.scrollX) + 'px';
  pop.style.top = (top + window.scrollY) + 'px';
}
function hide() {
  clearTimeout(hoverTimer);
  if (pop) pop.hidden = true;
  if (current) current.removeAttribute('aria-describedby');
  current = null;
}
function hideSoon() { clearTimeout(hideTimer); hideTimer = setTimeout(hide, 220); }

export function install() {
  document.addEventListener('pointerover', function (e) {
    var node = e.target.closest && e.target.closest('.term');
    if (!node || e.pointerType !== 'mouse') return;
    clearTimeout(hoverTimer); clearTimeout(hideTimer);
    hoverTimer = setTimeout(function () { show(node); }, 180);
  });
  document.addEventListener('pointerout', function (e) {
    var node = e.target.closest && e.target.closest('.term');
    if (!node || e.pointerType !== 'mouse') return;
    clearTimeout(hoverTimer);
    hideSoon();
  });
  document.addEventListener('click', function (e) {
    var node = e.target.closest && e.target.closest('.term');
    /* A tap focuses the term (which opens it) and then clicks it: only a later click closes it */
    if (node) { e.preventDefault(); if (current === node && pop && !pop.hidden && Date.now() - shownAt > 400) hide(); else show(node); return; }
    if (pop && !pop.hidden && !e.target.closest('.term-pop')) hide();
  });
  document.addEventListener('focusin', function (e) { if (e.target.classList && e.target.classList.contains('term')) show(e.target); });
  document.addEventListener('focusout', function (e) { if (e.target.classList && e.target.classList.contains('term')) hideSoon(); });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') hide();
    else if ((e.key === 'Enter' || e.key === ' ') && e.target.classList && e.target.classList.contains('term')) { e.preventDefault(); show(e.target); }
  });
  window.addEventListener('resize', hide);
  scan(document.body);
  /* Pages rewrite their text as data arrives; mark terms in the new text */
  var queued = [], timer = null;
  new MutationObserver(function (records) {
    records.forEach(function (r) { if (r.target.nodeType === 1 && !r.target.closest('.term, .term-pop')) queued.push(r.target); });
    clearTimeout(timer);
    timer = setTimeout(function () {
      var batch = queued; queued = [];
      batch.forEach(function (n) { if (n.isConnected) scan(n); });
    }, 60);
  }).observe(document.body, { childList: true, subtree: true, characterData: true });
}
