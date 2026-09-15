"""Tests for bot.py's preset-command registration and startup priming.

Telegram's own command-name rules (see BotFather / Bot API docs): 1-32
chars, lowercase Latin letters, digits and underscores only. We don't hit
the network here - just check BOT_COMMANDS is well-formed and matches the
handlers actually registered in build_dispatcher().
"""
import re

import pytest

from motopark_bot.bot import (
    BOT_COMMANDS,
    START_TEXT,
    _NAV_BACK_CALLBACK,
    _NAV_LABEL_MAX_CHARS,
    _NAV_MENU_CALLBACK,
    _expanded_nav_keyboard,
    _NavMenuStore,
    _prime_optional_store,
    build_dispatcher,
    build_nav_keyboard,
)
from motopark_bot.responses import NavTarget

_VALID_COMMAND_RE = re.compile(r"^[a-z0-9_]{1,32}$")


def test_bot_commands_are_valid_telegram_command_names():
    for cmd in BOT_COMMANDS:
        assert _VALID_COMMAND_RE.match(cmd.command), f"{cmd.command!r} is not a valid Telegram command name"


def test_bot_commands_have_nonempty_descriptions_within_telegram_limit():
    for cmd in BOT_COMMANDS:
        assert cmd.description
        assert len(cmd.description) <= 256  # Telegram's max description length


def test_bot_commands_cover_the_handlers_users_can_type():
    # /start doubles as /help in the dispatcher (Command("start", "help")),
    # but only /start needs to be listed - typing "/help" still works, it's
    # just not offered as a menu suggestion.
    names = {cmd.command for cmd in BOT_COMMANDS}
    assert names == {"check", "nearest", "start"}


def test_bot_commands_no_duplicates():
    names = [cmd.command for cmd in BOT_COMMANDS]
    assert len(names) == len(set(names))


# --- _prime_optional_store's return value ------------------------------
# run_bot() uses this to decide whether to follow up a failed URA prime
# with the raw-feature diagnostic (ura_data.log_raw_feature_sample) - so
# it needs to reliably report success/failure rather than just swallowing
# exceptions silently.


@pytest.mark.asyncio
async def test_prime_optional_store_returns_true_on_success():
    async def ok():
        return None

    assert await _prime_optional_store("thing", ok()) is True


@pytest.mark.asyncio
async def test_prime_optional_store_returns_false_and_does_not_raise_on_failure():
    async def broken():
        raise RuntimeError("simulated failure")

    assert await _prime_optional_store("thing", broken()) is False


# --- build_nav_keyboard() / _expanded_nav_keyboard() ---------------------
# Turns responses.py's plain NavTarget data into the "🧭 Navigate" inline
# buttons under /check and /nearest replies. A single result links
# straight to Google Maps; more than one collapses to a single "Navigate"
# button that expands into a picker on tap (see nav_menu_handler in
# build_dispatcher), rather than showing every option's button at once.


def test_build_nav_keyboard_returns_none_for_no_targets():
    # e.g. a /check reply that only matched Carpark Rates entries, which
    # have no coordinates - there's nothing to link a button to.
    assert build_nav_keyboard([]) is None


def test_build_nav_keyboard_single_target_links_straight_to_maps():
    # Nothing to choose between with only one result - no picker step.
    targets = [NavTarget(label="ALBERT CENTRE", lat=1.301059, lon=103.855409)]
    markup = build_nav_keyboard(targets)
    assert markup is not None
    assert len(markup.inline_keyboard) == 1
    button = markup.inline_keyboard[0][0]
    assert "ALBERT CENTRE" in button.text
    assert button.url == "https://www.google.com/maps/dir/?api=1&destination=1.301059,103.855409"
    assert button.callback_data is None


def test_build_nav_keyboard_multiple_targets_collapses_to_one_button():
    targets = [
        NavTarget(label="ALBERT CENTRE", lat=1.301059, lon=103.855409),
        NavTarget(label="ORCHARD ROAD CARPARK", lat=1.3005, lon=103.848),
    ]
    markup = build_nav_keyboard(targets)
    assert markup is not None
    assert len(markup.inline_keyboard) == 1
    button = markup.inline_keyboard[0][0]
    assert "Navigate" in button.text
    assert "2" in button.text
    assert button.callback_data == _NAV_MENU_CALLBACK
    assert button.url is None  # not a direct maps link - taps expand the picker


