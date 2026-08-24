from __future__ import annotations

import logging
import re
import time
import xml.etree.ElementTree as ET
from typing import Literal, Optional
from urllib.parse import quote

from appium.webdriver.common.appiumby import AppiumBy
from appium.webdriver.webdriver import WebDriver
from selenium.common.exceptions import StaleElementReferenceException, WebDriverException

from app.matching import pick_match, titles_match, years_match
from app.models import FilmHit, Outcome, WatchlistRequest, WatchlistResponse

logger = logging.getLogger(__name__)

LETTERBOXD_ORIGIN = "https://letterboxd.com"
FILM_HREF_RE = re.compile(r"/film/([^/?#]+)")
YEAR_RE = re.compile(r"\b((?:19|20)\d{2})\b")
ITEM_NAME_RE = re.compile(
    r"^(?P<title>.+?)\s*\((?P<year>(?:19|20)\d{2})\)\s*$"
)
_SLUG_APOS_RE = re.compile(r"['’`´]")
_SLUG_JUNK_RE = re.compile(r"[^a-z0-9]+")
_BOUNDS_RE = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")
_DIRECTED_BY_RE = re.compile(
    r"(?:directed by|director[:\s]+)\s*(.+)",
    re.I,
)

SKIP_TITLES = frozenset(
    {
        "search",
        "home",
        "films",
        "lists",
        "profile",
        "log",
        "diary",
        "watchlist",
        "settings",
        "browse",
        "reviews",
        "members",
        "cancel",
        "clear",
        "back",
        "close",
        "ok",
        "done",
        "want to watch",
        "sign in",
        "log in",
    }
)

# Content-desc / text locators from the Material 3 Letterboxd app.
# Prefer accessibility id, then UiSelector regex, then text.
SEARCH_TAB = (
    (AppiumBy.ACCESSIBILITY_ID, "Search"),
    (
        AppiumBy.ANDROID_UIAUTOMATOR,
        'new UiSelector().descriptionMatches("(?i)^search$")',
    ),
    (
        AppiumBy.ANDROID_UIAUTOMATOR,
        'new UiSelector().textMatches("(?i)^search$")',
    ),
)
SEARCH_FIELD = (
    (AppiumBy.CLASS_NAME, "android.widget.EditText"),
    (
        AppiumBy.ANDROID_UIAUTOMATOR,
        'new UiSelector().className("android.widget.EditText")',
    ),
)
_KEYCODE_PASTE = 279
# Search tab and the top search bar share content-desc/text "Search".
# The bar is in the content area; the tab sits in the system nav chrome.
WATCHLIST_CONTROL = (
    (AppiumBy.ACCESSIBILITY_ID, "Watchlist"),
    (
        AppiumBy.ANDROID_UIAUTOMATOR,
        'new UiSelector().descriptionMatches("(?i).*(add|remove).*(watchlist|want to watch).*")',
    ),
    (
        AppiumBy.ANDROID_UIAUTOMATOR,
        'new UiSelector().descriptionMatches("(?i).*watchlist.*")',
    ),
    (
        AppiumBy.ANDROID_UIAUTOMATOR,
        'new UiSelector().textMatches("(?i)^watchlist$")',
    ),
)
# Blue film-actions bar. Copy changes (rated vs unlogged); match the job, not one string.
# Do not require clickable: Compose often puts the label on a child TextView.
FILM_ACTIONS_OPENER = (
    (
        AppiumBy.ANDROID_UIAUTOMATOR,
        'new UiSelector().textMatches("(?i).*rated this film.*")',
    ),
    (
        AppiumBy.ANDROID_UIAUTOMATOR,
        'new UiSelector().descriptionMatches("(?i).*rated this film.*")',
    ),
    (
        AppiumBy.ANDROID_UIAUTOMATOR,
        'new UiSelector().textMatches("(?i).*(rate,\\s*log|log,\\s*review).*")',
    ),
    (
        AppiumBy.ANDROID_UIAUTOMATOR,
        'new UiSelector().descriptionMatches("(?i).*(rate,\\s*log|log,\\s*review).*")',
    ),
    (
        AppiumBy.ANDROID_UIAUTOMATOR,
        'new UiSelector().textMatches("(?i).*\\+\\s*more.*")',
    ),
    (
        AppiumBy.ANDROID_UIAUTOMATOR,
        'new UiSelector().textMatches("(?i).*add to list.*")',
    ),
    (
        AppiumBy.ANDROID_UIAUTOMATOR,
        'new UiSelector().descriptionMatches("(?i).*add to list.*")',
    ),
)

