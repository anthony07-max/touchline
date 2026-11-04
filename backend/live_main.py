"""Entry point for the live-match instance (port 8766): shared app + additive live router + poller."""
import asyncio
from .main import app
from .live.router import router, poller

app.include_router(router)


@app.on_event("startup")
async def _start_live_poller():
    asyncio.create_task(poller())
    from .live import lineups
    asyncio.create_task(lineups.watcher())
    asyncio.create_task(_push_watch())


async def _push_watch():
    """Kick-off / full-time alerts from the discovery cache (no extra feed calls beyond what the site already makes)."""
    from . import webpush
    from .live import centre
    await asyncio.sleep(30)
    while True:
        try:
            if centre.DISCOVERY.get("items"): await asyncio.to_thread(webpush.tick, centre.DISCOVERY["items"])
        except Exception: pass
        await asyncio.sleep(60)
from .live.centre import router as centre_router
app.include_router(centre_router)
from .live.browse import router as browse_router
app.include_router(browse_router)
