from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

import httpx

from app.config import get_settings
from app.models import WatchlistRequest
from app.service import run_watchlist
from app.session import SessionManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Add a film to your Letterboxd watchlist, or check the phone session.",
    )
    parser.add_argument("title", nargs="?", help="Film title")
    parser.add_argument("--year", type=int, default=None)
    parser.add_argument("--director", default="")
    parser.add_argument(
        "--direct",
        action="store_true",
        help="Drive Appium in this process instead of calling the running API",
    )
    parser.add_argument(
        "--setup",
        "--login",
        dest="setup",
        action="store_true",
        help="Confirm Appium, the phone, and that Letterboxd looks signed in",
    )
    parser.add_argument(
        "--dump-source",
        metavar="PATH",
        default="",
        help="Write the current Appium page source XML (for locator work)",
    )
    return parser


async def setup_and_probe() -> int:
    settings = get_settings()
    session = SessionManager(settings)
    await session.start()
    try:
        ok = session.logged_in is True
    finally:
        await session.stop()
    if ok:
        print("Appium session is up and Letterboxd looks signed in")
        return 0
    print(
        "setup failed: is the phone on USB, authorized, unlocked, "
        "and Letterboxd signed in?",
        file=sys.stderr,
    )
    return 1


async def dump_source(path: Path) -> int:
    settings = get_settings()
    session = SessionManager(settings)
    await session.start()
    try:
        if not await session.ensure_ready():
            print("could not open a signed-in Letterboxd session", file=sys.stderr)
            return 1
        xml = await asyncio.to_thread(session.dump_source)
    finally:
        await session.stop()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(xml, encoding="utf-8")
    print(f"wrote {path} ({len(xml)} bytes)")
    return 0


async def via_http(request: WatchlistRequest) -> int:
    settings = get_settings()
    url = f"http://127.0.0.1:{settings.port}/watchlist"
    headers = {
        "Authorization": f"Bearer {settings.middleman_api_key}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=180.0) as client:
        try:
            response = await client.post(url, headers=headers, json=request.model_dump())
        except httpx.ConnectError:
            print(
                f"could not reach {url}; is uvicorn up? "
                "or pass --direct to drive Appium here",
                file=sys.stderr,
            )
            return 2
    print(json.dumps(response.json(), indent=2))
    return 0 if response.is_success else 1


async def via_direct(request: WatchlistRequest) -> int:
    settings = get_settings()
    session = SessionManager(settings)
    await session.start()
    try:
        result = await run_watchlist(session, request)
    finally:
        await session.stop()
    print(result.model_dump_json(indent=2))
    return 0 if result.outcome.value not in {"error", "auth_required"} else 1


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.setup:
        raise SystemExit(asyncio.run(setup_and_probe()))
    if args.dump_source:
        raise SystemExit(asyncio.run(dump_source(Path(args.dump_source))))
    if not args.title:
        parser.error("title is required unless --setup or --dump-source")
    request = WatchlistRequest(title=args.title, year=args.year, director=args.director)
    if args.direct:
        raise SystemExit(asyncio.run(via_direct(request)))
    raise SystemExit(asyncio.run(via_http(request)))


if __name__ == "__main__":
    main()
