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
    _NAV_LABEL_MAX_CHARS,
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


# --- build_nav_keyboard() -----------------------------------------------
# Turns responses.py's plain NavTarget data into the "🧭 Navigate" inline
# buttons under /check and /nearest replies (see the AskUserQuestion
# decision to go with Google Maps buttons over native location pins).


def test_build_nav_keyboard_returns_none_for_no_targets():
    # e.g. a /check reply that only matched Carpark Rates entries, which
    # have no coordinates - there's nothing to link a button to.
    assert build_nav_keyboard([]) is None


def test_build_nav_keyboard_one_button_per_target():
    targets = [
        NavTarget(label="ALBERT CENTRE", lat=1.301059, lon=103.855409),
        NavTarget(label="ORCHARD ROAD CARPARK", lat=1.3005, lon=103.848),
    ]
    markup = build_nav_keyboard(targets)
    assert markup is not None
    # adjust(1) - one button per row.
    assert len(markup.inline_keyboard) == 2
    assert all(len(row) == 1 for row in markup.inline_keyboard)

    first_button = markup.inline_keyboard[0][0]
    assert "ALBERT CENTRE" in first_button.text
    assert first_button.url == "https://www.google.com/maps/dir/?api=1&destination=1.301059,103.855409"


def test_build_nav_keyboard_truncates_long_labels_within_telegram_limit():
    long_name = "A" * 100
    markup = build_nav_keyboard([NavTarget(label=long_name, lat=1.0, lon=103.0)])
    label = markup.inline_keyboard[0][0].text
    # Telegram's hard cap is 64 UTF-16 code units; this module truncates
    # well before that (_NAV_LABEL_MAX_CHARS), so both bounds must hold.
    assert len(label) <= _NAV_LABEL_MAX_CHARS
    assert len(label) <= 64
    assert label.endswith("…")


def test_build_nav_keyboard_strips_whitespace_from_label():
    markup = build_nav_keyboard([NavTarget(label="  Marsiling Crescent Heavy Vehicle Park  ", lat=1.0, lon=103.0)])
    label = markup.inline_keyboard[0][0].text
    assert not label.endswith(" ")
    assert "Marsiling Crescent" in label


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
