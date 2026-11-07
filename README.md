# GB Grid

A self-hosted, live dashboard and power station map for the Great Britain
electricity grid. One small container, no API keys, and nothing but the Python
standard library inside.

![The overview page](docs/overview.png)

<sub>Screenshots use sample data.</sub>

## What it shows

- **Overview:** what Britain is using right now and where it comes from.
  Generation mix, demand, carbon intensity, wholesale price, grid frequency and
  interconnector flows, with history charts (24 hours, 7 days, 30 days), the
  cleanest times to use electricity over the next day, for Great Britain or one
  of NESO's regions, and forecasts of demand, wind, solar and carbon intensity
  for the next 48 hours.
- **Map:** every grid-connected power station as a circle sized by what it's
  generating and coloured by fuel, plus the interconnectors to Europe and
  Ireland with their live flows. Zoom, pan, full screen, and a 24-hour history
  for any station. A time machine replays the last seven days in 10-minute steps.
- **Stations:** the same data as tables. Output by nation and by fuel, every
  station with its capacity and how hard it's running, the interconnectors, and
  any units that couldn't be placed on the map.
- **Regions:** NESO's 14 regions coloured by carbon intensity, from 24 hours
  ago to 48 hours ahead, with each region's generation mix and solar output.
- **Balancing:** who the grid operator is paying to switch off or turn up right
  now, what each half hour of balancing cost today and yesterday, the units paid
  the most, and daily constraint costs.
- **Outages:** spare capacity over the next 12 hours, the big units that are
  offline and when they're due back, the nuclear fleet, and NESO's grid notices.
  A banner appears on every page while a margin notice or similar warning is in
  force.
- **History:** the generation mix, carbon intensity and wholesale price since
  2009 (prices since 2018), zoomable down to the half hour, plus records, the
  latest day against a year earlier, and each year's mix.
- **Plain English throughout:** grid jargon in the explanations is underlined;
  hover, tap or tab to it for a short definition, or read them all on the
  Glossary page (`/glossary`). The Overview opens with **Today on the grid**, a
  summary of the day so far (or yesterday) written from the data: the mix,
  carbon, demand, prices, peaks, imports, balancing costs and any notices,
  led by whatever was most notable.
- **Display:** a kiosk view for a tablet or spare screen, opened from the
  Display button in the header. Each slide is designed to fit one screen:
  right now, the last 24 hours, the station map, regions, balancing, the next
  48 hours, spare capacity and outages, and then and now (once the history has
  downloaded). Tap to pause, swipe or use the arrow keys to move on, and it
  dims to a clock overnight. On an iPad, add it to the home screen for a
  full-screen app; it keeps the screen awake where the browser allows.

  Options go in the address: `/display?views=now,day,map,regions,balancing,outlook,outages,records`
  (which slides, in order), `every=20` (seconds per slide), `night=23-7` (dim
  hours, UK time, or `night=off`) and `theme=dark` or `theme=light`.

![The map page](docs/map.png)

## Quick start

```sh
mkdir gb-grid && cd gb-grid
curl -fsSLO https://raw.githubusercontent.com/rorymarland/gb-grid/main/docker-compose.yml
curl -fsSL -o .env https://raw.githubusercontent.com/rorymarland/gb-grid/main/.env.example  # optional
docker compose up -d
```

Then open `http://<host>:8080`. Live data appears within a minute, and history
downloads in the background (a couple of minutes for 30 days).

On NAS and Docker front ends (Synology Container Manager, Portainer, TrueNAS and
similar), create a project or stack from `docker-compose.yml` and put `.env`
alongside it if you want to change any settings.

### Image tags

Images are published for `linux/amd64` and `linux/arm64` to
`ghcr.io/rorymarland/gb-grid`.

| Tag | What it follows |
|---|---|
| `1` | The latest 1.x release (recommended) |
| `1.0.0` | That exact release |
| `latest` | The main branch |

To update: `docker compose pull && docker compose up -d`.

## Configuration

Settings are environment variables, normally set in `.env`. Every one has a
default; [`.env.example`](.env.example) explains each in more detail.

