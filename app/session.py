from __future__ import annotations

import logging
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import httpx
from appium import webdriver
from appium.options.android import UiAutomator2Options
from appium.webdriver.client_config import AppiumClientConfig
from appium.webdriver.webdriver import WebDriver
from selenium.common.exceptions import WebDriverException

from app.config import Settings

logger = logging.getLogger(__name__)

_APPIUM_READY_TIMEOUT_S = 45
_DRIVER_HTTP_TIMEOUT_S = 180


class SessionManager:
    """One Appium UiAutomator2 session. Login lives on the phone (`noReset`)."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._driver: Optional[WebDriver] = None
        self._appium_proc: Optional[subprocess.Popen] = None
        self.logged_in: Optional[bool] = None
        self.appium_up: bool = False

    @property
    def driver(self) -> WebDriver:
        if self._driver is None:
            raise RuntimeError("Appium session is not open")
        return self._driver

    def _start_sync(self) -> None:
        self._ensure_appium()
        try:
            self._ensure_driver()
            self._probe_logged_in()
        except RuntimeError as exc:
            logger.error("%s", exc)
            self.logged_in = False
        except Exception:
            logger.exception("could not open Letterboxd on the phone yet")
            self.logged_in = False

    async def start(self) -> None:
        import asyncio

        await asyncio.to_thread(self._start_sync)

    def _stop_sync(self) -> None:
        self._quit_driver()
        if self._appium_proc is not None:
            logger.info("stopping Appium process we spawned")
            self._appium_proc.terminate()
            try:
                self._appium_proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self._appium_proc.kill()
            self._appium_proc = None
        self.appium_up = False

    async def stop(self) -> None:
        import asyncio

        await asyncio.to_thread(self._stop_sync)

    def _ensure_ready_sync(self) -> bool:
        try:
            self._ensure_appium()
            self._ensure_driver()
        except RuntimeError as exc:
            logger.error("%s", exc)
            self.logged_in = False
            return False
        except Exception:
            logger.exception("no Appium/device session")
            self.logged_in = False
            return False
        return self._probe_logged_in()

    async def ensure_ready(self) -> bool:
        import asyncio

        return await asyncio.to_thread(self._ensure_ready_sync)

    def dump_source(self) -> str:
        self._ensure_appium()
        self._ensure_driver()
        return self.driver.page_source or ""

    def _ensure_appium(self) -> None:
        if _appium_ready(self.settings.appium_url):
            self.appium_up = True
            return
        if not self.settings.appium_spawn:
            self.appium_up = False
            raise RuntimeError(
                f"Appium is not reachable at {self.settings.appium_url} "
                "(start it, or set APPIUM_SPAWN=true)"
            )
        self._spawn_appium()
        deadline = time.monotonic() + _APPIUM_READY_TIMEOUT_S
        while time.monotonic() < deadline:
            if _appium_ready(self.settings.appium_url):
                self.appium_up = True
                logger.info("Appium is ready at %s", self.settings.appium_url)
                return
            if self._appium_proc is not None and self._appium_proc.poll() is not None:
                raise RuntimeError("Appium process exited before becoming ready")
            time.sleep(0.4)
        self.appium_up = False
        raise RuntimeError(f"Appium did not become ready at {self.settings.appium_url}")

    def _spawn_appium(self) -> None:
        parsed = urlparse(self.settings.appium_url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 4723
        binary = self.settings.resolved_appium_bin
        env = os.environ.copy()
        adb = Path(self.settings.resolved_adb_bin)
        if adb.parent.is_dir():
            env["PATH"] = f"{adb.parent}{os.pathsep}{env.get('PATH', '')}"
        if adb.parent.name == "platform-tools":
            sdk = str(adb.parent.parent)
            env.setdefault("ANDROID_HOME", sdk)
            env.setdefault("ANDROID_SDK_ROOT", sdk)
        log_path = Path("data") / "appium.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_file = log_path.open("ab")
        logger.info("starting Appium %s on %s:%s", binary, host, port)
        self._appium_proc = subprocess.Popen(
            [binary, "--address", host, "--port", str(port), "--log-no-colors"],
            stdout=log_file,
            stderr=subprocess.STDOUT,
            env=env,
            start_new_session=True,
        )

    def _ensure_driver(self) -> WebDriver:
        if self._driver is not None and _driver_alive(self._driver):
            return self._driver
        self._quit_driver()
        self._require_device()
        self._wake_device()
        options = _uia2_options(self.settings)
        client = AppiumClientConfig(
            remote_server_addr=self.settings.appium_url.rstrip("/"),
            timeout=_DRIVER_HTTP_TIMEOUT_S,
        )
        logger.info(
            "creating UiAutomator2 session package=%s udid=%s",
            self.settings.letterboxd_package,
            self.settings.adb_serial or "(auto)",
        )
        self._driver = webdriver.Remote(
            self.settings.appium_url.rstrip("/"),
            options=options,
            client_config=client,
        )
        self._driver.implicitly_wait(0)
        try:
            self._driver.activate_app(self.settings.letterboxd_package)
        except Exception:
            logger.info("activate_app failed; session still has the launch activity")
        self.dim_display()
        return self._driver

    def _quit_driver(self) -> None:
        if self._driver is None:
            return
        try:
            self._driver.quit()
        except Exception:
            logger.info("Appium quit failed; dropping stale session")
        self._driver = None

    def _require_device(self) -> None:
        rows = _adb_device_rows(self.settings)
        serials = [serial for serial, state in rows if state == "device"]
        if not serials:
            unauthorized = [serial for serial, state in rows if state == "unauthorized"]
            if unauthorized:
                raise RuntimeError(
                    "Android device is unauthorized; accept the USB debugging prompt "
                    "on the phone, or mount the host ~/.android adb keys into the container"
                )
            raise RuntimeError(
                "no Android device on adb; plug in the phone, stop host adb "
                "(adb kill-server), and authorize USB debugging"
            )
        wanted = self.settings.adb_serial.strip()
        if wanted and wanted not in serials:
            raise RuntimeError(
                f"ADB_SERIAL {wanted} is not connected (adb sees: {', '.join(serials)})"
            )
        logger.info("adb devices: %s", ", ".join(serials))

    def _wake_device(self) -> None:
        prefix = _adb_prefix(self.settings)
        for args in (
            prefix + ["shell", "svc", "power", "stayon", "usb"],
            prefix + ["shell", "wm", "dismiss-keyguard"],
        ):
            try:
                subprocess.run(
                    args,
                    check=False,
                    timeout=8,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except (FileNotFoundError, subprocess.TimeoutExpired):
                logger.info("adb wake step skipped: %s", args[0])
                return

    def dim_display(self) -> None:
        """Turn the physical panel off without sleeping the device."""
        prefix = _adb_prefix(self.settings)
        try:
            result = subprocess.run(
                prefix + ["shell", "cmd", "display", "power-off", "0"],
                check=False,
                timeout=8,
                capture_output=True,
                text=True,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            logger.info("display power-off skipped; signalling scrcpy instead")
            _restart_scrcpy()
            return
        text = f"{result.stdout or ''}{result.stderr or ''}".lower()
        failed = result.returncode != 0 or any(
            token in text
            for token in ("unknown", "usage:", "can't find", "not found", "error")
        )
        if not failed:
            logger.info("display power-off via cmd")
            return
        _restart_scrcpy()

    def _probe_logged_in(self) -> bool:
        from app.letterboxd import looks_logged_in, looks_signed_out

        driver = self._driver
        if driver is None:
            self.logged_in = False
            return False
        try:
            driver.activate_app(self.settings.letterboxd_package)
        except Exception:
            pass
        time.sleep(1.2)
        source = driver.page_source or ""
        if looks_signed_out(source):
            logger.info("Letterboxd UI looks signed out")
            self.logged_in = False
            return False
        if looks_logged_in(source):
            logger.info("Letterboxd UI looks signed in")
            self.logged_in = True
            return True
        logger.info("could not tell login state from the current screen")
        self.logged_in = False
        return False


def _uia2_options(settings: Settings) -> UiAutomator2Options:
    options = UiAutomator2Options()
    caps: dict = {
        "platformName": "Android",
        "appium:automationName": "UiAutomator2",
        "appium:appPackage": settings.letterboxd_package,
        "appium:appActivity": settings.letterboxd_activity,
        "appium:appWaitActivity": "*",
        "appium:noReset": True,
        "appium:fullReset": False,
        "appium:autoGrantPermissions": True,
        "appium:disableWindowAnimation": True,
        "appium:skipLogcatCapture": True,
        "appium:ignoreHiddenApiPolicyError": True,
        "appium:newCommandTimeout": 86400,
        "appium:uiautomator2ServerInstallTimeout": 120000,
        "appium:adbExecTimeout": 60000,
    }
    if settings.adb_serial.strip():
        caps["appium:udid"] = settings.adb_serial.strip()
    options.load_capabilities(caps)
    return options


def _adb_prefix(settings: Settings) -> list[str]:
    adb = settings.resolved_adb_bin
    serial = settings.adb_serial.strip()
    return [adb] + (["-s", serial] if serial else [])


def _restart_scrcpy() -> None:
    path = Path(os.environ.get("SCRCPY_PID_FILE", "/tmp/scrcpy.pid"))
    try:
        pid = int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        logger.info("scrcpy pid file missing; panel-off fallback skipped")
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        logger.info("scrcpy pid %s already gone", pid)
        return
    except PermissionError:
        logger.info("could not signal scrcpy pid %s", pid)
        return
    logger.info("signalled scrcpy pid %s to re-apply turn-screen-off", pid)


def _adb_device_rows(settings: Settings) -> list[tuple[str, str]]:
    adb = settings.resolved_adb_bin
    try:
        result = subprocess.run(
            [adb, "devices"],
            check=False,
            timeout=8,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        logger.warning("could not run %s devices", adb)
        return []
    rows: list[tuple[str, str]] = []
    for line in (result.stdout or "").splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2:
            rows.append((parts[0], parts[1]))
    return rows


def _list_adb_serials(settings: Settings) -> list[str]:
    return [serial for serial, state in _adb_device_rows(settings) if state == "device"]


def _appium_ready(url: str) -> bool:
    status_url = url.rstrip("/") + "/status"
    try:
        response = httpx.get(status_url, timeout=2.0)
    except httpx.HTTPError:
        return False
    if response.status_code >= 400:
        return False
    try:
        payload = response.json()
    except Exception:
        return True
    value = payload.get("value") if isinstance(payload, dict) else None
    if isinstance(value, dict) and "ready" in value:
        return bool(value.get("ready"))
    return True


def _driver_alive(driver: WebDriver) -> bool:
    try:
        return bool(driver.session_id) and driver.current_package is not None
    except WebDriverException:
        return False
    except Exception:
        return False
