"""Entry point for the live-match instance (port 8766): shared app + additive live router + poller."""
import asyncio
from .main import app
from .live.router import router, poller

app.include_router(router)


@app.on_event("startup")
async def _start_live_poller():
    asyncio.create_task(poller())
from .live.centre import router as centre_router
app.include_router(centre_router)
