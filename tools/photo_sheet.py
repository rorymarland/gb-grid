#!/usr/bin/env python3
"""Contact sheets of the photos in app/station_photos.csv, for reviewing matches.

Usage: python3 tools/photo_sheet.py OUT_DIR   (needs Pillow and access to Wikimedia)

Writes sheet-1.jpg, sheet-2.jpg, … with each photo labelled by station name.
"""
import csv
import io
import os
import sys
import time
import urllib.parse
import urllib.request

from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UA = 'gb-grid-photo-builder/1.0 (https://github.com/rorymarland/gb-grid)'
TILE_W, TILE_H, LABEL, COLS, PER_SHEET = 240, 160, 18, 5, 30


def thumb(name):
    url = 'https://commons.wikimedia.org/w/index.php?' + urllib.parse.urlencode({'title': 'Special:FilePath/' + name, 'width': TILE_W * 2})
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        img = Image.open(io.BytesIO(r.read()))
        img.load()
    return img.convert('RGB')


def main():
    out = sys.argv[1]
    os.makedirs(out, exist_ok=True)
    stations = {r['id']: r['name'] for r in csv.DictReader(open(os.path.join(ROOT, 'app', 'stations.csv'), encoding='utf-8'))}
    rows = list(csv.DictReader(open(os.path.join(ROOT, 'app', 'station_photos.csv'), encoding='utf-8')))
    font = ImageFont.load_default()
    for n in range(0, len(rows), PER_SHEET):
        chunk = rows[n:n + PER_SHEET]
        sheet_rows = (len(chunk) + COLS - 1) // COLS
        sheet = Image.new('RGB', (COLS * TILE_W, sheet_rows * (TILE_H + LABEL)), 'white')
        draw = ImageDraw.Draw(sheet)
        for i, row in enumerate(chunk):
            x, y = (i % COLS) * TILE_W, (i // COLS) * (TILE_H + LABEL)
            try:
                img = thumb(row['file'])
                img.thumbnail((TILE_W - 4, TILE_H - 4))
                sheet.paste(img, (x + (TILE_W - img.width) // 2, y + (TILE_H - img.height) // 2))
            except Exception as error:
                draw.text((x + 6, y + 60), 'failed: %s' % error, fill='red', font=font)
            draw.text((x + 4, y + TILE_H + 2), '%d %s' % (n + i + 1, stations.get(row['id'], row['id']))[:38], fill='black', font=font)
            time.sleep(0.3)
        sheet.save(os.path.join(out, 'sheet-%d.jpg' % (n // PER_SHEET + 1)), quality=72)


if __name__ == '__main__':
    main()
