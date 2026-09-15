# MotoPark SG Bot

Telegram bot for finding motorcycle parking in Singapore: live lot counts
(via LTA DataMall) plus sheltered/unsheltered and free/paid info (via
data.gov.sg's HDB Carpark Information dataset) — the combination none of
the existing apps seem to do.

**Commands:**
- `/check <name>` — look up a specific carpark by name, e.g. `/check jurong point`
- `/nearest` — then share a location pin, to see the closest carparks

## How it works

- `static_data.py` fetches and caches (24h TTL) the HDB Carpark Information
  dataset from data.gov.sg — no API key needed, it's a public dataset. This
  gives shelter type (`car_park_type`), free/paid (`free_parking`), night
  parking, and coordinates (converted from SVY21 to lat/lon in `geo.py`).
- `lta_client.py` polls LTA DataMall's `CarParkAvailabilityv2` for live lot
  counts, filtered to motorcycle lots, cached with a 45s TTL.
- The two are joined on carpark ID (HDB's `car_park_no` == LTA's
  `CarParkID` for HDB-agency records) to answer both commands.
- `health.py` runs a tiny HTTP endpoint, but *only* when `RENDER=true` (or
  `$PORT`) is set — true on Render, false everywhere else this README
  covers. Render doesn't auto-inject `$PORT` for a custom start command,
  it just expects port 10000 by default, so that's what this defaults to.
  See "Deploy to Render" below for why it exists.

## ⚠️ Two things to verify once you have a real API key

I built this against LTA's documented schema, but couldn't test live calls
myself (no DataMall account, and this sandbox's network doesn't reach
data.gov.sg/LTA anyway). Two assumptions need a real check:

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
  config.py       env var loading (get_settings())
  geo.py          SVY21->WGS84 conversion, haversine distance
  static_data.py  HDB Carpark Information fetch/cache (shelter, free/paid)
  lta_client.py   LTA DataMall live motorcycle-lot polling/cache
  matching.py     text search ranking for /check
  nearest.py      distance-based ranking for /nearest
  formatting.py   Telegram message formatting shared by both commands
  health.py       decoy HTTP endpoint, active only when $PORT is set (Render)
  bot.py          aiogram handlers
  main.py         entrypoint
tests/            pytest suite (33 tests, run against real fixture data
                  pulled from data.gov.sg — no network/API keys needed)
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
- `/nearest` only searches HDB carparks (LTA/URA carparks are technically
  in the live feed too, but the static shelter/pricing dataset used here
  only covers HDB) — URA's own carpark API could extend coverage.
- No rate limiting on user commands — unlikely to matter for personal use,
  but worth adding if this ever gets shared publicly.
- The Render free-tier deploy depends on an external pinger staying up
  (see "Keep it awake" above) — it's not a guaranteed always-on setup the
  way a paid worker plan would be.
