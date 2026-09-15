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

### 3. Local setup

```bash
cp .env.example .env
# edit .env and fill in TELEGRAM_BOT_TOKEN and LTA_ACCOUNT_KEY

pip install -r requirements-dev.txt
pytest                          # run the test suite (no API keys needed)
python -m motopark_bot.main     # run the bot locally (needs both keys)
```

### 4. Deploy to Railway

1. Push this repo to GitHub.
2. In Railway: New Project → Deploy from GitHub repo → pick this repo.
3. Railway will detect `requirements.txt` and the `Procfile` (a `worker`
   process — this bot long-polls Telegram, it isn't a web server, so don't
   let Railway assign it a public URL/port).
4. In the service's Variables tab, add `TELEGRAM_BOT_TOKEN` and
   `LTA_ACCOUNT_KEY` (and any of the optional tuning vars from
   `.env.example` if you want non-default values).
5. Deploy. Check the logs for `Starting polling...` — then message your
   bot on Telegram.

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
  bot.py          aiogram handlers
  main.py         entrypoint
tests/            pytest suite (31 tests, run against real fixture data
                  pulled from data.gov.sg — no network/API keys needed)
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
