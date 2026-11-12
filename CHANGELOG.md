# Changelog

Notable changes in each release. Version numbers follow
[semantic versioning](https://semver.org/), so the `1` image tag only moves to
releases that keep working with your existing settings and data.

## 1.7.0 (unreleased)

- **Your home on the Display:** a new slide after Right now, with what your
  home is drawing, the last hour of readings, today so far, yesterday and the
  last 30 days. It's in the default slide list and drops out by itself when
  Octopus isn't set up; with a `views=` list, add `home` where you want it.
  While a Display shows it, live readings are fetched every two minutes
  (about 32 requests an hour to Octopus).
- **Longer live chart** on Your home: the last hour by default, or 6 or 24
  hours. The server now keeps a day of the Home Mini's 10-second readings, so
  the longer views cost no extra requests, survive a restart, and each live
  request only asks for readings since the last one.
- **What other tariffs would have cost** on Your home: your last 12 months
  of half-hourly use priced on Flexible Octopus, Agile Octopus, Octopus Go and
  today's Octopus 12M Fixed for your region, against what you paid, with
  each month's difference. Prices come from Octopus's public price list once
  a day, without your key.
- **Saving Sessions** on Your home: your Octoplus points, upcoming sessions
  with your baseline worked out Octopus's way, a reminder under the live
  figure the day before, and how you did in recent sessions (points Octopus
  gave, or an estimate until it does). `--test-octopus` checks these too.
- Tariff prices for your own tariff are now fetched without the key, as
  they're public; only meter readings and your account's data use it.
- **Fixed:** the last 30 days chart's axis now gives its unit (kg or kWh, as
  well as £), and so does today's.

## 1.6.0 (2026-11-11)

- **Your home** (`/home`, optional): your own electricity and gas from an
  Octopus Energy smart meter. What your home is drawing now from an Octopus
  Home Mini, and its CO₂ an hour at the grid's current carbon intensity;
  today's half hours coloured by how clean the grid was, with the forecast
  for the rest of the day; carbon, energy or cost for the last 30 days; how
  your timing compares with steady use; and month by month for a year. A year
  of half-hourly readings is downloaded on the first start. Off unless
  `OCTOPUS_API_KEY` (or `OCTOPUS_API_KEY_FILE`, for a Docker secret) and
  `OCTOPUS_ACCOUNT` are set; `python /app/server.py --test-octopus` checks
  them. The key is only ever sent to Octopus, and the server keeps well
  inside Octopus's limit of about 100 requests an hour.
- New API: `/api/home`. New settings: `OCTOPUS_API_KEY`, `OCTOPUS_API_KEY_FILE`,
  `OCTOPUS_ACCOUNT`, `OCTOPUS_LIVE_SECONDS`, `GAS_CALORIFIC_VALUE` and
  `GAS_KG_CO2_PER_KWH`.

## 1.5.0 (2026-11-10)

- **Station pages** (`/station?id=<id>`): each station's output now against
  its capacity, the last 24 hours or 7 days with energy generated, capacity
  factor, highest output and time running, its generating units one by one,
  its outages and the frequency events they set off, and what's known about
  it (year opened, owner and operator from Wikidata, turbines from the
  Renewable Energy Planning Database, a Wikipedia link, the nearest other
  stations). Reached from a station's card, from outages on the Timeline,
  and from search, which now finds power stations by name.
