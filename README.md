# MotoPark SG Bot

Telegram bot for finding motorcycle parking in Singapore: live availability
(via LTA DataMall) plus free/paid info (via data.gov.sg's HDB Carpark
Information dataset), extended with URA's motorcycle-capacity dataset (more
carparks) and the Carpark Rates dataset (malls/hotels/attractions, price
reference) — the combination none of the existing apps seem to do.

Each result is streamlined to just four things: address, paid/free,
distance (when known), and availability — availability is always shown as
a state (✅ available / 🔴 full / ❓ no live data), never a raw lot count or
bay-capacity number.

**Commands:**
- `/check <name or postal code>` — look up a carpark by name, e.g.
  `/check jurong point`, or by a 6-digit postal code, e.g. `/check 238801`
  (geocoded via OneMap, then searched like `/nearest` from that point —
  see Setup below to enable this)
- `/nearest` — then share a location pin, to see the closest carparks

A name/mall search (`/check jurong point`, `/check ang mo kio`) treats
multi-word queries as one phrase — an address has to contain the whole
sequence of words together, not just each word somewhere in it.

Every result comes with a **🧭 Navigate** button that opens turn-by-turn
directions to that carpark in Google Maps.

## How it works

- `static_data.py` fetches and caches (24h TTL) the HDB Carpark Information
  dataset from data.gov.sg — no API key needed, it's a public dataset. This
  gives shelter type (`car_park_type`), free/paid (`free_parking`), night
  parking, and coordinates (converted from SVY21 to lat/lon in `geo.py`).
- `lta_client.py` polls LTA DataMall's `CarParkAvailabilityv2` for live lot
  counts, filtered to motorcycle lots, cached with a 45s TTL. It joins to
  HDB by exact `CarParkID`, and also exposes
  `find_by_development_name()` — a fuzzy text match against live
  `Development` names, used for sources with no ID to join on (Carpark
  Rates, below).
- `datagovsg.py` is a shared fetch helper for data.gov.sg's modern
  `poll-download` API (used for GeoJSON/CSV file-based datasets, as
  opposed to the older `datastore_search` API `static_data.py` uses). It
  handles two GeoJSON shapes defensively: clean top-level `properties`,
  or the legacy ArcGIS-export shape where the real fields are packed into
  an HTML `<table>` inside a `Description` property. It also retries with
  backoff on a `429 Too Many Requests` from the poll-download endpoint
  (confirmed in production — see the URA diagnostic note below), honoring
  a `Retry-After` header when the server sends one.
- `ura_data.py` fetches and caches URA's "Capacity of URA Parking Places"
  dataset (name, location, and motorcycle/car/heavy-vehicle bay counts,
  one row per carpark facility — confirmed against real production data),
  extending both `/check` and `/nearest` with carparks outside the HDB
  dataset. It used to join this against a second "URA Parking Lot"
  dataset too, until production logs showed that one is actually a
  ~38,700-feature per-*lot* Polygon layer, not per-facility points —
  parsed to 0 usable records every time, since only Point geometries are
  kept. Since the Capacity dataset alone already has everything needed,
  the join was dropped. See the README history / `ura_data.py`'s module
  docstring for the full story.
- `carpark_rates_data.py` fetches and caches the Carpark Rates dataset
  (malls, hotels, attractions — parking price reference, no coordinates,
  no vehicle-type breakdown). It extends `/check` only (nothing to rank
  by distance without coordinates), with live lot counts attempted via
  `lta_client.find_by_development_name()` fuzzy-matching.
- `matching.py` and `nearest.py` are written generically (Python
  `Protocol`/`TypeVar`) so the same ranking logic works across all three
  static-data types without duplication. `matching.py` treats a query as
  one phrase, not a bag of independently-scored words: a multi-word query
  only matches an address whose (normalized) text contains that whole
  sequence of words together, not one that merely contains each word
  somewhere unrelated.