WatchlistState = Literal["add", "remove", "missing"]


def letterboxd_slug(title: str) -> str:
    """Letterboxd-style slug: lowercase, drop apostrophes, hyphens for the rest."""
    text = (title or "").lower().strip()
    text = text.replace("&", " and ")
    text = _SLUG_APOS_RE.sub("", text)
    return _SLUG_JUNK_RE.sub("-", text).strip("-")


def slug_candidates(title: str, year: Optional[int] = None) -> list[str]:
    base = letterboxd_slug(title)
    if not base:
        return []
    candidates = [base]
    if year:
        candidates.append(f"{base}-{year}")
    return candidates


def search_url(title: str, year: Optional[int] = None) -> str:
    slugs = slug_candidates(title, year)
    slug = slugs[0] if slugs else letterboxd_slug(title)
    return f"{LETTERBOXD_ORIGIN}/film/{slug}/"


def pretty_search_url(title: str) -> str:
    return f"{LETTERBOXD_ORIGIN}/search/{quote(title.strip(), safe='')}/"


def parse_item_name(name: str) -> tuple[str, Optional[int]]:
    """Split a label like 'Inception (2010)'."""
    cleaned = re.sub(r"\s+", " ", (name or "").strip())
    match = ITEM_NAME_RE.match(cleaned)
    if match:
        return match.group("title").strip(), int(match.group("year"))
    year_match = YEAR_RE.search(cleaned)
    if year_match:
        year = int(year_match.group(1))
        title = YEAR_RE.sub("", cleaned).strip(" -–—")
        return title or cleaned, year
    return cleaned, None


def film_url_from_href(href: str) -> tuple[str, str]:
    path = href.split("?")[0]
    match = FILM_HREF_RE.search(path)
    slug = match.group(1) if match else path.strip("/").split("/")[-1]
    if path.startswith("http"):
        return path, slug
    return f"{LETTERBOXD_ORIGIN}/film/{slug}/", slug


def looks_like_film_actions_opener(label: str) -> bool:
    """True for the film page bar that opens Watched / Like / Watchlist."""
    blob = (label or "").lower()
    if not blob:
        return False
    if "trailer" in blob:
        return False
    # Sheet row, not the film-page bar ("add to list + more" is the bar).
    if re.search(r"add to lists", blob):
        return False
    return bool(
        re.search(r"rated this film", blob)
        or re.search(r"rate,\s*log", blob)
        or re.search(r"log,\s*review", blob)
        or re.search(r"add to list", blob)
        or re.search(r"\+\s*more", blob)
    )


def film_actions_opener_bounds(xml: str) -> Optional[tuple[int, int, int, int]]:
    """Bounds of the film-page actions bar, if present."""
    if not (xml or "").strip():
        return None
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return None
    screen = _node_bounds(root) or (0, 0, 1080, 1920)
    sw = max(screen[2] - screen[0], 1)
    sh = max(screen[3] - screen[1], 1)
    window = {"width": sw, "height": sh}
    best: Optional[tuple[int, int, int, int]] = None
    best_key: Optional[tuple[int, int, int]] = None
    for el in root.iter():
        own = " ".join(_node_texts(el, descendants=False))
        blob = own
        clickable = (el.attrib.get("clickable") or "").lower() == "true"
        if clickable:
            blob = " ".join(_node_texts(el, descendants=True))
        if not looks_like_film_actions_opener(blob):
            continue
        bounds = _node_bounds(el)
        if bounds is None:
            continue
        width = bounds[2] - bounds[0]
        height = bounds[3] - bounds[1]
        if height > sh * 0.45 or width < 40 or height < 20:
            continue
        rect = {"x": bounds[0], "y": bounds[1], "width": width, "height": height}
        if _in_nav_chrome(rect, window):
            continue
        key = (1 if clickable else 0, width, -height)
        if best_key is None or key > best_key:
            best_key = key
            best = bounds
    return best