def test_expanded_nav_keyboard_one_button_per_target():
    targets = [
        NavTarget(label="ALBERT CENTRE", lat=1.301059, lon=103.855409),
        NavTarget(label="ORCHARD ROAD CARPARK", lat=1.3005, lon=103.848),
    ]
    markup = _expanded_nav_keyboard(targets, with_back=True)
    # 2 result buttons + 1 "back" button, one per row (adjust(1)).
    assert len(markup.inline_keyboard) == 3
    assert all(len(row) == 1 for row in markup.inline_keyboard)

    first_button = markup.inline_keyboard[0][0]
    assert "ALBERT CENTRE" in first_button.text
    assert first_button.url == "https://www.google.com/maps/dir/?api=1&destination=1.301059,103.855409"

    back_button = markup.inline_keyboard[-1][0]
    assert back_button.callback_data == _NAV_BACK_CALLBACK
    assert back_button.url is None


def test_expanded_nav_keyboard_without_back_button():
    targets = [NavTarget(label="ALBERT CENTRE", lat=1.301059, lon=103.855409)]
    markup = _expanded_nav_keyboard(targets, with_back=False)
    assert len(markup.inline_keyboard) == 1  # no extra "back" row


def test_expanded_nav_keyboard_truncates_long_labels_within_telegram_limit():
    long_name = "A" * 100
    markup = _expanded_nav_keyboard([NavTarget(label=long_name, lat=1.0, lon=103.0)], with_back=False)
    label = markup.inline_keyboard[0][0].text
    # Telegram's hard cap is 64 UTF-16 code units; this module truncates
    # well before that (_NAV_LABEL_MAX_CHARS), so both bounds must hold.
    assert len(label) <= _NAV_LABEL_MAX_CHARS
    assert len(label) <= 64
    assert label.endswith("…")


def test_expanded_nav_keyboard_strips_whitespace_from_label():
    targets = [NavTarget(label="  Marsiling Crescent Heavy Vehicle Park  ", lat=1.0, lon=103.0)]
    markup = _expanded_nav_keyboard(targets, with_back=False)
    label = markup.inline_keyboard[0][0].text
    assert not label.endswith(" ")
    assert "Marsiling Crescent" in label


# --- _NavMenuStore --------------------------------------------------------
# Server-side (chat_id, message_id) -> NavTarget list, since callback_data
# is too small to carry a whole picker's worth of results itself.


def test_nav_menu_store_put_then_get():
    store = _NavMenuStore()
    targets = [NavTarget(label="ALBERT CENTRE", lat=1.3, lon=103.85)]
    store.put(chat_id=1, message_id=42, nav_targets=targets)
    assert store.get(chat_id=1, message_id=42) == targets


def test_nav_menu_store_get_missing_returns_none():
    store = _NavMenuStore()
    assert store.get(chat_id=1, message_id=42) is None


def test_nav_menu_store_evicts_oldest_beyond_max_size():
    store = _NavMenuStore(max_size=2)
    store.put(1, 1, [NavTarget(label="A", lat=1.0, lon=103.0)])
    store.put(1, 2, [NavTarget(label="B", lat=1.0, lon=103.0)])
    store.put(1, 3, [NavTarget(label="C", lat=1.0, lon=103.0)])

    assert store.get(1, 1) is None  # evicted - oldest, beyond max_size
    assert store.get(1, 2) is not None
    assert store.get(1, 3) is not None


# --- build_dispatcher() wiring -------------------------------------------
# onemap_client is optional (postal-code search gracefully no-ops without
# it, see responses.py) - build_dispatcher() must accept either case
# without blowing up, since run_bot() only constructs one when
# ONEMAP_EMAIL/ONEMAP_PASSWORD are both set.


def test_build_dispatcher_works_without_onemap_client():
    dp = build_dispatcher(object(), object(), object(), object())
    assert dp is not None


def test_build_dispatcher_works_with_onemap_client():
    dp = build_dispatcher(object(), object(), object(), object(), onemap_client=object())
    assert dp is not None


def test_start_text_mentions_postal_code_support_and_not_stale_shelter_claim():
    # START_TEXT used to promise "shelter type" info that formatting.py no
    # longer shows (see the streamlined-output change) - and now that
    # /check accepts postal codes too, the help text should say so.
    assert "shelter" not in START_TEXT.lower()
    assert "postal code" in START_TEXT.lower()
