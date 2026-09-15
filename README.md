# MotoPark SG Bot

Telegram bot for finding motorcycle parking in Singapore: live lot counts
(via LTA DataMall) plus sheltered/unsheltered and free/paid info (via
data.gov.sg's HDB Carpark Information dataset), extended with URA's
motorcycle-capacity dataset (more carparks, no live join always available)
and the Carpark Rates dataset (malls/hotels/attractions, price reference) —
the combination none of the existing apps seem to do.

**Commands:**
- `/check <name>` — look up a specific carpark by name, e.g. `/check jurong point`
- `/nearest` — then share a location pin, to see the closest carparks

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
- `ura_data.py` fetches and caches URA's Parking Lot (location) and
  Capacity (motorcycle/car/heavy-vehicle bay counts) datasets, joins them
  on `PP_CODE`, and extends both `/check` and `/nearest` with carparks
  outside the HDB dataset. See the verification caveat below — this one's
  built against documented schema only, not a real sample.
- `carpark_rates_data.py` fetches and caches the Carpark Rates dataset
  (malls, hotels, attractions — parking price reference, no coordinates,
  no vehicle-type breakdown). It extends `/check` only (nothing to rank
  by distance without coordinates), with live lot counts attempted via
  `lta_client.find_by_development_name()` fuzzy-matching.
- `matching.py` and `nearest.py` are written generically (Python
  `Protocol`/`TypeVar`) so the same ranking logic works across all three
  static-data types without duplication.
- `responses.py` merges results from all applicable sources per command,
  joins each against live data where possible, and formats the combined
  reply — pulled out of `bot.py` so this logic (the most complex part of
  the bot now) is unit-testable without simulating Telegram objects.
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

> **Update from a real deploy:** item 3 below (URA field names) is now
> confirmed wrong — production logs showed `Fetched 0 usable URA
> carparks`. This no longer breaks the bot: `/check` and `/nearest` fall
> back to HDB + Carpark Rates results and log a warning
> (`URA carpark data unavailable for this request...`) instead of
> crashing (see "Resilience" below for how). URA results just won't show
> up until the field names are fixed — see item 3 for how to diagnose it.

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

3. **URA dataset field names and GeoJSON shape.** I could not fetch actual
   sample data for URA's Parking Lot / Capacity datasets — the sandbox's
   `WebFetch` hit `403 PROXY_REJECTED` on the presigned S3 download URLs
   data.gov.sg's API returned. `ura_data.py` is built strictly against the
   *documented* field names (`PP_CODE`, `PARKING_PL`, `NO_MCYCLE`, etc.)
   and assumes WGS84 `[lon, lat]` coordinates per the GeoJSON spec — and
   this has now failed for real in production (`Fetched 0 usable URA
   carparks`).

   If you have Shell access (Render's paid plans, Railway, your own box),
   run this — it prints how many carparks loaded and a couple of sample
   records:

   ```bash
   python -m motopark_bot.ura_data
   ```

   **On Render's free tier there's no Shell tab**, so instead, whenever
   priming `ura_store` fails at startup, `bot.py` automatically calls
   `ura_data.log_raw_feature_sample()`, which logs the raw (unparsed)
   GeoJSON — feature count, geometry type, and the actual property keys —
   at `WARNING` level. Check Render's **Logs** tab (free) after a
   deploy/restart for lines starting `URA diagnostic [...]`. Whichever way
   you get it, that output is exactly what's needed to fix the field-name
   guesses in `ura_data.py`/`datagovsg.py` against the real dataset shape.

   **Heads up — data.gov.sg's poll-download endpoint rate-limits (429)
   rapid successive calls,** confirmed in production: a startup that
   already fired 2 calls (for `ura_store`'s two datasets), followed
   immediately by 2 more from this diagnostic, got a `429 Too Many
   Requests` on the diagnostic's own first call — so the very thing meant
   to explain the failure failed too. Fixed with a retry-with-backoff in
   `datagovsg.fetch_download_url()` plus a short stagger between calls
   (`ura_data._INTER_REQUEST_DELAY_SECONDS`), but if you ever see `429` in
   the logs again, that's what it means — not a new bug.
4. **Whether URA's `PP_CODE` ever matches an LTA `CarParkID`.** Unverified
   like #2, but for the URA/LTA-agency pairing instead of HDB — if it
   never matches, `/check` and `/nearest` results for URA carparks will
   always show capacity instead of a live count (not broken, just less
   precise; see `formatting.format_ura_carpark`'s fallback).
5. **Carpark Rates dataset.** Same S3-fetch limitation as URA. Run:

   ```bash
   python -m motopark_bot.carpark_rates_data
   ```

   to confirm rows parse as expected. Also worth knowing going in: this
   dataset (as of when it was last checked) is itself stale — roughly
   2018-era rates — so treat `/check` results from it as a rough price
   reference, not current pricing.

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

### 3. (Optional) run the test suite locally

The tests use fixture data, not live network calls, so they run without
either API key — useful as a sanity check before you deploy, without
actually running the bot itself anywhere:

```bash
pip install -r requirements-dev.txt
pytest
```

### 4. Push to GitHub

Render (like Railway) deploys from a GitHub repo, not an upload:

```bash
git init
git add .
git commit -m "Initial commit"
git remote add origin https://github.com/<your-username>/motopark-bot.git
git branch -M main
git push -u origin main
```

### 5. Deploy to Render (free)

Render's free tier only offers *Web Services* (not background workers),
and free web services spin down after 15 minutes without an HTTP request.
This bot doesn't naturally receive HTTP traffic — it long-polls Telegram —
so `health.py` adds a decoy HTTP endpoint Render can health-check, and
you'll pair it with an external pinger (step 6) to keep it awake. This is
a workaround for the free tier, not how you'd run it on a platform with a
real background-worker plan.

1. In Render: **New +** → **Blueprint**, connect your GitHub repo. Render
   reads `render.yaml` and pre-fills everything (free plan, Singapore
   region, build/start commands) — you just need to paste in the two env
   var values (`TELEGRAM_BOT_TOKEN`, `LTA_ACCOUNT_KEY`) when prompted.
   - No `render.yaml` support, or prefer doing it by hand? **New +** →
     **Web Service** instead, pick the repo, set Build Command to
     `pip install -r requirements.txt` and Start Command to
     `python -m motopark_bot.main`, plan **Free**, then add the two env
     vars under the Environment tab.
2. Deploy. Check the logs for `Health check server listening on
   0.0.0.0:10000` (confirms the decoy endpoint is up) followed by
   `Starting polling...` (confirms the actual bot is running).
3. Render gives the service a public URL (something like
   `https://motopark-bot-xxxx.onrender.com`) — you won't use this for
   anything in Telegram, it only matters for step 6.

### 6. Keep it awake with a free external pinger

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
  static_data.py        HDB Carpark Information fetch/cache (shelter, free/paid)
  lta_client.py         LTA DataMall live motorcycle-lot polling/cache
  datagovsg.py          shared poll-download fetch helper (GeoJSON/CSV datasets)
  ura_data.py           URA motorcycle-capacity dataset fetch/cache
  carpark_rates_data.py Carpark Rates (malls/hotels/attractions) fetch/cache
  matching.py           generic text search ranking for /check
  nearest.py            generic distance-based ranking for /nearest
  formatting.py         Telegram message formatting, one formatter per source
  responses.py          merges all sources into the /check and /nearest replies
  health.py             decoy HTTP endpoint, active only when $PORT is set (Render)
  bot.py                aiogram handlers (thin wiring onto responses.py)
  main.py               entrypoint
tests/            pytest suite (85 tests, run against fixture data — real
                  fixtures for HDB/LTA pulled from data.gov.sg, synthetic
                  fixtures for URA/Carpark Rates since real samples
                  couldn't be fetched (see verification section above) —
                  no network/API keys needed either way)
render.yaml       Render Blueprint (free Web Service)
Procfile          worker-process declaration (Railway, or any Procfile-based host)
```

## Known limitations / ideas for v2

- `is_sheltered` is inferred from `car_park_type` (basement/multi-storey =
  sheltered, surface = not) since the dataset has no explicit shelter
  flag — accurate for the vast majority of carparks but not a guarantee.
- No caching layer shared across bot restarts (in-memory only) — fine for
  a single-instance personal bot, would need Redis/similar if you ever
  scale this to multiple workers.
- `/nearest` now covers HDB + URA carparks (URA's dataset extends coverage
  beyond HDB, per "How it works" above), but Carpark Rates entries never
  appear there — that dataset has no coordinates, so they only show up in
  `/check`.
- URA carparks shown via `/check`/`/nearest` fall back to showing total
  motorcycle *capacity* rather than a live count whenever the live feed
  doesn't join to them (see verification items #3–4 above) — still useful,
  but worth knowing it's not always live.
- Carpark Rates entries rely on fuzzy name-matching against the live feed
  (no ID to join on) and the rates themselves may be stale — treat as a
  price reference, not a live-availability source.
- No rate limiting on user commands — unlikely to matter for personal use,
  but worth adding if this ever gets shared publicly.
- The Render free-tier deploy depends on an external pinger staying up
  (see "Keep it awake" above) — it's not a guaranteed always-on setup the
  way a paid worker plan would be.
