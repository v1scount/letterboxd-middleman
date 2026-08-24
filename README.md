# Letterboxd middleman

Personal HTTP service that adds a film to **your** Letterboxd watchlist. It drives the official Android app on a spare phone through Appium + UIAutomator2. Your other tools (for example [media-ai-bookmarker](https://github.com/)) call `POST /watchlist` with a title.

Letterboxd does not grant API keys for private projects. This taps the app on your account only. A few films a week is the intended load.

## Selfhost (Docker)

One container runs FastAPI, Appium, and adb. Run it on the machine the phone is plugged into.

1. USB debugging on the phone, authorize this computer, no PIN (swipe or none). Install Letterboxd and **sign in by hand**. Leave it charging; stay-awake while plugged in.
2. Copy env and set the API key:

```bash
cp .env.example .env
```

Set `MIDDLEMAN_API_KEY`.

3. Stop host Appium and host adb so they do not steal the USB device:

```bash
adb kill-server
```

Stop any `appium` process on the host as well.

4. Start the stack:

```bash
docker compose up -d --build
```

5. Confirm the phone and that Letterboxd looks signed in:

```bash
docker compose exec middleman python -m app.cli --setup
```

The first Appium session installs `io.appium.uiautomator2.server` on the phone.

The API is on `http://127.0.0.1:8787`. Add this compose project in Arcane and start/stop the `letterboxd-middleman` container from there.

Callers on the same host keep using `http://127.0.0.1:8787`. A sibling container must not use `127.0.0.1`; use `http://host.docker.internal:8787` (or a shared Compose network).

Optional: `scrcpy` on the host needs this container's adb server (port 5037 is not published by default). Watch the phone screen, or exec `--dump-source` inside the container:

```bash
docker compose exec middleman python -m app.cli --dump-source /app/data/source.xml
```

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

Drive the phone in-process (no API), from a host venv or inside the container:

```bash
python -m app.cli "Inception" --year 2010 --direct
docker compose exec middleman python -m app.cli "Inception" --year 2010 --direct
```

## Session

Login lives in the Letterboxd app (`noReset`). This service does not type your password. If you see a sign-in screen, sign in on the phone and retry `--setup`.

## Development

Host venv is for tests and local iteration. You still need Appium, adb, and the phone on this machine.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pytest
```

```bash
cp .env.example .env
```

For a host venv, set `HOST=127.0.0.1`. Leave `APPIUM_SPAWN=true` so the API starts Appium if nothing is listening on `APPIUM_URL`.

```bash
python -m app.cli --setup
uvicorn app.main:app --host 127.0.0.1 --port 8787
```

Matching rules and page-source parsers are unit-tested. There are no live Letterboxd tests in CI.

## Security

- Compose publishes `127.0.0.1:8787` only. Do not publish `8787` to the LAN.
- Keep `.env` off shared remotes.