- `onemap.py` geocodes a 6-digit postal code to `(lat, lon)` via OneMap
  Singapore's Search API, for `/check`'s postal-code support. OneMap now
  requires a free registered account — `OneMapClient` logs in with
  email+password (`POST /api/auth/post/getToken`) and caches the resulting
  bearer token (documented ~72h validity) rather than re-authenticating on
  every lookup. Optional: if `ONEMAP_EMAIL`/`ONEMAP_PASSWORD` aren't set, a
  postal-code query just gets a "not set up" reply instead of results —
  everything else in the bot is unaffected.
- `responses.py` merges results from all applicable sources per command,
  joins each against live data where possible, and formats the combined
  reply — pulled out of `bot.py` so this logic (the most complex part of
  the bot now) is unit-testable without simulating Telegram objects. It
  returns a `BotReply(text, nav_targets)`, not a bare string: `nav_targets`
  is one `NavTarget(label, lat, lon)` per result that has coordinates
  (everything except Carpark Rates entries, which have none). `/check`
  with a bare postal code is handled here too: geocode via `onemap.py`,
  then delegate entirely to the same nearest-search logic `/nearest` uses.
- `maps.py` turns a `(lat, lon)` pair into a Google Maps directions URL
  (`https://www.google.com/maps/dir/?api=1&destination=...`) — no API key
  needed, opens the Google Maps app if installed or the website otherwise,
  on any platform. `bot.py`'s `build_nav_keyboard()` turns each reply's
  `nav_targets` into one inline "🧭 Navigate" button per result (labels
  truncated to fit Telegram's 64-character button-text limit).
- `health.py` runs a tiny HTTP endpoint, but *only* when `RENDER=true` (or
  `$PORT`) is set — true on Render, false everywhere else this README
  covers. Render doesn't auto-inject `$PORT` for a custom start command,
  it just expects port 10000 by default, so that's what this defaults to.
  See "Deploy to Render" below for why it exists.

**Resilience:** `responses.py`'s `_safe()` wraps every call to the URA,
Carpark Rates, and live-data stores — if one of them is broken (bad
dataset schema, LTA outage, whatever), that source is silently dropped
from the reply (with a `WARNING`-level log line) instead of the entire
`/check` or `/nearest` reply failing. The stores themselves also back off
after a failed fetch (`retry_backoff_seconds`, default 5 min) so a
persistently broken source doesn't re-hit the network on every message.
HDB (`static_data.py`) is the one source that's *expected* to always work
— if it's down at startup the bot refuses to start (see `run_bot()` in
`bot.py`), but a transient failure mid-session is still caught the same
way as the others rather than taking down every reply.

## ⚠️ Things to verify once you have a real API key

I built this against documented schemas, but couldn't test live calls
myself (no DataMall account, and this sandbox's network doesn't reach
data.gov.sg/LTA anyway). These assumptions need a real check:

> **Update from a real deploy:** item 3 below (URA field names/shape) is
> now resolved — see its entry for what production data actually showed
> and how `ura_data.py` was fixed. While it was broken, the bot degraded
> gracefully rather than crashing (`/check`/`/nearest` fell back to HDB +
> Carpark Rates and logged a warning) — that resilience stays in place
> for whatever's still unverified below (items 1, 2, 4, 5).

1. **Which `LotType` code means motorcycle.** LTA's official API guide says
   `Y`, but I've seen a third-party source use `M` — I coded for both
   (`lta_client.MOTORCYCLE_LOT_TYPES = {"Y", "M"}`), but run the smoke test
   below to confirm what your actual responses use, and tighten that set if
   one of the two never appears.
2. **`car_park_no` / `CarParkID` actually match for the carparks you care
   about.** This is well-documented behavior for HDB-agency records, but
   worth spot-checking against a couple of carparks you know.

Run this after you have `LTA_ACCOUNT_KEY` set:

```bash
LTA_ACCOUNT_KEY=your_key_here python -m motopark_bot.lta_client
```

It prints every distinct `LotType` seen and how many records matched the
motorcycle guess — if that number looks wrong, fix `MOTORCYCLE_LOT_TYPES`
in `lta_client.py` before relying on it.

