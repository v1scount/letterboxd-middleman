# Letterboxd middleman

Personal HTTP service that adds a film to **your** Letterboxd watchlist. It drives the official Android app on a spare phone through Appium + UIAutomator2. Your other tools (for example [media-ai-bookmarker](https://github.com/)) call `POST /watchlist` with a title.

Letterboxd does not grant API keys for private projects. This taps the app on your account only. A few films a week is the intended load.

## Host setup

The Playwright/Chromium path is gone. Run this on the machine the phone is plugged into — not inside the old headed-Chromium container.

1. USB debugging on the phone, authorize this computer, no PIN (swipe or none). Install Letterboxd and **sign in by hand**. Leave it charging; stay-awake while plugged in.
2. Android `adb` on the PATH, or at `~/.local/opt/platform-tools/adb`.
3. Node.js, then Appium 2 and the Android driver:

```bash
npm install -g appium
appium driver install uiautomator2
```

The first Appium session installs `io.appium.uiautomator2.server` on the phone.

```bash
cp .env.example .env
```

Set `MIDDLEMAN_API_KEY`. Leave `APPIUM_SPAWN=true` so the API starts Appium if nothing is listening on `APPIUM_URL`.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m app.cli --setup
```

`--setup` confirms Appium, the device, and that the Letterboxd UI looks signed in. Optional: `scrcpy` to watch taps; `python -m app.cli --dump-source tmp/source.xml` to capture locators.

Then:

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8787
```

The API listens on `127.0.0.1:8787`.

## API

`GET /health` — process is up. `logged_in` is `true` / `false` / `unknown` from the last probe. `appium` is whether the Appium server responded.

`POST /watchlist` — bearer required.

```bash
# zsh: do not use `set -a` / `set +a` (that is bash). Paste the key, or:
#   setopt allexport && source .env && unsetopt allexport
curl -sS -X POST http://127.0.0.1:8787/watchlist \
  -H "Authorization: Bearer $MIDDLEMAN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"title":"Inception","year":2010,"director":"Christopher Nolan"}'
```

A bare `GET /watchlist` returns 405. `-X POST` and `-d` both have to be on the request; if a line-break paste drops the flags, curl falls back to GET.

`year` and `director` are optional. If several films look equally right, the service returns `ambiguous` and does not write.

| outcome | meaning |
|---|---|
| `added` | watchlist control flipped to remove |
| `already_on_watchlist` | already there; left as-is |
| `no_match` | nothing confident |
| `ambiguous` | two or more equally good hits; see `candidates` |
| `auth_required` | phone missing, Appium down, or Letterboxd looks signed out |
| `error` | timeout, markup change, unexpected screen |

Drive the phone in-process (no API):

```bash
python -m app.cli "Inception" --year 2010 --direct
```

## Session

Login lives in the Letterboxd app (`noReset`). This service does not type your password. If you see a sign-in screen, sign in on the phone and retry `--setup`.

## Docker

Not the default. Appium needs host `adb` and USB. `docker compose` can run only the FastAPI process with `network_mode: host` against a host Appium server (`APPIUM_SPAWN=false`). Prefer `uvicorn` on the host.

## Development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pytest
```

Matching rules and page-source parsers are unit-tested. There are no live Letterboxd tests in CI.

## Security

- Bind stays on localhost. Do not publish `8787` to the LAN.
- Keep `.env` off shared remotes.
