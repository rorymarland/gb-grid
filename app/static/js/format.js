/* Numbers, times (always UK time) and small utilities */

export const TZ = 'Europe/London';

export function num(v) { return v !== null && v !== undefined && !isNaN(v); }
export function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }
var timeFmt = new Intl.DateTimeFormat('en-GB', { hour: '2-digit', minute: '2-digit', hourCycle: 'h23', timeZone: TZ });
var dayFmt = new Intl.DateTimeFormat('en-GB', { weekday: 'short', day: 'numeric', month: 'short', timeZone: TZ });
var dateFmt = new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', year: 'numeric', timeZone: TZ });
var keyFmt = new Intl.DateTimeFormat('en-CA', { year: 'numeric', month: '2-digit', day: '2-digit', timeZone: TZ });
var intFmt = new Intl.NumberFormat('en-GB', { maximumFractionDigits: 0 });
export function hm(t) { return timeFmt.format(new Date(t * 1000)); }
export function day(t) { return dayFmt.format(new Date(t * 1000)); }
export function date(t) { return dateFmt.format(new Date(t * 1000)); }
export function dayKey(t) { return keyFmt.format(new Date(t * 1000)); }
export function when(t, now) {
  /* "14:30", "Yesterday 14:30" or "Thu 5 Nov 14:30" relative to now */
  now = now || Date.now() / 1000;
  var key = dayKey(t);
  if (key === dayKey(now)) return hm(t);
  if (key === dayKey(now - 86400)) return 'Yesterday ' + hm(t);
  if (key === dayKey(now + 86400)) return 'Tomorrow ' + hm(t);
  return day(t) + ' ' + hm(t);
}
export function int(v) { return num(v) ? intFmt.format(Math.round(v)) : '–'; }
export function gw(vMw) {
  if (!num(vMw)) return '–';
  var v = vMw / 1000;
  return Math.abs(v) >= 1 ? v.toFixed(1) : v.toFixed(2);
}
export function money(pounds) {
  if (!num(pounds)) return '–';
  var sign = pounds < 0 ? '−' : '', v = Math.abs(pounds);
  if (v >= 1e6) return sign + '£' + (v / 1e6).toFixed(v >= 1e7 ? 1 : 2) + 'm';
  if (v >= 1e4) return sign + '£' + Math.round(v / 1e3) + 'k';
  if (v >= 1e3) return sign + '£' + (v / 1e3).toFixed(1) + 'k';
  return sign + '£' + Math.round(v);
}
export function ago(seconds) {
  seconds = Math.max(0, Math.round(seconds));
  if (seconds < 60) return seconds + ' s ago';
  if (seconds < 3600) return Math.round(seconds / 60) + ' min ago';
  if (seconds < 172800) return Math.round(seconds / 3600) + ' h ago';
  return Math.round(seconds / 86400) + ' days ago';
}
export function plural(n, one, many) { return n + ' ' + (n === 1 ? one : many); }
export function niceStep(span, target) {
  var raw = span / Math.max(1, target);
  var magnitude = Math.pow(10, Math.floor(Math.log(raw) / Math.LN10));
  var norm = raw / magnitude;
  return (norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 2.5 ? 2.5 : norm <= 5 ? 5 : 10) * magnitude;
}
var measure = null;
export function textWidth(text, size, weight) {
  if (!measure) measure = document.createElement('canvas').getContext('2d');
  measure.font = (weight || 600) + ' ' + size + 'px ' + getComputedStyle(document.body).fontFamily;
  return measure.measureText(text).width;
}
export function css(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }

/* Re-render only when the inputs to a section change */
let signatures = {};
export function resetChanged() { signatures = {}; }
export function changed(name, inputs) {
  var signature = JSON.stringify(inputs);
  if (signatures[name] === signature) return false;
  signatures[name] = signature;
  return true;
}
export const mw = int;
