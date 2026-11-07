# GB Grid

A self-hosted, live dashboard and power station map for the Great Britain
electricity grid. One small container, no API keys, and nothing but the Python
standard library inside.

![The overview page](docs/overview.png)

<sub>Screenshots use sample data.</sub>

## What it shows

- **Overview:** what Britain is using right now and where it comes from.
  Generation mix, demand, carbon intensity, wholesale price, grid frequency and
  interconnector flows, with history charts (24 hours, 7 days, 30 days) and the
  cleanest times to use electricity over the next day, for Great Britain or one
  of NESO's regions.
- **Map:** every grid-connected power station as a circle sized by what it's
  generating and coloured by fuel, plus the interconnectors to Europe and
  Ireland with their live flows. Zoom, pan, full screen, and a 24-hour history
  for any station.
- **Stations:** the same data as tables. Output by nation and by fuel, every
  station with its capacity and how hard it's running, the interconnectors, and
  any units that couldn't be placed on the map.

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

Run the tests with `python3 test/test_units.py`, `python3 test/test_time.py` and
`python3 test/test_edges.py`. Pushes to `main` and version tags (`v1.2.3`) run
the tests, smoke-test the image and publish it.

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
- Carbon intensity from the [NESO Carbon Intensity API](https://carbonintensity.org.uk/),
  and embedded generation from the [NESO Data Portal](https://www.neso.energy/data-portal).
- Solar estimates from [Sheffield Solar PV_Live](https://www.solar.sheffield.ac.uk/pvlive/).
- Station locations derived from the
  [Power Station Dictionary](https://github.com/OSUKED/Power-Station-Dictionary) (MIT).
- Map outlines from [Natural Earth](https://www.naturalearthdata.com/) (public domain).
- Inspired by Kate Morley's [National Grid: Live](https://grid.iamkate.com/).

Live data is fetched from each source's public API and is subject to its terms.
This project isn't affiliated with or endorsed by Elexon or NESO.

The code is released under the [MIT licence](LICENSE).
