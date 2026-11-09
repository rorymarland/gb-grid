/* The next hour: a simplified merit order for the "what if" sandbox.

   Every source that could run is a block of megawatts at a cost per MWh.
   Sorted cheapest first, the blocks make the supply curve; the market runs
   them in that order until demand is met, and the last one needed sets the
   price for everyone. Costs are typical short-run costs: fuel and carbon for
   gas, near zero for wind, solar and nuclear, and the European price for
   imports. Real prices come from bids in several markets and NESO's
   balancing actions, so this shows the logic rather than forecasting a price.

     var stack = buildStack(base, settings);
     var result = clear(stack, demandMW, settings); */

export const DEFAULT_SETTINGS = { gas: 90, carbon: 60, europe: 80 };   // p/therm, £/t CO2, £/MWh
export const COLOURS = { nuclear: '--c-nuclear', wind: '--c-wind', solar: '--c-solar', hydro: '--c-hydro', biomass: '--c-biomass',
  gas: '--c-gas', other: '--c-other', storage: '--c-storage', imports: '--c-imports', coal: '--c-coal' };
/* g CO2 per kWh, matching the dashboard's carbon factors */
const CI = { nuclear: 0, wind: 0, solar: 0, hydro: 0, biomass: 120, storage: 0, other: 651, coal: 937 };
const KWH_PER_THERM = 29.3071;
const CO2_PER_MWH_GAS = 0.184;   // tonnes per MWh of gas burned

/* A steady pseudo-random number in [0, 1) from a station id, so each gas
   plant keeps the same efficiency between runs */
function spread(id) {
  var x = Math.sin((+id || 1) * 12.9898) * 43758.5453;
  return x - Math.floor(x);
}

export function gasCost(efficiency, s) {
  var fuel = s.gas / 100 * 1000 / KWH_PER_THERM / efficiency;     // £/MWh of electricity
  var carbon = CO2_PER_MWH_GAS / efficiency * s.carbon;
  return { cost: fuel + carbon, ci: Math.round(CO2_PER_MWH_GAS / efficiency * 1000) };
}

/* base: { stations: [{ id, n, c, cap, mw }], extra: { wind, solar }, links: [{ name, cap, country }], offline: { sid: MW } }
   settings: { gas, carbon, europe, windScale, remove: { name: true } } */
export function buildStack(base, settings) {
  var s = Object.assign({}, DEFAULT_SETTINGS, settings || {});
  var remove = s.remove || {}, windScale = s.windScale === undefined ? 1 : s.windScale;
  var blocks = [];
  function add(b) { if (b.mw > 1 && !remove[b.name]) blocks.push(b); }
  (base.stations || []).forEach(function (st) {
    var cap = Math.max(st.cap || 0, st.mw || 0) - ((base.offline || {})[st.id] || 0);
    var mw = Math.max(0, st.mw || 0);
    var b = { name: st.n, cat: st.c, id: st.id };
    switch (st.c) {
      case 'wind': b.mw = mw * windScale; b.cost = -5; b.ci = 0; b.note = 'Runs whenever the wind blows; subsidies let it bid below zero'; break;
      case 'solar': b.mw = mw; b.cost = 0; b.ci = 0; break;
      case 'nuclear': b.mw = mw; b.cost = 8; b.ci = 0; b.note = 'Runs flat out; it can’t turn up and rarely turns down'; break;
      case 'biomass': b.mw = Math.max(0, cap); b.cost = 45; b.ci = CI.biomass; b.note = 'Subsidised, so it bids low'; break;
      case 'hydro': b.mw = Math.max(mw, 0.4 * Math.max(0, cap)); b.cost = 35; b.ci = 0; break;
      case 'gas': {
        var big = (st.cap || 0) >= 250;
        var eff = big ? 0.47 + 0.11 * spread(st.id) : 0.33 + 0.05 * spread(st.id);
        var g = gasCost(eff, s);
        b.mw = Math.max(0, cap); b.cost = g.cost; b.ci = g.ci; b.eff = eff;
        b.note = (big ? 'Combined cycle' : 'Open cycle or engines') + ', about ' + Math.round(eff * 100) + '% efficient';
        break;
      }
      case 'storage': b.mw = Math.max(0, cap); b.cost = 130; b.ci = 0; b.note = 'Batteries and pumped hydro sell when prices are high, for an hour or two'; break;
      case 'coal': b.mw = Math.max(0, cap); b.cost = 200; b.ci = CI.coal; break;
      default: b.mw = Math.max(0, cap); b.cost = 170; b.ci = CI.other; b.note = 'Peaking plant and oil'; break;
    }
    add(b);
  });
  var extra = base.extra || {};
  add({ name: 'Smaller wind farms', cat: 'wind', mw: (extra.wind || 0) * windScale, cost: -5, ci: 0, note: 'Wind not shown as separate stations' });
  add({ name: 'Rooftop and small solar', cat: 'solar', mw: extra.solar || 0, cost: 0, ci: 0 });
  (base.links || []).forEach(function (l) {
    add({ name: l.name, cat: 'imports', mw: l.cap, cost: s.europe + 2, ci: l.ci || 200, link: true, cap: l.cap, note: 'Imports at the European price' });
  });
  blocks.sort(function (a, b) { return a.cost - b.cost || b.mw - a.mw; });
  var x = 0;
  blocks.forEach(function (b) { b.from = x; x += b.mw; b.to = x; });
  return { blocks: blocks, total: x, settings: s };
}

/* Run the stack against demand. Cables export whenever GB is cheaper
   than Europe, so demand is higher (by the export capacity) at any price
   below Europe's. The first block that covers demand at its own price sets
   the price; if the cheap blocks run out between the two demands, GB's
   price settles at Europe's and the cables export what is left over. */
export function clear(stack, demand, options) {
  var s = stack.settings, blocks = stack.blocks;
  var exportCap = (options && options.exportCap) || 0;
  var used = blocks.map(function () { return 0; });
  var cum = 0, marginal = null, price = null, exporting = 0, target = demand;
  for (var i = 0; i < blocks.length; i++) {
    var b = blocks[i];
    if (b.cost >= s.europe && price === null && exportCap > 0) {
      // Crossing Europe's price: exports stop at whatever the cheaper blocks could spare
      if (cum >= demand) { price = s.europe; exporting = Math.min(exportCap, cum - demand); marginal = { name: 'Europe', cat: 'imports', cost: s.europe, link: true }; break; }
    }
    target = demand + (b.cost < s.europe ? exportCap : 0);
    var take = Math.min(b.mw, Math.max(0, target - cum));
    used[i] = take;
    cum += take;
    if (take > 0) marginal = b;
    if (cum >= target - 1e-6) { price = b.cost; exporting = target - demand; break; }
  }
  var short = price === null ? Math.max(0, demand - cum) : 0;
  if (price === null) price = short > 0 ? 1000 : marginal ? marginal.cost : 0;
  // Exports leave from the last blocks used; keep it simple and scale everything
  var energy = 0, carbon = 0, byCat = {};
  blocks.forEach(function (b, k) {
    if (!used[k]) return;
    byCat[b.cat] = (byCat[b.cat] || 0) + used[k];
    energy += used[k]; carbon += used[k] * b.ci;
  });
  return { demand: demand, exporting: exporting, used: used, marginal: marginal, short: short, price: price,
    ci: energy ? carbon / energy : 0, byCat: byCat, cost: price * demand };
}