def film_actions_opener_from_page_source(xml: str) -> bool:
    return film_actions_opener_bounds(xml) is not None


def looks_signed_out(source: str) -> bool:
    blob = (source or "").lower()
    return bool(
        re.search(r"sign\s*in|log\s*in|create an account|welcome to letterboxd", blob)
        and not re.search(r'content-desc="search"|text="search"', blob)
    )


def looks_logged_in(source: str) -> bool:
    blob = (source or "").lower()
    if looks_signed_out(source):
        return False
    return bool(
        re.search(r'content-desc="search"|text="search"', blob)
        or "watchlist" in blob
        or "profile" in blob
    )


def hits_from_page_source(xml: str) -> list[FilmHit]:
    """Turn an Appium page-source dump of search results into FilmHits.

    Letterboxd Android rows are often Compose nodes whose label is a single
    line like 'Inception 2010, directed by Christopher Nolan', and they may
    not be marked clickable. The search field itself is just 'Inception'
    with no year — those must not count as hits.
    """
    if not (xml or "").strip():
        return []
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        logger.info("page source was not valid XML")
        return []
    screen = _node_bounds(root) or (0, 0, 1080, 1920)
    screen_h = max(screen[3] - screen[1], 1)
    hits: list[FilmHit] = []
    seen: set[str] = set()

    def add(hit: Optional[FilmHit]) -> None:
        if hit is None or hit.year is None or hit.slug in seen:
            return
        seen.add(hit.slug)
        hits.append(hit)

    for el in root.iter():
        for raw in (el.attrib.get("content-desc") or "", el.attrib.get("text") or ""):
            add(_hit_from_result_line(raw))
        if (el.attrib.get("clickable") or "").lower() != "true":
            continue
        bounds = _node_bounds(el)
        if bounds:
            height = bounds[3] - bounds[1]
            if height > screen_h * 0.4 or height < 40:
                continue
        add(_hit_from_texts(_node_texts(el)))
    return hits


def watchlist_state_from_page_source(xml: str) -> WatchlistState:
    if not (xml or "").strip():
        return "missing"
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return "missing"
    found_add = False
    for el in root.iter():
        label = " ".join(
            filter(
                None,
                [
                    el.attrib.get("content-desc") or "",
                    el.attrib.get("text") or "",
                ],
            )
        )
        if not re.search(r"watchlist|want to watch", label, re.I):
            continue
        selected = (el.attrib.get("selected") or "").lower() == "true"
        checked = (el.attrib.get("checked") or "").lower() == "true"
        if re.search(r"remove", label, re.I) or selected or checked:
            return "remove"
        if re.search(r"add", label, re.I) or re.search(
            r"watchlist|want to watch", label, re.I
        ):
            found_add = True
    return "add" if found_add else "missing"


def director_from_page_source(xml: str) -> str:
    if not (xml or "").strip():
        return ""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return ""
    texts = []
    for el in root.iter():
        texts.extend(_node_texts(el, descendants=False))
    seen: set[str] = set()
    ordered: list[str] = []
    for text in texts:
        key = text.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        ordered.append(key)
    for text in ordered:
        match = _DIRECTED_BY_RE.search(text)
        if match:
            return match.group(1).strip().split("\n")[0].strip()
    for index, text in enumerate(ordered):
        if re.fullmatch(r"directors?", text, re.I) and index + 1 < len(ordered):
            nxt = ordered[index + 1].strip()
            if nxt and nxt.lower() not in SKIP_TITLES:
                return nxt
    return ""


