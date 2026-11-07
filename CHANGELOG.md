# Changelog

Notable changes in each release. Version numbers follow
[semantic versioning](https://semver.org/), so the `1` image tag only moves to
releases that keep working with your existing settings and data.

## 1.2.0 (unreleased)

- **Plain English layer:** grid terms in the explanations are underlined and
  open a short definition; a new Glossary page (`/glossary`) lists all 41.
- **Today on the grid:** the Overview opens with a written summary of the day
  so far or yesterday, led by the most notable event (negative prices, a
  notice, the cleanest day of the week, heavy curtailment). The Display view
  has it as a slide too.
- **Display mode rebuilt** with its own slides, each designed to fit one
  screen: right now, the last 24 hours, the station map, regions, balancing
  (with today's half hours), the next 48 hours, spare capacity and outages,
  and then and now. It no longer shows cropped copies of the other pages.
  Slide names for `views=` have changed: `now,day,map,regions,balancing,outlook,outages,records`.
- The header spans the full window on every page, and every page uses the
  wave logo as its favicon.
- Under the hood: the pages' scripts and styles moved into `static/` as ES
  modules with shared helpers, the station map became a reusable module,
  and `server.py` became the `gridlive` package. No change to settings or
  data.

## 1.1.0 (2026-11-07)

New pages and views. Existing settings and data keep working; the new feeds
start filling in on first start.

- **Page tabs and a grid warning banner** on every page while NESO has a
  margin notice or similar warning in force.
- **Map time machine:** replay the last seven days in 10-minute steps, with
  play at 1, 4 or 12 hours a second. Station history now keeps seven days.
- **Regions page:** NESO's 14 regions coloured by carbon intensity from 24
  hours ago to 48 hours ahead, each region's mix and solar output.
- **Balancing page:** live bids and offers on a map, what each half hour of
  balancing cost today and yesterday, the units paid most, and daily
  constraint costs.
- **Outages page:** spare capacity over the next 12 hours, large units offline
  and when they're due back, the nuclear fleet, upcoming outages and notices.
  The Overview shows a spare-capacity strip that links to it.
- **History page:** NESO's generation mix and carbon intensity since 2009 and
  wholesale prices since 2018, zoomable down to the half hour, with records,
  the latest day against a year earlier, and each year's mix. Downloads about
  20 MB once (about 20 minutes; prices take a few hours to fill in behind it);
  set `LONG_HISTORY=false` to skip it.
- **The next 48 hours** on the Overview: demand, wind, solar and carbon
  intensity forecasts.
- **Display mode** at `/display`: a kiosk view for a tablet or spare screen
  that cycles through the views and dims overnight.
- New settings: `POLL_WARNINGS_SECONDS`, `POLL_REGIONS_SECONDS`,
  `POLL_BALANCING_SECONDS`, `POLL_OUTAGES_SECONDS`, `POLL_OUTLOOK_SECONDS`,
  `LONG_HISTORY` and `POLL_LONG_HISTORY_SECONDS`.

## 1.0.0 (2026-11-07)

The first release.

- **Overview page:** live generation mix, demand, carbon intensity, wholesale
  price, grid frequency and interconnector flows, with 24-hour, 7-day and
  30-day history and the cleanest times to use electricity over the next day.
- **Regional carbon intensity** for any of NESO's 17 regions, with that
  region's generation mix and an explanation when it sits at 0 g/kWh.
- **Map page:** modelled output of each grid-connected power station, live
  interconnector flows, zoom, pan and full screen.
- **Stations page:** output by nation and by fuel, every station with its
  capacity and a 24-hour history, and the units that couldn't be placed.
- **Image:** `linux/amd64` and `linux/arm64`, Python standard library only,
  runs as a non-root user with a read-only root filesystem.