| Variable | Default | What it does |
|---|---|---|
| `GRID_PORT` | `8080` | Port on the host for the dashboard |
| `TZ` | `Europe/London` | Time zone for log timestamps (the pages always show UK time) |
| `CARBON_REGION_ID` | `0` | NESO region for the regional carbon view (1 to 17), or 0 for none |
| `HISTORY_DAYS` | `7` | Days of history to download and keep (2 to 60) |
| `BROWSER_REFRESH_SECONDS` | `10` | How often open pages check for new data |
| `LONG_HISTORY` | `true` | Download NESO's record since 2009 for the History page (about 20 MB) |
| `POLL_*_SECONDS` | varies | How often each upstream source is polled |
| `LOG_LEVEL` | `INFO` | `DEBUG` logs every poll |
| `ACCESS_LOG` | `false` | Log every HTTP request |

Data is kept in a SQLite database in the `/data` volume. Everything in it can be
downloaded again, so deleting the volume only costs the history download on
the next start. The container runs as UID 10001; if you bind-mount a host
folder instead of using a named volume, make it writable by that user.

### Behind a reverse proxy

Any reverse proxy works (Caddy, Traefik, Nginx, Nginx Proxy Manager and so on).
Proxy a whole host name, such as `grid.example.com`, to the container over plain
HTTP; serving it under a sub-path such as `/grid/` isn't supported. No websockets
are needed.

### DNS blocklists

Some DNS blocklists used with Pi-hole or AdGuard Home block whole top-level
domains, including `.energy`, which NESO uses for `api.neso.energy`. The
embedded wind and solar feeds then fail with "Connection refused", and the
data feeds section at the bottom of the overview says DNS is blocking them.
Allowlist `api.neso.energy` to fix it.

## How the numbers are made

- **Generation by fuel** comes from Elexon's five-minute FUELINST data for
  transmission-connected generation, plus estimates of solar (Sheffield Solar
  PV_Live) and smaller wind farms (NESO) connected to local networks, so the
  totals cover what Britain actually uses.
- **Carbon intensity** comes from NESO's Carbon Intensity API. Regional figures
  describe the electricity used in that region, following power flows between
  regions, so a region that makes more wind, nuclear and hydro power than it
  uses (often the Scottish ones) can sit at 0 g/kWh for days.