def add_to_watchlist(driver: WebDriver, request: WatchlistRequest) -> WatchlistResponse:
    if not _open_search(driver):
        source = _safe_source(driver)
        if looks_signed_out(source):
            return WatchlistResponse(
                outcome=Outcome.auth_required,
                title=request.title,
                year=request.year,
                director=request.director,
                message="Letterboxd looks signed out on the phone",
            )
        return WatchlistResponse(
            outcome=Outcome.error,
            title=request.title,
            year=request.year,
            director=request.director,
            message="could not open Letterboxd search",
        )

    if not _submit_search(driver, request.title):
        return WatchlistResponse(
            outcome=Outcome.error,
            title=request.title,
            year=request.year,
            director=request.director,
            message="could not type the film title into search",
        )

    hits = _wait_for_hits(driver, request.title, request.year)
    decision = pick_match(request.title, hits, request.year, request.director)

    if decision.kind == "ambiguous" and request.director.strip():
        filled = _fill_directors(driver, decision.candidates, request.title)
        decision = pick_match(
            request.title,
            filled,
            request.year,
            request.director,
            use_director=True,
        )

    if decision.kind == "no_match":
        return WatchlistResponse(
            outcome=Outcome.no_match,
            title=request.title,
            year=request.year,
            director=request.director,
            message="no confident Letterboxd match",
        )
    if decision.kind == "ambiguous" or decision.hit is None:
        return WatchlistResponse(
            outcome=Outcome.ambiguous,
            title=request.title,
            year=request.year,
            director=request.director,
            message="several films look equally right; not writing",
            candidates=[item.as_candidate() for item in decision.candidates],
        )

    hit = decision.hit
    if not _open_hit(driver, hit, request.title):
        return WatchlistResponse.from_hit(
            Outcome.error,
            hit,
            "could not open the film in the Letterboxd app",
        )

    source = _safe_source(driver)
    if not hit.director:
        names = director_from_page_source(source)
        if names:
            hit = hit.model_copy(update={"director": names})

    if looks_signed_out(source):
        return WatchlistResponse.from_hit(
            Outcome.auth_required,
            hit,
            "film screen has no watchlist control; session looks logged out",
        )

    if not _ensure_watchlist_sheet(driver):
        _return_to_search(driver)
        return WatchlistResponse.from_hit(
            Outcome.error,
            hit,
            "could not open the film actions sheet",
        )

    state = _watchlist_state(driver)
    if state == "remove":
        _return_to_search(driver)
        return WatchlistResponse.from_hit(
            Outcome.already_on_watchlist,
            hit,
            "already on the watchlist",
        )
    if state == "missing":
        logger.warning("watchlist control missing in the actions sheet")
        _return_to_search(driver)
        return WatchlistResponse.from_hit(
            Outcome.error,
            hit,
            "watchlist control was not in the film actions sheet",
        )

    if not _tap_watchlist(driver):
        _return_to_search(driver)
        return WatchlistResponse.from_hit(
            Outcome.error,
            hit,
            "could not tap the watchlist control",
        )

    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if _watchlist_state(driver) == "remove":
            _return_to_search(driver)
            return WatchlistResponse.from_hit(Outcome.added, hit, "added to the watchlist")
        time.sleep(0.4)

    _return_to_search(driver)
    return WatchlistResponse.from_hit(
        Outcome.error,
        hit,
        "tapped watchlist but the control did not flip to remove",
    )


def _open_search(driver: WebDriver) -> bool:
    """Open the Search tab, then focus the top search bar.

    One tap on the nav icon only shows Browse (Release date, Genre, …).
    The actual text field is the other 'Search' control at the top.
    """
    tab = _lowest_displayed(driver, SEARCH_TAB, timeout=6)
    if tab is None:
        logger.info("Search tab not found")
        return False
    if not _tap_without_scroll(driver, tab):
        return False
    time.sleep(0.45)
    box = _search_box(driver)
    if box is not None:
        _tap_without_scroll(driver, box)
        time.sleep(0.4)
        return True
    # Letterboxd: tap Search again to focus the field and open the keyboard.
    logger.info("top search bar not found; tapping Search tab again to focus it")
    _tap_without_scroll(driver, tab)
    time.sleep(0.4)
    return True


