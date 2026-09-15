import socket

import aiohttp
import pytest

from motopark_bot.health import start_health_server


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


@pytest.mark.asyncio
async def test_health_server_responds_ok_on_root_and_health_path():
    port = _free_port()
    runner = await start_health_server(port)
    try:
        async with aiohttp.ClientSession() as session:
            for path in ("/", "/health"):
                async with session.get(f"http://127.0.0.1:{port}{path}") as resp:
                    assert resp.status == 200
                    assert (await resp.text()) == "ok"
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_health_server_404_on_unknown_path():
    port = _free_port()
    runner = await start_health_server(port)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"http://127.0.0.1:{port}/nope") as resp:
                assert resp.status == 404
    finally:
        await runner.cleanup()
