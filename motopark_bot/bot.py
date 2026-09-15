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
from collections import OrderedDict

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandObject
from aiogram.types import BotCommand, CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from motopark_bot.carpark_rates_data import CarparkRatesStore
from motopark_bot.config import get_settings
from motopark_bot.health import start_health_server
from motopark_bot.lta_client import LiveAvailabilityStore
from motopark_bot.maps import maps_url
from motopark_bot.onemap import OneMapClient
from motopark_bot.responses import BotReply, NavTarget, build_check_response, build_nearest_response
from motopark_bot.static_data import StaticCarparkStore
from motopark_bot.ura_data import UraCarparkStore, log_raw_feature_sample

# Telegram caps inline button text at 64 UTF-16 code units - carpark
# addresses/names routinely run longer than that, so labels get truncated
# to this before the "🧭 " prefix and a trailing "…" are added.
_NAV_LABEL_MAX_CHARS = 40

# Callback data for the two-step "Navigate" picker (see _NavMenuStore and
# build_nav_keyboard below): tapping the single "Navigate" button on a
# multi-result reply expands it into one button per result; "‹ Back"
# collapses it again. Fixed strings, not per-request IDs - which specific
# reply they apply to comes from the callback's own message (chat_id,
# message_id), looked up in _NavMenuStore.
_NAV_MENU_CALLBACK = "navmenu"
_NAV_BACK_CALLBACK = "navback"

log = logging.getLogger(__name__)

START_TEXT = (
    "🏍 *MotoPark SG*\n\n"
    "Find motorcycle parking with live availability and free/paid info.\n\n"
    "*/check <name or postal code>* — look up a carpark by name or a 6-digit "
    "postal code, e.g. `/check jurong point` or `/check 238801`\n"
    "*/nearest* — then share your location, to see the closest carparks\n\n"
    "Each result comes with a 🧭 *Navigate* button. With more than one "
    "result, tap it to pick which carpark to open in Google Maps.\n\n"
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


def _nav_button_label(target: NavTarget) -> str:
    label = f"🧭 {target.label.strip()}"
    if len(label) > _NAV_LABEL_MAX_CHARS:
        label = label[: _NAV_LABEL_MAX_CHARS - 1] + "…"
    return label


def _expanded_nav_keyboard(nav_targets: list[NavTarget], with_back: bool) -> InlineKeyboardMarkup:
    """One Google Maps URL button per result - the picker's "open" step.

    `with_back` adds a "‹ Back" button that collapses back to the single
    "Navigate" button (see build_nav_keyboard) - used for a multi-result
    reply, where the user reached this view by tapping it. A single-result
    reply reuses this too (via build_nav_keyboard) but skips the back
    button, since there's no collapsed view to go back to.
    """
    builder = InlineKeyboardBuilder()
    for target in nav_targets:
        builder.button(text=_nav_button_label(target), url=maps_url(target.lat, target.lon))
    if with_back:
        builder.button(text="‹ Back", callback_data=_NAV_BACK_CALLBACK)
    builder.adjust(1)  # one per row - labels are full names/addresses, too wide for 2-up
    return builder.as_markup()


def build_nav_keyboard(nav_targets: list[NavTarget]) -> InlineKeyboardMarkup | None:
    """The nav keyboard as it first appears on a /check or /nearest reply.

    Returns None (no reply_markup at all) when there's nothing to link to -
    e.g. a /check reply that matched only Carpark Rates entries, which have
    no coordinates (see responses.py). With exactly one result, there's
    nothing to choose between, so this links straight to Google Maps like
    before. With more than one, this collapses to a single "🧭 Navigate"
    button rather than one button per result - tapping it expands into the
    per-result picker (see nav_menu_handler in build_dispatcher), so the
    user picks a specific carpark before landing on a maps link instead of
    being handed every option at once.
    """
    if not nav_targets:
        return None
    if len(nav_targets) == 1:
        return _expanded_nav_keyboard(nav_targets, with_back=False)
    builder = InlineKeyboardBuilder()
    builder.button(text=f"🧭 Navigate ({len(nav_targets)} results)", callback_data=_NAV_MENU_CALLBACK)
    return builder.as_markup()


class _NavMenuStore:
    """(chat_id, message_id) -> that reply's NavTarget list.

    Telegram's callback_query handlers get the message a button was
    attached to for free, but not the data that built its keyboard - and
    there's no room to encode a whole NavTarget list into callback_data's
    64-byte limit. This holds it server-side instead, in memory only, like
    every other cache in this bot (see README's "Known limitations") - a
    restart just means a picker mid-tap needs a fresh /check or /nearest,
    not a crash. Bounded FIFO so a long-running bot doesn't grow this
    unboundedly; any picker a user actually taps is used within seconds of
    being sent, long before eviction would matter.
    """

    def __init__(self, max_size: int = 200) -> None:
        self._max_size = max_size
        self._by_key: OrderedDict[tuple[int, int], list[NavTarget]] = OrderedDict()

    def put(self, chat_id: int, message_id: int, nav_targets: list[NavTarget]) -> None:
        key = (chat_id, message_id)
        self._by_key[key] = nav_targets
        self._by_key.move_to_end(key)
        while len(self._by_key) > self._max_size:
            self._by_key.popitem(last=False)

    def get(self, chat_id: int, message_id: int) -> list[NavTarget] | None:
        return self._by_key.get((chat_id, message_id))


def build_dispatcher(
    static_store: StaticCarparkStore,
    ura_store: UraCarparkStore,
    rates_store: CarparkRatesStore,
    live_store: LiveAvailabilityStore,
    onemap_client: OneMapClient | None = None,
) -> Dispatcher:
    dp = Dispatcher()
    nav_menus = _NavMenuStore()

    async def _send_reply_with_nav(message: Message, reply: BotReply) -> None:
        sent = await message.answer(
            reply.text, parse_mode="Markdown", reply_markup=build_nav_keyboard(reply.nav_targets)
        )
        # Only multi-result replies use the picker (see build_nav_keyboard) -
        # a single-result reply's button already links straight out, so
        # there's nothing for nav_menu_handler to ever look up for it.
        if len(reply.nav_targets) > 1:
            nav_menus.put(sent.chat.id, sent.message_id, reply.nav_targets)

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
        await _send_reply_with_nav(message, reply)

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
        await _send_reply_with_nav(message, reply)

    @dp.callback_query(F.data == _NAV_MENU_CALLBACK)
    async def nav_menu_handler(callback: CallbackQuery) -> None:
        # Expands the single "Navigate" button into one per result - see
        # build_nav_keyboard's docstring for why this is two steps.
        message = callback.message
        targets = nav_menus.get(message.chat.id, message.message_id) if message else None
        if not targets:
            await callback.answer(
                "This menu's expired - send /check or /nearest again.", show_alert=True
            )
            return
        await message.edit_reply_markup(reply_markup=_expanded_nav_keyboard(targets, with_back=True))
        await callback.answer()

    @dp.callback_query(F.data == _NAV_BACK_CALLBACK)
    async def nav_back_handler(callback: CallbackQuery) -> None:
        message = callback.message
        targets = nav_menus.get(message.chat.id, message.message_id) if message else None
        if not targets:
            await callback.answer()
            return
        await message.edit_reply_markup(reply_markup=build_nav_keyboard(targets))
        await callback.answer()

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