def _submit_search(driver: WebDriver, title: str) -> bool:
    field = _search_box(driver)
    if field is not None:
        try:
            field.clear()
        except Exception:
            pass
    if not _type_title(driver, title, field):
        logger.error("could not type %r into search", title)
        return False
    # Keep the results list on screen; hiding the IME is optional and can
    # race the first page-source dump.
    time.sleep(0.8)
    return True


def _search_box(driver: WebDriver):
    """The top search field, never the bottom/side nav Search icon."""
    try:
        window = driver.get_window_size()
    except Exception:
        window = {"width": 1080, "height": 1920}
    found = _displayed_matches(driver, SEARCH_TAB + SEARCH_FIELD, timeout=3)
    content = []
    for el in found:
        try:
            if not _in_nav_chrome(el.rect, window):
                content.append(el)
        except Exception:
            continue
    if not content:
        return None

    def sort_key(el) -> tuple[int, int]:
        try:
            rect = el.rect
            return (int(rect.get("y") or 0), int(rect.get("x") or 0))
        except Exception:
            return (0, 0)

    return min(content, key=sort_key)


def _in_nav_chrome(rect: dict, window: dict) -> bool:
    """True for the gesture/nav strip (bottom in portrait, right in landscape)."""
    width = max(int(window.get("width") or 1), 1)
    height = max(int(window.get("height") or 1), 1)
    x = float(rect.get("x") or 0) + float(rect.get("width") or 0) / 2
    y = float(rect.get("y") or 0) + float(rect.get("height") or 0) / 2
    if height >= width:
        return y > height * 0.82
    return x > width * 0.82


def _in_search_chrome(rect: dict, window: dict) -> bool:
    """True for the search field strip (top in portrait, left in landscape)."""
    width = max(int(window.get("width") or 1), 1)
    height = max(int(window.get("height") or 1), 1)
    x = float(rect.get("x") or 0) + float(rect.get("width") or 0) / 2
    y = float(rect.get("y") or 0) + float(rect.get("height") or 0) / 2
    if height >= width:
        return y < height * 0.16
    return x < width * 0.22


def _type_title(driver: WebDriver, title: str, field) -> bool:
    """Compose TextFields often reject setText/send_keys even with the IME open."""
    try:
        driver.execute_script("mobile: type", {"text": title})
        logger.info("typed search via mobile: type")
        return True
    except Exception:
        logger.info("mobile: type failed")
    if field is not None:
        try:
            field.send_keys(title)
            return True
        except Exception:
            logger.info("send_keys rejected the field")
    try:
        driver.set_clipboard_text(title)
        time.sleep(0.2)
        driver.press_keycode(_KEYCODE_PASTE)
        logger.info("typed search via clipboard paste")
        return True
    except Exception:
        logger.info("clipboard paste failed")
    return False


def _wait_for_hits(
    driver: WebDriver, title: str, year: Optional[int]
) -> list[FilmHit]:
    deadline = time.monotonic() + 12
    last: list[FilmHit] = []
    while time.monotonic() < deadline:
        last = hits_from_page_source(_safe_source(driver))
        if last:
            titled = [
                hit
                for hit in last
                if titles_match(title, hit.title) and years_match(year, hit.year)
            ]
            if titled:
                logger.info(
                    "search parsed %s row(s); %s matched %r (%s)",
                    len(last),
                    len(titled),
                    title,
                    year,
                )
                return last
        time.sleep(0.5)
    logger.info(
        "search parsed %s row(s) for %r: %s",
        len(last),
        title,
        [(hit.title, hit.year) for hit in last],
    )
    return last


def _open_hit(driver: WebDriver, hit: FilmHit, query: str) -> bool:
    if _tap_result_row(driver, hit):
        time.sleep(0.8)
        return True
    logger.info("row tap missed for %s; retrying search", hit.title)
    if not _open_search(driver):
        return False
    if not _submit_search(driver, query):
        return False
    _wait_for_hits(driver, query, hit.year)
    if not _tap_result_row(driver, hit):
        return False
    time.sleep(0.8)
    return True


