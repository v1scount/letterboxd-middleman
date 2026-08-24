from __future__ import annotations

import asyncio
import logging

from app.letterboxd import add_to_watchlist
from app.models import Outcome, WatchlistRequest, WatchlistResponse
from app.session import SessionManager

logger = logging.getLogger(__name__)


async def run_watchlist(
    session: SessionManager,
    request: WatchlistRequest,
) -> WatchlistResponse:
    try:
        if not await session.ensure_ready():
            return WatchlistResponse(
                outcome=Outcome.auth_required,
                title=request.title,
                year=request.year,
                director=request.director,
                message="phone session unavailable or Letterboxd looks signed out",
            )
        return await asyncio.to_thread(add_to_watchlist, session.driver, request)
    except Exception:
        logger.exception("watchlist job failed for %r", request.title)
        return WatchlistResponse(
            outcome=Outcome.error,
            title=request.title,
            year=request.year,
            director=request.director,
            message="unexpected error while talking to Letterboxd",
        )
