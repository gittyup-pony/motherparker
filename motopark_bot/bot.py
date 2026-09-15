"""aiogram handlers: /start, /check, /nearest, and a raw-location handler.

The actual response-building logic (merging HDB + URA + Carpark Rates
results) lives in responses.py, kept separate so it's testable without
simulating Telegram Message/Update objects. This module is just the thin
Telegram-facing wiring.
"""
from __future__ import annotations

import asyncio
import logging
import os

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandObject
from aiogram.types import BotCommand, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from motopark_bot.carpark_rates_data import CarparkRatesStore
from motopark_bot.config import get_settings
from motopark_bot.health import start_health_server
from motopark_bot.lta_client import LiveAvailabilityStore
from motopark_bot.maps import maps_url
from motopark_bot.onemap import OneMapClient
from motopark_bot.responses import NavTarget, build_check_response, build_nearest_response
from motopark_bot.static_data import StaticCarparkStore
from motopark_bot.ura_data import UraCarparkStore, log_raw_feature_sample

# Telegram caps inline button text at 64 UTF-16 code units - carpark
# addresses/names routinely run longer than that, so labels get truncated
# to this before the "🧭 " prefix and a trailing "…" are added.
_NAV_LABEL_MAX_CHARS = 40

log = logging.getLogger(__name__)

START_TEXT = (
    "🏍 *MotoPark SG*\n\n"
    "Find motorcycle parking with live availability and free/paid info.\n\n"
    "*/check <name or postal code>* — look up a carpark by name or a 6-digit "
    "postal code, e.g. `/check jurong point` or `/check 238801`\n"
    "*/nearest* — then share your location, to see the closest carparks\n\n"
    "Each result comes with a 🧭 *Navigate* button that opens directions in Google Maps.\n\n"
    "Data: LTA DataMall (live lots), data.gov.sg HDB Carpark Information "
    "(pricing), URA carpark capacity, and Carpark Rates (malls/hotels)."
)

# Registered with Telegram via bot.set_my_commands() at startup so they show
# up as tappable suggestions (the "/" menu and the chat's attach-menu button)
# instead of requiring the user to type them out. Keep this in sync with the
# handlers in build_dispatcher() below - Telegram doesn't validate that a
# listed command actually exists.
BOT_COMMANDS = [
    BotCommand(command="check", description="Look up a specific carpark by name"),
    BotCommand(command="nearest", description="Find the closest motorcycle parking"),
    BotCommand(command="start", description="Show what this bot does"),
]


def build_nav_keyboard(nav_targets: list[NavTarget]) -> InlineKeyboardMarkup | None:
    """One "🧭 Navigate" button per result that has coordinates.

    Returns None (no reply_markup at all) when there's nothing to link to -
    e.g. a /check reply that matched only Carpark Rates entries, which have
    no coordinates (see responses.py). Each button opens Google Maps
    directions to that carpark (see maps.py) - tapping it hands off to
    whatever maps app is installed, or the Google Maps website otherwise.
    """
    if not nav_targets:
        return None
    builder = InlineKeyboardBuilder()
    for target in nav_targets:
        label = f"🧭 {target.label.strip()}"
        if len(label) > _NAV_LABEL_MAX_CHARS:
            label = label[: _NAV_LABEL_MAX_CHARS - 1] + "…"
        builder.button(text=label, url=maps_url(target.lat, target.lon))
    builder.adjust(1)  # one per row - labels are full names/addresses, too wide for 2-up
    return builder.as_markup()


def build_dispatcher(
    static_store: StaticCarparkStore,
    ura_store: UraCarparkStore,
    rates_store: CarparkRatesStore,
    live_store: LiveAvailabilityStore,
    onemap_client: OneMapClient | None = None,
) -> Dispatcher:
    dp = Dispatcher()

    @dp.message(Command("start", "help"))
    async def start_handler(message: Message) -> None:
        await message.answer(START_TEXT, parse_mode="Markdown")

    @dp.message(Command("check"))
    async def check_handler(message: Message, command: CommandObject) -> None:
        query = (command.args or "").strip()
        if not query:
            await message.answer(
                "Usage: `/check <carpark name or postal code>`, e.g. `/check jurong point` or `/check 238801`",
                parse_mode="Markdown",
            )
            return

        settings = get_settings()
        await message.chat.do("typing")
        reply = await build_check_response(
            query,
            static_store,
            ura_store,
            rates_store,
            live_store,
            onemap_client,
            nearest_result_count=settings.nearest_result_count,
            nearest_max_radius_km=settings.nearest_max_radius_km,
        )
        await message.answer(
            reply.text, parse_mode="Markdown", reply_markup=build_nav_keyboard(reply.nav_targets)
        )

    @dp.message(Command("nearest"))
    async def nearest_prompt_handler(message: Message) -> None:
        await message.answer(
            "Tap 📎 → Location and share where you are (or a pinned location on the map), "
            "and I'll find the closest motorcycle parking."
        )

    @dp.message(F.location)
    async def location_handler(message: Message) -> None:
        loc = message.location
        settings = get_settings()

        await message.chat.do("typing")
        reply = await build_nearest_response(
            loc.latitude,
            loc.longitude,
            static_store,
            ura_store,
            live_store,
            limit=settings.nearest_result_count,
            max_radius_km=settings.nearest_max_radius_km,
        )
        await message.answer(
            reply.text, parse_mode="Markdown", reply_markup=build_nav_keyboard(reply.nav_targets)
        )

    return dp