- New API: `/api/station?id=N&full=1` and `/api/stations`.
- **Notifications.** New timeline events can be pushed to your phone through
  [ntfy](https://ntfy.sh/) (`NOTIFY_NTFY_URL`) or to a webhook such as Home
  Assistant (`NOTIFY_WEBHOOK_URL`). Major events only by default;
  `NOTIFY_LEVEL`, `NOTIFY_KINDS` and `NOTIFY_QUIET_HOURS` tune it, and
  `PUBLIC_URL` makes each one link to its event. Off unless an address is
  set. `python /app/server.py --test-notification` sends a test, and
  `/api/status` reports what has been sent.
- **More station photos:** 131 stations now have one (was 87), covering 65%
  of capacity, including 75 wind farms (was 36). The new ones were chosen by
  eye from Wikimedia Commons, mostly Geograph photographs.
- **Fixed:** another 67 stations moved to better locations, 29 of them by
  more than 5 km. Fellside was on the Solent rather than at Sellafield, Fort
  Dunlop shared its coordinates, and Baillie, Carraig Gheal, Beinn Tharsuinn
  and Berry Burn wind farms were 30 km or more out. Onshore stations Wikidata
  doesn't list are now placed by the government's Renewable Energy Planning
  Database, or by the site they stand on; 214 of 246 stations no longer rely
  on the Power Station Dictionary's coordinates.
- **Power station names link to their pages** wherever they're listed: on
  Outages, Balancing and the Stations table.
- **Data sources** now sit at the foot of every page as one line ("Data
  sources: All 9 working", or the feed with a problem named), opening to the
  full list. They used to be at the bottom of the Overview only.
- **Outages on phones:** the tables of units offline, upcoming outages and the
  nuclear fleet no longer run off the side of the screen; each unit becomes a
  short block with what's out, when it's due back and why.
- **Map on phones:** fewer labels until you zoom in, so place and station
  names no longer pile on top of each other.
- **Balancing:** the figures are open rather than boxed, as on the Overview,
  and when nothing is being instructed the empty map is replaced by a line
  saying so.
- **Colours easier to tell apart**, including with colour-blindness: hydro is
  a deeper blue, biomass a slightly brighter green, and "other" a plain grey,
  so wind, hydro and storage no longer blur together. The Stations page
  heading matches the other pages.
- **Wide screens** (1560 px and up) give charts and maps more room; text keeps
  its reading width.
- Station locations are checked two new ways: against the map's coastline
  (in the tests), and against where each station's photo was taken (when the
  photo list is rebuilt).

## 1.4.0 (2026-11-10)

- **Station cards.** Select a station on the map, or a row on the Stations
  page, for its photo from Wikimedia Commons, credited to its photographer
  with its licence (87 stations so far, covering 57% of capacity; every photo
  was checked by eye), or a small locator map where there's no photo yet. The
  card also gives the station's GPS coordinates (with a copy button), its
  Ordnance Survey grid reference, and links to open it in Google Maps, Apple
  Maps, OpenStreetMap or Wikidata. The server fetches each photo once and
  keeps it in `/data/photos`, so browsers never contact Wikimedia;
  `STATION_PHOTOS=false` turns photos off. `/map?station=<id>` and
  `/stations?station=<id>` open a station's card directly.
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
- New API endpoints: `/api/frequency`, `/api/events`, `/api/sandbox` and
  `/api/photo`. New settings: `POLL_RESPONSE_SECONDS` (default 3600),
  `POLL_EVENTS_SECONDS` (120) and `STATION_PHOTOS` (true).
- **A new look and easier navigation.** Pages are grouped (Now, Running the
  grid, Over time, Explore) in a bar that stays pinned as you scroll; on
  phones they're in a Menu with a line on what each page shows, instead of a
  sideways-scrolling strip. Search pages and glossary terms with `/` or
  `⌘K`/`Ctrl+K`. A site map in the footer and a skip-to-content link.
  Sections are open rather than boxed, with Hanken Grotesk for text and
  figures and Instrument Sans for headings, both served locally.
- **Light by default.** The theme button now offers Light, Dark or Match my
  device (it used to follow the device unless changed). Choices made before
  this version aren't carried over.
- **Fixed:** 120 stations sat in the wrong place on the map, 60 of them by
  more than 5 km (Baglan Bay was in Pembrokeshire, Torness out in the North
  Sea, Dinorwig outside its mountain). Locations now come from Wikidata
  wherever a station is matched there, which is 140 of 246 stations; the
  rest still use the Power Station Dictionary's.
- **Fixed:** the map's fuel list could blank the map. Tapping a fuel on an
  iPad left it highlighted, and clicking to hide one on a Mac kept
  highlighting the fuel just hidden, so every other station faded out. Now a
  click or tap only shows or hides; hovering with a mouse previews that fuel
  on its own, showing it even while it's hidden; and **Show all** and **Hide
  all** sit above the list (Option-click shows one fuel alone).
- **Fixed:** on an iPad, the map's full screen closed as soon as you dragged
  the map downwards, because iPad Safari's full screen claims that swipe for
  itself. iPads now fill the browser window instead, as iPhones always have.
- **Fixed:** on phones, dragging the map with one finger could zoom and jump
  instead of moving it. If a finger's lift went unreported (as Safari can when
  the map redraws under it), the next single touch was treated as a pinch
  against the missing finger; and lifting one finger out of a pinch made the
  map leap. One finger now always pans; pinch, double-tap, or double-tap and
  slide down or up zoom, as in Google and Apple Maps. The map's hint shows the
  touch gestures on touch screens.
- **Fixed:** at some desktop widths the page tabs overlapped the live status.
- **Fixed:** the Display's 24-hour chart cut off its time labels when the
  slide settled after the first draw; it now redraws when its box changes size.
- The image now runs Python 3.14 (was 3.13, which only gets security fixes
  now).
- **Security:** images are rebuilt weekly on a fresh base image (for `latest`
  and the latest release's tags) and scanned with Trivy before publishing;
  Alpine packages are upgraded at build time and pip is removed from the
  image. The server closes connections idle for 30 seconds and accepts at
  most 100 at once. GitHub Actions are pinned to commit SHAs, updated to their
  current major versions, and kept current by Dependabot.
- README screenshots and feature notes brought up to date.
- The glossary has grown from 41 terms to 48.

## 1.3.0 (2026-11-07)

- **Plain English layer:** grid terms in the explanations are underlined and
  open a short definition; a new Glossary page (`/glossary`) lists all 41.
- **Today on the grid:** the Overview opens with a written summary of the day
  so far or yesterday, led by the most notable event (negative prices, a
  notice, the cleanest day of the week, heavy curtailment). The Display view
  has it as a slide too, so the default slide list is now
  `now,story,day,map,regions,balancing,outlook,outages,records`.

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
