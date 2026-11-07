/* NESO's 14 regions (regions.json): geometry and carbon intensity levels,
   shared by the Regions page and the Display view. Paths are in half-km
   units; Shetland moves to an inset east of Orkney so the map keeps a
   sensible height. */

import { num } from './format.js';

export const LEVEL_CLASS = ['ci-very-low', 'ci-low', 'ci-moderate', 'ci-high', 'ci-very-high'];
export const LEVEL_NAME = ['Very low', 'Low', 'Moderate', 'High', 'Very high'];
export const MAP_REGIONS = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14];
export const SHETLAND_Y = -2330;   /* half-km units: rings starting north of 59.5N */
export const INSET = { dx: 175, dy: 230 };

/* NESO's index if given, else its published bands */
export function levelFor(value, level) {
  if (num(level)) return level;
  if (!num(value)) return null;
  return value < 40 ? 0 : value < 120 ? 1 : value < 200 ? 2 : value < 280 ? 3 : 4;
}
export function isDarkFill(level) { return level === 4 || level === 3 || level === 0; }

/* Bounding box (half-km units) of one "M x y l dx dy ..." subpath */
export function ringBox(part) {
  var nums = part.match(/-?\d+/g).map(Number);
  var x = nums[0], y = nums[1], box = [x, y, x, y];
  for (var k = 2; k + 1 < nums.length; k += 2) {
    x += nums[k];
    y += nums[k + 1];
    box[0] = Math.min(box[0], x); box[1] = Math.min(box[1], y);
    box[2] = Math.max(box[2], x); box[3] = Math.max(box[3], y);
  }
  return box;
}
export function grow(box, other) {
  return box ? [Math.min(box[0], other[0]), Math.min(box[1], other[1]), Math.max(box[2], other[2]), Math.max(box[3], other[3])] : other.slice();
}

/* Split each region into its mainland and its Shetland part, and work out
   the view box in km: { regions: [{ region, mainland, island }], view, insetFrame } */
export function regionShapes(geo, pad) {
  var mainBox = null, islandBox = null, insetFrame = null;
  var regions = geo.regions.map(function (region) {
    var mainland = '', island = '';
    region.d.split(/(?=M)/).forEach(function (part) {
      var box = ringBox(part);
      if (box[1] < SHETLAND_Y) { island += part; islandBox = grow(islandBox, box); }
      else { mainland += part; mainBox = grow(mainBox, box); }
    });
    return { region: region, mainland: mainland, island: island };
  });
  var view = [mainBox[0] / 2, mainBox[1] / 2, mainBox[2] / 2, mainBox[3] / 2];
  if (islandBox) {
    var ib = [islandBox[0] / 2 + INSET.dx - 7, islandBox[1] / 2 + INSET.dy - 7, islandBox[2] / 2 + INSET.dx + 7, islandBox[3] / 2 + INSET.dy + 7];
    view = [Math.min(view[0], ib[0]), Math.min(view[1], ib[1]), Math.max(view[2], ib[2]), Math.max(view[3], ib[3])];
    insetFrame = ib;
  }
  pad = pad === undefined ? 12 : pad;
  return { regions: regions, insetFrame: insetFrame, view: [view[0] - pad, view[1] - pad, view[2] - view[0] + 2 * pad, view[3] - view[1] + 2 * pad] };
}
