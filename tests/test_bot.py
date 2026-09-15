"""Tests for bot.py's preset-command registration and startup priming.

Telegram's own command-name rules (see BotFather / Bot API docs): 1-32
chars, lowercase Latin letters, digits and underscores only. We don't hit
the network here - just check BOT_COMMANDS is well-formed and matches the
handlers actually registered in build_dispatcher().
"""
import re

import pytest

from motopark_bot.bot import BOT_COMMANDS, _prime_optional_store

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
