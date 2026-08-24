from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request

from app.config import Settings, get_settings
from app.models import WatchlistRequest, WatchlistResponse
from app.service import run_watchlist
from app.session import SessionManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    session = SessionManager(settings)
    await session.start()
    app.state.settings = settings
    app.state.session = session
    app.state.lock = asyncio.Lock()
    logger.info("letterboxd middleman ready on %s:%s", settings.host, settings.port)
    try:
        yield
    finally:
        await session.stop()


app = FastAPI(title="Letterboxd middleman", lifespan=lifespan)


def require_bearer(
    authorization: Optional[str] = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> None:
    expected = f"Bearer {settings.middleman_api_key}"
    if not authorization or authorization != expected:
        raise HTTPException(status_code=401, detail="invalid or missing bearer token")


@app.get("/health")
async def health(request: Request) -> dict:
    session: SessionManager = request.app.state.session
    if session.logged_in is True:
        logged_in = "true"
    elif session.logged_in is False:
        logged_in = "false"
    else:
        logged_in = "unknown"
    return {
        "ok": True,
        "logged_in": logged_in,
        "appium": session.appium_up,
    }


@app.api_route("/watchlist", methods=["GET", "HEAD"], include_in_schema=False)
async def watchlist_wrong_method() -> None:
    raise HTTPException(
        status_code=405,
        detail="Use POST /watchlist with JSON {\"title\", \"year?\", \"director?\"}",
        headers={"Allow": "POST"},
    )


@app.post("/watchlist", response_model=WatchlistResponse)
async def watchlist(
    payload: WatchlistRequest,
    request: Request,
    _: None = Depends(require_bearer),
) -> WatchlistResponse:
    session: SessionManager = request.app.state.session
    lock: asyncio.Lock = request.app.state.lock
    async with lock:
        result = await run_watchlist(session, payload)
    logger.info(
        "watchlist %r -> %s %s",
        payload.title,
        result.outcome.value,
        result.url or "",
    )
    return result
