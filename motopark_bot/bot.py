"""aiogram handlers: /start, /check, /nearest, and a raw-location handler."""
from __future__ import annotations

import logging

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from motopark_bot.config import get_settings
from motopark_bot.formatting import format_check_results, format_nearest_results
from motopark_bot.lta_client import LiveAvailabilityStore
from motopark_bot.matching import rank_matches
from motopark_bot.nearest import find_nearest
from motopark_bot.static_data import StaticCarparkStore

log = logging.getLogger(__name__)

START_TEXT = (
    "🏍 *MotoPark SG*\n\n"
    "Find motorcycle parking with live lot counts, shelter type, and free/paid info.\n\n"
    "*/check <name>* — look up a specific carpark, e.g. `/check jurong point`\n"
    "*/nearest* — then share your location, to see the closest carparks\n\n"
    "Data: LTA DataMall (live lots) + data.gov.sg HDB Carpark Information (shelter/pricing)."
)


def build_dispatcher(static_store: StaticCarparkStore, live_store: LiveAvailabilityStore) -> Dispatcher:
    dp = Dispatcher()

    @dp.message(Command("start", "help"))
    async def start_handler(message: Message) -> None:
        await message.answer(START_TEXT, parse_mode="Markdown")

    @dp.message(Command("check"))
    async def check_handler(message: Message, command: CommandObject) -> None:
        query = (command.args or "").strip()
        if not query:
            await message.answer("Usage: `/check <carpark name>`, e.g. `/check jurong point`", parse_mode="Markdown")
            return

        await message.chat.do("typing")
        carparks = await static_store.all()
        matches = rank_matches(query, carparks, limit=5)

        live_by_id = {}
        for cp in matches:
            live = await live_store.get(cp.car_park_no)
            if live is not None:
                live_by_id[cp.car_park_no] = live

        await message.answer(format_check_results(matches, live_by_id), parse_mode="Markdown")

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
        carparks = await static_store.all()
        ranked = find_nearest(
            loc.latitude,
            loc.longitude,
            carparks,
            limit=settings.nearest_result_count,
            max_radius_km=settings.nearest_max_radius_km,
        )

        live_by_id = {}
        for r in ranked:
            live = await live_store.get(r.info.car_park_no)
            if live is not None:
                live_by_id[r.info.car_park_no] = live

        await message.answer(format_nearest_results(ranked, live_by_id), parse_mode="Markdown")

    return dp


async def run_bot() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()

    static_store = StaticCarparkStore(ttl_seconds=settings.static_data_ttl_seconds)
    live_store = LiveAvailabilityStore(settings.lta_account_key, ttl_seconds=settings.live_data_ttl_seconds)

    log.info("Priming static carpark dataset from data.gov.sg...")
    await static_store.refresh(force=True)
    log.info("Loaded %d carparks.", len(await static_store.all()))

    bot = Bot(token=settings.bot_token)
    dp = build_dispatcher(static_store, live_store)

    log.info("Starting polling...")
    await dp.start_polling(bot)
