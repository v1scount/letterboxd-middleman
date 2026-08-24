from __future__ import annotations

from app.matching import (
    directors_overlap,
    needs_director_probe,
    pick_match,
    titles_match,
    years_match,
)
from app.models import FilmHit


def hit(title: str, year: int | None = None, director: str = "", slug: str = "") -> FilmHit:
    slug = slug or title.lower().replace(" ", "-")
    return FilmHit(
        title=title,
        year=year,
        url=f"https://letterboxd.com/film/{slug}/",
        slug=slug,
        director=director,
    )


def test_titles_match_ignores_the_and_punctuation() -> None:
    assert titles_match("The Matrix", "Matrix")
    assert titles_match("inception", "Inception")
    assert not titles_match("Inception", "Interstellar")


def test_titles_match_strips_subtitle_after_colon() -> None:
    assert titles_match("Dune", "Dune: Part One")
    assert titles_match("Blade Runner 2049", "Blade Runner 2049: The Final Cut")
    assert titles_match("Mad Max", "Mad Max: Fury Road")
    assert not titles_match("Inception", "Inception: The Cobol Job", strip_subtitle=False)


def test_pick_match_prefers_exact_title_over_colon_subtitle() -> None:
    hits = [
        hit("Inception", 2010, slug="inception"),
        hit("Inception: The Cobol Job", 2010, slug="inception-the-cobol-job"),
    ]
    decision = pick_match("Inception", hits, year=2010)
    assert decision.kind == "match"
    assert decision.hit is not None
    assert decision.hit.slug == "inception"


def test_titles_match_empty_is_not_a_match() -> None:
    assert not titles_match("", "Inception")
    assert not titles_match("The", "The")


def test_years_match_only_when_client_sent_a_year() -> None:
    assert years_match(None, 2010)
    assert years_match(2010, 2010)
    assert not years_match(2010, 2021)
    assert not years_match(2010, None)


def test_directors_overlap_on_shared_name_token() -> None:
    assert directors_overlap("Christopher Nolan", "Christopher Nolan")
    assert directors_overlap("Nolan", "Christopher Nolan")
    assert not directors_overlap("Villeneuve", "Christopher Nolan")
    assert directors_overlap("", "Anyone")


def test_pick_match_single_title() -> None:
    decision = pick_match("Inception", [hit("Inception", 2010), hit("Interstellar", 2014)])
    assert decision.kind == "match"
    assert decision.hit is not None
    assert decision.hit.year == 2010


def test_pick_match_year_disambiguates_remake() -> None:
    hits = [hit("Dune", 1984, slug="dune"), hit("Dune", 2021, slug="dune-2021")]
    decision = pick_match("Dune", hits, year=2021)
    assert decision.kind == "match"
    assert decision.hit is not None
    assert decision.hit.slug == "dune-2021"


def test_pick_match_no_year_two_remakes_is_ambiguous() -> None:
    hits = [hit("Dune", 1984, slug="dune"), hit("Dune", 2021, slug="dune-2021")]
    decision = pick_match("Dune", hits)
    assert decision.kind == "ambiguous"
    assert len(decision.candidates) == 2


def test_pick_match_director_breaks_title_year_tie() -> None:
    hits = [
        hit("The Tourist", 2010, director="Florian Henckel von Donnersmarck", slug="the-tourist"),
        hit("The Tourist", 2010, director="Someone Else", slug="the-tourist-2010"),
    ]
    decision = pick_match(
        "The Tourist",
        hits,
        year=2010,
        director="Donnersmarck",
        use_director=True,
    )
    assert decision.kind == "match"
    assert decision.hit is not None
    assert decision.hit.slug == "the-tourist"


def test_pick_match_director_miss_is_no_match() -> None:
    hits = [
        hit("Heat", 1995, director="Michael Mann"),
        hit("Heat", 1995, director="Paul Kelly"),
    ]
    decision = pick_match("Heat", hits, year=1995, director="Nolan", use_director=True)
    assert decision.kind == "no_match"


def test_pick_match_nothing_confident() -> None:
    decision = pick_match("Inception", [hit("Interstellar", 2014)])
    assert decision.kind == "no_match"


def test_pick_match_year_mismatch_is_no_match() -> None:
    decision = pick_match("Inception", [hit("Inception", 2010)], year=1999)
    assert decision.kind == "no_match"


def test_needs_director_probe_only_for_ambiguous_without_directors() -> None:
    tied = pick_match("Dune", [hit("Dune", 1984), hit("Dune", 2021)])
    assert needs_director_probe(tied, "Denis Villeneuve")
    assert not needs_director_probe(tied, "")
    filled = pick_match(
        "Dune",
        [
            hit("Dune", 1984, director="David Lynch"),
            hit("Dune", 2021, director="Denis Villeneuve"),
        ],
    )
    assert not needs_director_probe(filled, "Villeneuve")