3. **URA dataset field names and GeoJSON shape — RESOLVED.** I couldn't
   fetch actual sample data while building this (the sandbox's `WebFetch`
   hit `403 PROXY_REJECTED` on data.gov.sg's presigned S3 download URLs),
   so it was built against documented field names only, joining two
   datasets. In production, that join failed outright
   (`Fetched 0 usable URA carparks`), and the diagnostic added to debug it
   (`ura_data.log_raw_feature_sample()`, logged to Render's free **Logs**
   tab — Shell is a paid Render feature, so this was the only option)
   revealed two things once its own rate-limiting problem was fixed (see
   the 429 note below):
   - The **Capacity** dataset (`d_9bf8620ecfdc8a5f8f77e3f02160af5c`) was
     exactly as documented: one `Point`-geometry feature per carpark
     facility, with `PP_CODE`, `PARKING_PL` (name), and
     `NO_CAR`/`NO_H_VEHIC`/`NO_MCYCLE` — confirmed correct.
   - The **Parking Lot** dataset (`d_d959102fa76d58f2de276bfbb7e8f68e`)
     was not what the name suggested: ~38,700 `Polygon`-geometry
     features, one per individual physical parking *lot* (tagged
     `TYPE`, e.g. `"Motorcycle Lots"`), not one point per facility.
     `datagovsg.parse_geojson()` only keeps `Point` geometries, so every
     one of those features was silently dropped — that's why the join
     produced 0 usable carparks.

   Since the Capacity dataset alone already has everything this bot
   needs, `ura_data.py` no longer joins against Parking Lot at all — it's
   simpler (one dataset fetch instead of two) and it works. See
   `ura_data.py`'s module docstring for the full detail, and
   `tests/fixtures/sample_ura_parking_lot.geojson.json` for a verbatim
   copy of the real (Polygon) shape that caused this, kept as a
   regression fixture.

   **Also found along the way — data.gov.sg's poll-download endpoint
   rate-limits (429) rapid successive calls.** A single startup can fire
   several calls to it back-to-back (2 for the old two-dataset join, plus
   2 more from the diagnostic), which was enough to trip it — so the
   diagnostic itself failed on the first attempt. Fixed with
   retry-with-backoff in `datagovsg.fetch_download_url()` (honors
   `Retry-After` when the server sends one) plus a short stagger between
   calls; if `429` ever shows up in the logs again, that's what it means,
   not a new bug.
4. **Whether URA's `PP_CODE` ever matches an LTA `CarParkID`.** Still
   unverified, same class of assumption as #2 but for the URA/LTA-agency
   pairing instead of HDB — if it never matches, `/check` and `/nearest`
   results for URA carparks will always show capacity instead of a live
   count (not broken, just less precise; see
   `formatting.format_ura_carpark`'s fallback).
5. **Carpark Rates dataset.** Same S3-fetch limitation as URA. Run:

   ```bash
   python -m motopark_bot.carpark_rates_data
   ```

   to confirm rows parse as expected. Also worth knowing going in: this
   dataset (as of when it was last checked) is itself stale — roughly
   2018-era rates — so treat `/check` results from it as a rough price
   reference, not current pricing.
6. **OneMap's Search API response shape — not yet verified against a live
   account.** Built strictly from OneMap's own documented field names
   (`access_token` from the login endpoint; `results`, `LATITUDE`,
   `LONGITUDE` from the search endpoint) — this sandbox has no
   `ONEMAP_EMAIL`/`ONEMAP_PASSWORD` and no network access to confirm them
   live. A few years ago the equivalent endpoint was fully public with no
   signup at all, which is why older blog posts/tutorials describe a
   different (keyless) API — the current one requires registering a free
   account and sending a bearer token on every search call. Run this once
   you've registered:

   ```bash
   ONEMAP_EMAIL=you@example.com ONEMAP_PASSWORD=yourpassword \
     python -m motopark_bot.onemap [postal_code]
   ```

   It logs in, geocodes the given postal code (defaults to `238801`, ION
   Orchard), and prints the resulting `(lat, lon)` — if it errors instead,
   the field names in `onemap.py`'s `_get_token`/`geocode_postal_code`
   need adjusting to match what OneMap actually returns.

## Setup

### 1. Get an LTA DataMall AccountKey (free)

1. Go to https://datamall.lta.gov.sg/content/datamall/en/request-for-api.html
2. Fill in the form (name, email, contact no.; pick "Mobile App" or
   "Others" for purpose — a personal Telegram bot fits either), accept the
   Singapore Open Data Licence and Terms of Service.
3. LTA emails you an AccountKey after registering — that's your
   `LTA_ACCOUNT_KEY`.

### 2. Create the Telegram bot

1. Message [@BotFather](https://t.me/BotFather) on Telegram.
2. `/newbot`, follow the prompts, and copy the token it gives you — that's
   your `TELEGRAM_BOT_TOKEN`.

### 3. (Optional) register a free OneMap account, to enable postal-code search

Skip this and `/check` still works fine by name/mall — a 6-digit postal
code query just gets a "not set up" reply instead of results.

1. Go to https://www.onemap.gov.sg and click **Register** (top-right) —
   free, just an email and password.
2. Confirm the account (email verification), then that email/password pair
   is your `ONEMAP_EMAIL`/`ONEMAP_PASSWORD`.
3. See verification item 6 above for a smoke test to confirm it works
   before relying on it in production.

### 4. (Optional) run the test suite locally

The tests use fixture data, not live network calls, so they run without
either API key — useful as a sanity check before you deploy, without
actually running the bot itself anywhere:

```bash
pip install -r requirements-dev.txt
pytest
```

### 5. Push to GitHub

Render (like Railway) deploys from a GitHub repo, not an upload:

```bash
git init
git add .
git commit -m "Initial commit"
git remote add origin https://github.com/<your-username>/motopark-bot.git
git branch -M main
git push -u origin main
```

### 6. Deploy to Render (free)

Render's free tier only offers *Web Services* (not background workers),
and free web services spin down after 15 minutes without an HTTP request.
This bot doesn't naturally receive HTTP traffic — it long-polls Telegram —
so `health.py` adds a decoy HTTP endpoint Render can health-check, and
you'll pair it with an external pinger (step 6) to keep it awake. This is
a workaround for the free tier, not how you'd run it on a platform with a
real background-worker plan.

1. In Render: **New +** → **Blueprint**, connect your GitHub repo. Render
   reads `render.yaml` and pre-fills everything (free plan, Singapore
   region, build/start commands) — you just need to paste in the env var
   values (`TELEGRAM_BOT_TOKEN`, `LTA_ACCOUNT_KEY` required;
   `ONEMAP_EMAIL`/`ONEMAP_PASSWORD` optional, only if you did step 3) when
   prompted.
   - No `render.yaml` support, or prefer doing it by hand? **New +** →
     **Web Service** instead, pick the repo, set Build Command to
     `pip install -r requirements.txt` and Start Command to
     `python -m motopark_bot.main`, plan **Free**, then add the same env
     vars under the Environment tab.
2. Deploy. Check the logs for `Health check server listening on
   0.0.0.0:10000` (confirms the decoy endpoint is up) followed by
   `Starting polling...` (confirms the actual bot is running).
3. Render gives the service a public URL (something like
   `https://motopark-bot-xxxx.onrender.com`) — you won't use this for
   anything in Telegram, it only matters for step 7.

### 7. Keep it awake with a free external pinger

Without this, Render puts the service to sleep after 15 minutes of no HTTP
traffic, and since nothing calls it over HTTP under normal use, it would
just stay asleep.

1. Sign up free at [UptimeRobot](https://uptimerobot.com) (or
   [cron-job.org](https://cron-job.org), same idea).
2. Add a new monitor: type **HTTP(s)**, URL = your Render service's URL
   plus `/health` (e.g. `https://motopark-bot-xxxx.onrender.com/health`),
   interval = **5 minutes** (comfortably under Render's 15-minute
   spin-down window).
3. Save. UptimeRobot will now hit that URL every 5 minutes forever, which
   is indistinguishable from real traffic as far as Render's spin-down
   logic is concerned.

Worth knowing: this is a genuinely free way to keep it running, but it's a
workaround, not a guarantee — if UptimeRobot has an outage, or Render
changes free-tier behavior, the bot could go quiet until the next ping
wakes it. For a personal utility bot that's a reasonable trade for $0/mo;
if it ever needs to be reliable for other people, that's the point to
move to a paid background-worker plan (Render Pro, Railway Hobby, or a
self-managed box like Oracle Cloud's Always Free tier).

## Project layout

```
motopark_bot/
  config.py            env var loading (get_settings())
  geo.py                SVY21->WGS84 conversion, haversine distance
  static_data.py        HDB Carpark Information fetch/cache (free/paid)
  lta_client.py         LTA DataMall live motorcycle-lot polling/cache
  datagovsg.py          shared poll-download fetch helper (GeoJSON/CSV datasets)
  ura_data.py           URA motorcycle-capacity dataset fetch/cache
  carpark_rates_data.py Carpark Rates (malls/hotels/attractions) fetch/cache
  matching.py           generic text search ranking for /check (whole-phrase match)
  nearest.py            generic distance-based ranking for /nearest
  onemap.py              postal code -> (lat, lon) via OneMap, for /check
  formatting.py         Telegram message formatting, one formatter per source
  responses.py          merges all sources into the /check and /nearest replies
  maps.py               (lat, lon) -> Google Maps directions URL, for nav buttons
  health.py             decoy HTTP endpoint, active only when $PORT is set (Render)
  bot.py                aiogram handlers (thin wiring onto responses.py)
  main.py               entrypoint
tests/            pytest suite (115 tests, run against fixture data — real
                  fixtures for HDB/LTA pulled from data.gov.sg, plus one
                  verbatim real URA sample (see verification section
                  above); synthetic fixtures for the rest — no network/API
                  keys needed either way)
render.yaml       Render Blueprint (free Web Service)
Procfile          worker-process declaration (Railway, or any Procfile-based host)
```

## Known limitations / ideas for v2

- Output is deliberately streamlined to four fields (address, paid/free,
  distance, availability state) — no shelter type, carpark code, night
  parking, or raw lot/capacity numbers are shown anymore, per explicit
  design choice. If you want those back for your own use, they're still in
  the underlying data (`CarparkInfo`/`UraCarpark`/`LiveLot`); only
  `formatting.py`'s output was trimmed.
- No caching layer shared across bot restarts (in-memory only) — fine for
  a single-instance personal bot, would need Redis/similar if you ever
  scale this to multiple workers.
- `/nearest` now covers HDB + URA carparks (URA's dataset extends coverage
  beyond HDB, per "How it works" above), but Carpark Rates entries never
  appear there — that dataset has no coordinates, so they only show up in
  `/check`.
- URA carparks have no pricing field in the dataset at all, so `/check`/
  `/nearest` results for them never show a paid/free line — only HDB and
  Carpark Rates entries do. When the live feed doesn't join to a URA
  carpark (see verification items #3–4 above), its availability line just
  shows "no live data" — there's no capacity-number fallback anymore.
- Carpark Rates entries rely on fuzzy name-matching against the live feed
  (no ID to join on) and the rates themselves may be stale — treat as a
  price reference, not a live-availability source.
- Postal-code search (`/check <postal code>`) depends on a third external
  service (OneMap) with its own account/credentials and unverified
  response shape (see verification item #6) — if it's down or
  misconfigured, that one feature degrades to a "not set up"/"couldn't
  find" message rather than breaking name-based `/check` or `/nearest`.
- No rate limiting on user commands — unlikely to matter for personal use,
  but worth adding if this ever gets shared publicly.
- The Render free-tier deploy depends on an external pinger staying up
  (see "Keep it awake" above) — it's not a guaranteed always-on setup the
  way a paid worker plan would be.