def _tap_result_row(driver: WebDriver, hit: FilmHit) -> bool:
    try:
        window = driver.get_window_size()
    except Exception:
        window = {"width": 1080, "height": 1920}
    needles = []
    if hit.year:
        needles.append(f"{hit.title} {hit.year}")
    needles.append(hit.title)
    for needle in needles:
        locators = (
            (
                AppiumBy.ANDROID_UIAUTOMATOR,
                f'new UiSelector().descriptionContains("{_uia_escape(needle)}")',
            ),
            (
                AppiumBy.ANDROID_UIAUTOMATOR,
                f'new UiSelector().textContains("{_uia_escape(needle)}")',
            ),
        )
        for by, value in locators:
            try:
                elements = driver.find_elements(by, value)
            except WebDriverException:
                continue
            for el in elements:
                try:
                    if not el.is_displayed():
                        continue
                    rect = el.rect
                    if _in_nav_chrome(rect, window) or _in_search_chrome(rect, window):
                        continue
                except Exception:
                    continue
                if _tap_without_scroll(driver, el):
                    logger.info("tapped search row %s (%s)", hit.title, hit.year)
                    return True
    logger.info("no visible row for %r", hit.title)
    return False


def _fill_directors(
    driver: WebDriver, candidates: list[FilmHit], query: str
) -> list[FilmHit]:
    filled: list[FilmHit] = []
    for hit in candidates:
        if hit.director.strip():
            filled.append(hit)
            continue
        if not _open_hit(driver, hit, query):
            filled.append(hit)
            continue
        name = director_from_page_source(_safe_source(driver))
        if name:
            hit = hit.model_copy(update={"director": name})
        filled.append(hit)
        driver.back()
        time.sleep(0.6)
        if not _first_displayed(driver, SEARCH_FIELD, timeout=3):
            _open_search(driver)
            _submit_search(driver, query)
            _wait_for_hits(driver, query, hit.year)
    return filled


def _ensure_watchlist_sheet(driver: WebDriver) -> bool:
    """Open Watched / Like / Watchlist if that control is not already on screen."""
    deadline = time.monotonic() + 12
    swipes = 0
    while time.monotonic() < deadline:
        if _watchlist_state(driver) != "missing":
            return True
        if _tap_film_actions_opener(driver):
            if _wait_watchlist_visible(driver, 5):
                return True
        elif swipes < 3:
            logger.info("film actions bar not visible; swiping")
            _swipe_content_up(driver)
            swipes += 1
            time.sleep(0.4)
        else:
            time.sleep(0.3)
    return _watchlist_state(driver) != "missing"