async def _prime_optional_store(name: str, refresh_coro) -> bool:
    """Prime a non-critical static store at startup without crashing the bot.

    static_store (HDB) failing to load is fatal - it's the bot's core data.
    ura_store and rates_store are speculative extensions built against
    documented-but-unverified dataset schemas (see README), so a parsing
    failure there logs loudly and leaves that store empty/retrying rather
    than taking down /check and /nearest entirely. Returns whether priming
    succeeded, so callers can follow up (e.g. ura_store's raw-data
    diagnostic below) only when it didn't.
    """
    try:
        await refresh_coro
        return True
    except Exception:
        log.exception(
            "Failed to prime %s at startup - continuing without it. "
            "This is a known-unverified data source, see README.",
            name,
        )
        return False


async def run_bot() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()

    # Render always sets RENDER=true, but — unlike some other platforms —
    # does NOT auto-inject $PORT for a custom startCommand service; it just
    # expects the app to bind to port 10000 unless $PORT says otherwise. So
    # we trigger on RENDER=true (falling back to $PORT alone in case some
    # other host sets that without RENDER), defaulting to 10000. Railway
    # workers, Oracle Cloud VMs, and local runs set neither, so this stays
    # a no-op everywhere except Render's free Web Service tier. See health.py.
    health_runner = None
    port_env = os.environ.get("PORT")
    if os.environ.get("RENDER") == "true" or port_env:
        port = int(port_env) if port_env else 10000
        health_runner = await start_health_server(port)

    try:
        static_store = StaticCarparkStore(ttl_seconds=settings.static_data_ttl_seconds)
        ura_store = UraCarparkStore(ttl_seconds=settings.static_data_ttl_seconds)
        rates_store = CarparkRatesStore(ttl_seconds=settings.static_data_ttl_seconds)
        live_store = LiveAvailabilityStore(settings.lta_account_key, ttl_seconds=settings.live_data_ttl_seconds)

        onemap_client = (
            OneMapClient(settings.onemap_email, settings.onemap_password)
            if settings.onemap_email and settings.onemap_password
            else None
        )
        log.info(
            "Postal-code search via OneMap: %s",
            "enabled" if onemap_client else "disabled (ONEMAP_EMAIL/ONEMAP_PASSWORD not set)",
        )

        log.info("Priming static carpark dataset from data.gov.sg...")
        await static_store.refresh(force=True)
        log.info("Loaded %d HDB carparks.", len(await static_store.all()))

        log.info("Priming URA carpark capacity dataset...")
        ura_ok = await _prime_optional_store("ura_store", ura_store.refresh(force=True))
        # Read the cache directly rather than calling .all() again - that
        # would re-trigger refresh() and, on a failed prime above, throw an
        # unhandled exception here instead of just logging 0 and moving on.
        log.info("Loaded %d URA carparks.", len(ura_store._carparks))
        if not ura_ok:
            # Render's free tier has no Shell tab to run the interactive
            # `python -m motopark_bot.ura_data` smoke test, so log the raw
            # feature data here instead - visible from the (free) Logs tab.
            # A short cooldown first: the failed refresh above already made
            # 2 poll-download calls, and firing 2 more immediately risks
            # tripping data.gov.sg's rate limit (429) - confirmed in
            # production - which would make the diagnostic fail too.
            await asyncio.sleep(3)
            await log_raw_feature_sample()

        log.info("Priming Carpark Rates dataset...")
        await _prime_optional_store("rates_store", rates_store.refresh(force=True))
        log.info("Loaded %d rate entries.", len(rates_store._entries))

        bot = Bot(token=settings.bot_token)
        dp = build_dispatcher(static_store, ura_store, rates_store, live_store, onemap_client)

        await bot.set_my_commands(BOT_COMMANDS)
        log.info("Registered %d preset commands with Telegram.", len(BOT_COMMANDS))

        log.info("Starting polling...")
        await dp.start_polling(bot)
    finally:
        if health_runner is not None:
            await health_runner.cleanup()
