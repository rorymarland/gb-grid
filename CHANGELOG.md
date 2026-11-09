# Changelog

Notable changes in each release. Version numbers follow
[semantic versioning](https://semver.org/), so the `1` image tag only moves to
releases that keep working with your existing settings and data.

## 1.3.0 (unreleased)

- **Plain English layer:** grid terms in the explanations are underlined and
  open a short definition; a new Glossary page (`/glossary`) lists all 48.
- **Today on the grid:** the Overview opens with a written summary of the day
  so far or yesterday, led by the most notable event (negative prices, a
  notice, the cleanest day of the week, heavy curtailment). The Display view
  has it as a slide too, so the default slide list is now
  `now,story,day,map,regions,balancing,outlook,outages,records`.
- **Frequency page** (`/frequency`): live frequency against its limits, an hour
  to a week of 15-second readings, how steady it's been, NESO's inertia
  estimates and the frequency response held now. Sudden falls and rises are
  picked out as frequency events with their recovery time, likely cause and
  the response held at the time. Frequency readings are now kept for 8 days
  (about 46,000 rows) and backfilled on first start.
- **Timeline** (`/timeline`): notable moments kept for a year (frequency
  events, large unplanned outages, negative prices and spikes, grid notices,
  records, expensive constraint days and daily markers), filterable by kind.
  Recent ones open the map's time machine at that moment; the Map and History
  pages take `?at=<unix time>` for this. The Overview lists the latest few.
- **What if?** (`/sandbox`): trip a power station and watch the first minute
  (inertia, response services, governors, reserve, old loss-of-mains
  protection and demand disconnection), then a simplified merit order for the
  next hour with gas, carbon and European prices to play with. Starts from
  the grid now, a moment in the last week or a preset.
- New API endpoints: `/api/frequency`, `/api/events` and `/api/sandbox`. New settings:
  `POLL_RESPONSE_SECONDS` (default 3600) and `POLL_EVENTS_SECONDS` (120).
- **Security:** images are rebuilt weekly on a fresh base image (for `latest`
  and the latest release's tags) and scanned with Trivy before publishing;
  Alpine packages are upgraded at build time and pip is removed from the
  image. The server closes connections idle for 30 seconds and accepts at
  most 100 at once. GitHub Actions are pinned to commit SHAs, updated to their
  current major versions, and kept current by Dependabot.
- **Fixed:** the Display's 24-hour chart cut off its time labels when the
  slide settled after the first draw; it now redraws when its box changes size.
- README screenshots and feature notes brought up to date.

## 1.2.0 (2026-11-07)

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