def _wait_watchlist_visible(driver: WebDriver, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _watchlist_state(driver) != "missing":
            return True
        time.sleep(0.3)
    return _watchlist_state(driver) != "missing"


def _element_label(el) -> str:
    try:
        return f"{el.get_attribute('content-desc') or ''} {el.text or ''}".strip()
    except Exception:
        return ""


def _tap_film_actions_opener(driver: WebDriver) -> bool:
    try:
        window = driver.get_window_size()
    except Exception:
        window = {"width": 1080, "height": 1920}
    for el in _displayed_matches(driver, FILM_ACTIONS_OPENER, timeout=1.2):
        try:
            if not looks_like_film_actions_opener(_element_label(el)):
                continue
            if _in_nav_chrome(el.rect, window):
                continue
        except Exception:
            continue
        if _tap_without_scroll(driver, el):
            logger.info("tapped film actions bar")
            return True
    bounds = film_actions_opener_bounds(_safe_source(driver))
    if bounds is None:
        return False
    x = int((bounds[0] + bounds[2]) / 2)
    y = int((bounds[1] + bounds[3]) / 2)
    try:
        driver.execute_script("mobile: clickGesture", {"x": x, "y": y})
        logger.info("tapped film actions bar via page-source bounds")
        return True
    except Exception:
        logger.info("film actions bar clickGesture failed")
        return False


def _swipe_content_up(driver: WebDriver) -> None:
    try:
        window = driver.get_window_size()
    except Exception:
        return
    width = int(window.get("width") or 0)
    height = int(window.get("height") or 0)
    if width < 80 or height < 80:
        return
    try:
        driver.execute_script(
            "mobile: swipeGesture",
            {
                "left": int(width * 0.12),
                "top": int(height * 0.28),
                "width": int(width * 0.65),
                "height": int(height * 0.40),
                "direction": "up",
                "percent": 0.75,
            },
        )
    except Exception:
        logger.info("swipeGesture failed")


def _watchlist_state(
    driver: WebDriver, source: Optional[str] = None
) -> WatchlistState:
    if source is None:
        source = _safe_source(driver)
    parsed = watchlist_state_from_page_source(source)
    if parsed != "missing":
        return parsed
    el = _watchlist_control(driver, timeout=0.4)
    if el is None:
        return "missing"
    try:
        desc = _element_label(el)
        selected = (el.get_attribute("selected") or "").lower() == "true"
        checked = (el.get_attribute("checked") or "").lower() == "true"
    except StaleElementReferenceException:
        return "missing"
    if re.search(r"remove", desc, re.I) or selected or checked:
        return "remove"
    return "add"


def _watchlist_control(driver: WebDriver, timeout: float):
    try:
        window = driver.get_window_size()
    except Exception:
        window = {"width": 1080, "height": 1920}
    for el in _displayed_matches(driver, WATCHLIST_CONTROL, timeout):
        try:
            label = _element_label(el)
            if re.fullmatch(r"watched", label, re.I):
                continue
            if label and not re.search(r"watchlist|want to watch", label, re.I):
                continue
            if _in_nav_chrome(el.rect, window):
                continue
        except Exception:
            continue
        return el
    return None


def _tap_watchlist(driver: WebDriver) -> bool:
    el = _watchlist_control(driver, timeout=6)
    if el is None:
        return False
    return _tap_without_scroll(driver, el)


def _return_to_search(driver: WebDriver) -> None:
    tab = _lowest_displayed(driver, SEARCH_TAB, timeout=2)
    if tab is not None:
        _tap_without_scroll(driver, tab)
        return
    try:
        driver.back()
    except Exception:
        pass


def _first_displayed(
    driver: WebDriver,
    locators: tuple[tuple[str, str], ...],
    timeout: float = 6,
) -> Optional[object]:
    found = _displayed_matches(driver, locators, timeout)
    return found[0] if found else None


def _lowest_displayed(
    driver: WebDriver,
    locators: tuple[tuple[str, str], ...],
    timeout: float = 6,
) -> Optional[object]:
    """Prefer the match nearest the bottom of the screen (nav bar, not feed/ads)."""
    found = _displayed_matches(driver, locators, timeout)
    if not found:
        return None

    def top_y(el) -> int:
        try:
            return int(el.rect.get("y") or el.location.get("y") or 0)
        except Exception:
            return 0

    return max(found, key=top_y)


def _displayed_matches(
    driver: WebDriver,
    locators: tuple[tuple[str, str], ...],
    timeout: float,
) -> list:
    deadline = time.monotonic() + timeout
    while True:
        matches: list = []
        seen: set[str] = set()
        for by, value in locators:
            try:
                elements = driver.find_elements(by, value)
            except WebDriverException:
                continue
            for el in elements:
                try:
                    if not el.is_displayed():
                        continue
                    key = getattr(el, "id", None) or str(el)
                    if key in seen:
                        continue
                    seen.add(key)
                    matches.append(el)
                except Exception:
                    continue
        if matches:
            return matches
        if time.monotonic() >= deadline:
            return []
        time.sleep(0.35)


def _tap_without_scroll(driver: WebDriver, element) -> bool:
    """Tap by coordinates so UiAutomator does not scroll the feed to an ad."""
    try:
        rect = element.rect
        x = int(rect["x"] + rect["width"] / 2)
        y = int(rect["y"] + rect["height"] / 2)
        driver.execute_script("mobile: clickGesture", {"x": x, "y": y})
        return True
    except Exception:
        logger.info("clickGesture failed; falling back to element.click")
        return _click(element)


def _click(element) -> bool:
    try:
        element.click()
        return True
    except Exception:
        logger.info("element click failed")
        return False


def _safe_source(driver: WebDriver) -> str:
    try:
        return driver.page_source or ""
    except Exception:
        logger.info("page_source failed")
        return ""


def _node_bounds(el: ET.Element) -> Optional[tuple[int, int, int, int]]:
    match = _BOUNDS_RE.search(el.attrib.get("bounds") or "")
    if not match:
        return None
    return tuple(int(g) for g in match.groups())  # type: ignore[return-value]


def _node_texts(el: ET.Element, *, descendants: bool = True) -> list[str]:
    texts: list[str] = []
    nodes = el.iter() if descendants else [el]
    for node in nodes:
        for key in ("text", "content-desc"):
            value = (node.attrib.get(key) or "").strip()
            if value:
                texts.append(value)
    return texts


def parse_result_line(text: str) -> Optional[tuple[str, int, str]]:
    """Parse 'Inception 2010, directed by Christopher Nolan' (and similar)."""
    blob = re.sub(r"\s+", " ", text or "").strip()
    if not blob or blob.lower() in SKIP_TITLES:
        return None
    director = ""
    directed = _DIRECTED_BY_RE.search(blob)
    rest = blob
    if directed:
        director = directed.group(1).strip().strip(" ,;")
        rest = blob[: directed.start()].strip(" ,;-")
    named = ITEM_NAME_RE.match(rest)
    if named:
        title = named.group("title").strip()
        year = int(named.group("year"))
        after = ""
    else:
        years = list(YEAR_RE.finditer(rest))
        if not years:
            return None
        last = years[-1]
        year = int(last.group(1))
        title = rest[: last.start()].strip(" ,;-")
        after = rest[last.end() :].strip(" ,;-")
    if not title or title.lower() in SKIP_TITLES:
        return None
    if after and not director:
        director = after
    return title, year, director


def _film_hit(title: str, year: Optional[int], director: str = "") -> Optional[FilmHit]:
    if not title or title.lower() in SKIP_TITLES:
        return None
    slug = letterboxd_slug(title)
    if not slug:
        return None
    return FilmHit(
        title=title,
        year=year,
        url=f"{LETTERBOXD_ORIGIN}/film/{slug}/",
        slug=slug,
        director=director,
    )


def _hit_from_result_line(text: str) -> Optional[FilmHit]:
    parsed = parse_result_line(text)
    if parsed is None:
        return None
    title, year, director = parsed
    return _film_hit(title, year, director)


def _hit_from_texts(texts: list[str]) -> Optional[FilmHit]:
    joined = re.sub(r"\s+", " ", " ".join(texts)).strip()
    hit = _hit_from_result_line(joined)
    if hit is not None:
        return hit
    title: Optional[str] = None
    year: Optional[int] = None
    director = ""
    for raw in texts:
        text = re.sub(r"\s+", " ", raw).strip()
        if not text:
            continue
        lowered = text.lower()
        if lowered in SKIP_TITLES:
            continue
        directed = _DIRECTED_BY_RE.search(text)
        if directed:
            director = directed.group(1).strip()
            rest = _DIRECTED_BY_RE.sub("", text).strip(" ,;-")
            if rest:
                parsed_title, parsed_year = parse_item_name(rest)
                if parsed_title and parsed_title.lower() not in SKIP_TITLES:
                    title = title or parsed_title
                if parsed_year:
                    year = year or parsed_year
            continue
        if re.fullmatch(r"(?:19|20)\d{2}", text):
            year = year or int(text)
            continue
        parsed_title, parsed_year = parse_item_name(text)
        if parsed_year:
            year = year or parsed_year
        if parsed_title.lower() in SKIP_TITLES:
            continue
        if title is None:
            title = parsed_title
        elif not director and parsed_title.lower() != (title or "").lower():
            if not YEAR_RE.search(parsed_title) and len(parsed_title.split()) <= 6:
                director = parsed_title
    if year is None:
        return None
    return _film_hit(title or "", year, director)


def _uia_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


def _uia_regex_escape(text: str) -> str:
    return re.escape(text).replace("\\", "\\\\")