- **Power stations** on the map are modelled, because there's no public live
  meter reading per station. Each unit's output comes from its Physical
  Notification (what it told the grid operator it would generate), overridden
  by any Bid-Offer Acceptances (the operator's instructions to change that).
  Each fuel's estimates are then scaled so they add up to Elexon's measured
  total for the same five minutes. Interconnector flows are measured.
- **Locations** come from the Power Station Dictionary plus hand-added recent
  stations. Units that can't be matched still count in the totals; they're
  listed on the Stations page and logged once a day.
- **Northern Ireland** is part of the all-island grid with Ireland rather than
  the GB grid, so it only appears through the Moyle interconnector.

## API

| Endpoint | Returns |
|---|---|
| `/api/live` | The current snapshot behind the overview |
| `/api/history?range=24h` | History series (`24h`, `7d` or `30d`) |
| `/api/map` | Stations, interconnectors and totals for the map |
| `/api/station?id=N` | One station's last 24 hours |
| `/api/replay` | The last seven days of the map in 10-minute frames |
| `/api/regions` | Every region's carbon intensity, mix and solar, 24 hours back to 48 ahead |
| `/api/balancing` | Live instructions, half-hourly costs, top payments and constraint costs |
| `/api/outages` | Margin forecast, offline units, nuclear fleet, upcoming outages and notices |
| `/api/alerts` | Grid notices in force and the margin summary (drives the banner) |
| `/api/longterm?from=&to=` | Mix, carbon and price between two Unix times, averaged to suit the span |
| `/api/longterm/summary` | Records, the year-apart comparison and yearly mix |
| `/api/outlook` | Demand, wind, solar and carbon forecasts for the next 48 hours |
| `/api/summary?day=today` | Today on the grid (or `yesterday`), as text parts with glossary terms marked |
| `/api/status` | 200 when generation data is fresh, otherwise 503 (handy for uptime monitors) |
| `/healthz` | 200 when the server is running |

## Development

Python 3.11 or newer, with no packages to install. `test/mock_upstream.py`
serves fake versions of every upstream API, so you can work offline:

```sh
python3 test/mock_upstream.py 9101 app &
DATA_DIR=./data CARBON_REGION_ID=2 \
ELEXON_BASE_URL=http://127.0.0.1:9101/bmrs/api/v1 \
CARBON_BASE_URL=http://127.0.0.1:9101/carbon \
PVLIVE_BASE_URL=http://127.0.0.1:9101/pvlive/api/v4 \
NESO_BASE_URL=http://127.0.0.1:9101/neso \
GRID_INTERNAL_PORT=8080 python3 app/server.py
```

Add `UI_RELOAD=1` to reread the pages and static files on every request while
you edit them.

Run the tests with `python3 test/test_units.py`, `python3 test/test_time.py`,
`python3 test/test_edges.py` and `python3 test/test_features.py`.

### How the code is laid out

| Where | What |
|---|---|
| `app/server.py` | Starts the dashboard |
| `app/gridlive/` | The server: `config`, `fetch` and `store`; one module per feed (`ingest`, `units`, `balancing`, `outages`, `longterm`, `notices`); the API payloads (`grid` and `views_*`, including `views_summary` for Today on the grid); the HTTP server (`web`) |
| `app/*.html` | Short page shells; the server fills in the shared head, header and credits |
| `app/static/js/` | Shared ES modules: `gb.js` (DOM, formatting, tooltips, polling, theme, tabs and warning banner), `glossary.js` (the terms and their popovers), `summary.js`, `charts.js`, `stationmap.js`, `regionmap.js`, `outlook.js` |
| `app/static/pages/` | Each page's own script and styles |
| `app/static/vendor/` | uPlot, served from the image |

No build step: the browser loads the modules directly, and an import map in
each page adds a version to every module's address so they can be cached. Pushes to `main` and version tags (`v1.2.3`) run
the tests, smoke-test the image and publish it.

To make a release, add its notes to [CHANGELOG.md](CHANGELOG.md), then create a
GitHub release with a new `vX.Y.Z` tag on `main`. A fork publishes to its own
`ghcr.io/<owner>/gb-grid`, which GitHub creates as private: make it public in the
package's settings to pull it without logging in.

### Rebuilding the station list and map outlines

`app/stations.csv` and `app/geo.json` are generated by the scripts in `tools/`:

```sh
git clone --depth 1 https://github.com/OSUKED/Power-Station-Dictionary /tmp/psd
curl -fsSL -o /tmp/units.geojson \
  https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_admin_0_map_units.geojson
python3 -I tools/build_geo.py /tmp/units.geojson
python3 -I tools/build_stations.py /tmp/psd/data /tmp/units.geojson
```

To add a missing station, add it to `MANUAL` in `tools/build_stations.py` with
its BM unit IDs, ID prefixes or a pattern matching its registered name, then
rebuild.

## Credits and licences

- Contains BMRS data © Elexon Limited copyright and database right 2026
  ([licence](https://www.elexon.co.uk/data/balancing-mechanism-reporting-agent/copyright-licence-bmrs-data/)).
- Supported by National Energy SO Open Data
  ([licence](https://www.neso.energy/data-portal/neso-open-licence)): carbon
  intensity from the [NESO Carbon Intensity API](https://carbonintensity.org.uk/),
  and embedded generation, region boundaries, constraint costs and the historic
  generation mix from the [NESO Data Portal](https://www.neso.energy/data-portal).
- Solar estimates from [Sheffield Solar PV_Live](https://www.solar.sheffield.ac.uk/pvlive/).
- Station locations derived from the
  [Power Station Dictionary](https://github.com/OSUKED/Power-Station-Dictionary) (MIT).
- Map outlines from [Natural Earth](https://www.naturalearthdata.com/) (public domain).
- Charts on the History page use [uPlot](https://github.com/leeoniya/uPlot) (MIT),
  served from the image rather than a CDN.
- Inspired by Kate Morley's [National Grid: Live](https://grid.iamkate.com/).

Live data is fetched from each source's public API and is subject to its terms.
This project isn't affiliated with or endorsed by Elexon or NESO.

The code is released under the [MIT licence](LICENSE).
