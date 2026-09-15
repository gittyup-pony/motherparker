"""Minimal HTTP health endpoint.

Only exists so this bot can be deployed as a Render *free* Web Service.
Render's free tier has no "background worker" option — only web services,
and only web services that bind to $PORT and respond to HTTP pass Render's
health check. The bot itself doesn't need or use HTTP (it long-polls
Telegram), so this is purely a decoy: something for Render to see as
"alive", and something an external pinger (UptimeRobot, cron-job.org, etc)
can hit every ~10 minutes to stop the free service from spinning down
after 15 minutes of no requests.

If you ever move to a real background-worker host (Railway, Render Pro,
Oracle Cloud, ...), this module becomes unnecessary — main.py just wouldn't
call start_health_server, and the polling loop runs the same either way.
"""
from __future__ import annotations

import logging

from aiohttp import web

log = logging.getLogger(__name__)


async def _handle_health(request: web.Request) -> web.Response:
    return web.Response(text="ok")


async def start_health_server(port: int) -> web.AppRunner:
    """Bind a tiny HTTP server on 0.0.0.0:port and return its runner.

    Caller is responsible for `await runner.cleanup()` on shutdown.
    """
    app = web.Application()
    app.router.add_get("/", _handle_health)
    app.router.add_get("/health", _handle_health)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=port)
    await site.start()
    log.info("Health check server listening on 0.0.0.0:%d", port)
    return runner
