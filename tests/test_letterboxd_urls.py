from __future__ import annotations

from pathlib import Path

from app.letterboxd import (
    SEARCH_TAB,
    _in_nav_chrome,
    director_from_page_source,
    film_actions_opener_bounds,
    film_actions_opener_from_page_source,
    film_url_from_href,
    hits_from_page_source,
    letterboxd_slug,
    looks_like_film_actions_opener,
    looks_logged_in,
    looks_signed_out,
    parse_item_name,
    parse_result_line,
    pretty_search_url,
    search_url,
    slug_candidates,
    watchlist_state_from_page_source,
)
from app.matching import pick_match
from app.models import WatchlistRequest

FIXTURES = Path(__file__).parent / "fixtures"


def _xml(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_letterboxd_slug() -> None:
    assert letterboxd_slug("Inception") == "inception"
    assert letterboxd_slug("The Matrix") == "the-matrix"
    assert letterboxd_slug("Dune: Part Two") == "dune-part-two"
    assert letterboxd_slug("Ocean's Eleven") == "oceans-eleven"
    assert letterboxd_slug("Crazy, Stupid, Love") == "crazy-stupid-love"


def test_slug_candidates_include_year_suffix() -> None:
    assert slug_candidates("Inception") == ["inception"]
    assert slug_candidates("Inception", 2010) == ["inception", "inception-2010"]


def test_search_url_opens_film_page() -> None:
    assert search_url("Inception") == "https://letterboxd.com/film/inception/"
    assert search_url("The Matrix", 1999) == "https://letterboxd.com/film/the-matrix/"
    assert pretty_search_url("Inception") == "https://letterboxd.com/search/Inception/"


def test_parse_item_name() -> None:
    assert parse_item_name("Inception (2010)") == ("Inception", 2010)
    assert parse_item_name("Dune: Part One (2021)") == ("Dune: Part One", 2021)
    assert parse_item_name("Inception") == ("Inception", None)


def test_film_url_from_href() -> None:
    url, slug = film_url_from_href("/film/inception/")
    assert slug == "inception"
    assert url == "https://letterboxd.com/film/inception/"
    url, slug = film_url_from_href("https://letterboxd.com/film/dune-2021/?foo=1")
    assert slug == "dune-2021"
    assert url.startswith("https://letterboxd.com/film/dune-2021")


def test_watchlist_request_rejects_empty_title() -> None:
    import pytest

    with pytest.raises(ValueError):
        WatchlistRequest(title="  ")


def test_search_locator_uses_accessibility_id() -> None:
    assert any(value == "Search" for _by, value in SEARCH_TAB)


def test_hits_from_search_page_source() -> None:
    hits = hits_from_page_source(_xml("search_results.xml"))
    slugs = [hit.slug for hit in hits]
    assert "inception" in slugs
    assert "inception-the-cobol-job" in slugs
    assert "dune" in slugs
    assert "search" not in slugs
    assert "profile" not in slugs
    inception = next(hit for hit in hits if hit.slug == "inception")
    assert inception.year == 2010
    assert inception.director == "Christopher Nolan"
    dune = next(hit for hit in hits if hit.slug == "dune")
    assert dune.year == 2021
    assert "Villeneuve" in dune.director


def test_parse_result_line_android_label() -> None:
    assert parse_result_line("Inception 2010, directed by Christopher Nolan") == (
        "Inception",
        2010,
        "Christopher Nolan",
    )
    assert parse_result_line("Inception 1980, Экпал") == ("Inception", 1980, "Экпал")
    assert parse_result_line("Inception") is None


def test_hits_from_compose_search_results() -> None:
    hits = hits_from_page_source(_xml("search_results_compose.xml"))
    by_year = {(hit.title, hit.year): hit for hit in hits}
    assert ("Inception", 2010) in by_year
    assert by_year[("Inception", 2010)].director == "Christopher Nolan"
    assert ("Inception: The Cobol Job", 2010) in by_year
    assert all(hit.slug != "search" for hit in hits)
    decision = pick_match("Inception", hits, year=2010, director="Christopher Nolan")
    assert decision.kind == "match"
    assert decision.hit is not None
    assert decision.hit.year == 2010
    assert decision.hit.slug == "inception"


def test_watchlist_state_from_film_page_source() -> None:
    assert watchlist_state_from_page_source(_xml("film_page_add.xml")) == "add"
    assert watchlist_state_from_page_source(_xml("film_page_remove.xml")) == "remove"
    assert watchlist_state_from_page_source(_xml("search_results.xml")) == "missing"
    assert watchlist_state_from_page_source(_xml("film_page_rated.xml")) == "missing"
    assert watchlist_state_from_page_source(_xml("film_page_unlogged.xml")) == "missing"
    assert watchlist_state_from_page_source(_xml("film_actions_sheet_add.xml")) == "add"
    assert watchlist_state_from_page_source(_xml("film_actions_sheet_remove.xml")) == "remove"


def test_film_actions_opener_copy_variants() -> None:
    assert looks_like_film_actions_opener("You've rated this film ★★★★")
    assert looks_like_film_actions_opener("Rate, log, review, add to list + more")
    assert not looks_like_film_actions_opener("TRAILER")
    assert not looks_like_film_actions_opener("+ Add to lists")
    assert not looks_like_film_actions_opener("+ Review or log")


def test_film_actions_opener_from_page_source() -> None:
    rated = _xml("film_page_rated.xml")
    unlogged = _xml("film_page_unlogged.xml")
    assert film_actions_opener_from_page_source(rated)
    assert film_actions_opener_from_page_source(unlogged)
    assert film_actions_opener_bounds(rated) == (48, 1100, 1032, 1240)
    assert not film_actions_opener_from_page_source(_xml("film_actions_sheet_add.xml"))
    assert not film_actions_opener_from_page_source(_xml("film_page_add.xml"))


def test_director_from_film_page_source() -> None:
    assert director_from_page_source(_xml("film_page_add.xml")) == "Christopher Nolan"


def test_login_state_from_page_source() -> None:
    assert looks_signed_out(_xml("signed_out.xml"))
    assert not looks_logged_in(_xml("signed_out.xml"))
    assert looks_logged_in(_xml("search_results.xml"))
    assert not looks_signed_out(_xml("search_results.xml"))


def test_nav_chrome_is_bottom_in_portrait() -> None:
    window = {"width": 1080, "height": 1920}
    nav = {"x": 216, "y": 1800, "width": 200, "height": 120}
    bar = {"x": 48, "y": 80, "width": 900, "height": 120}
    assert _in_nav_chrome(nav, window)
    assert not _in_nav_chrome(bar, window)


def test_nav_chrome_is_right_edge_in_landscape() -> None:
    window = {"width": 1920, "height": 1080}
    nav = {"x": 1750, "y": 400, "width": 140, "height": 140}
    bar = {"x": 40, "y": 40, "width": 700, "height": 100}
    release_date = {"x": 40, "y": 280, "width": 700, "height": 80}
    assert _in_nav_chrome(nav, window)
    assert not _in_nav_chrome(bar, window)
    assert not _in_nav_chrome(release_date, window)
