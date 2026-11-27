from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal, Optional

from app.models import FilmHit

_TITLE_STOP_WORDS = frozenset({"a", "an", "the", "film", "movie"})
# Compose prefixes poster chrome onto the film title in content-desc.
_UI_CHROME_RE = re.compile(
    r"^(?:poster image(?:\s+poster overlay)?(?:\s+shadow)?\s+)+",
    re.I,
)
_UI_CHROME_LABELS = frozenset({"poster image", "poster overlay"})


@dataclass(frozen=True)
class MatchDecision:
    kind: Literal["match", "no_match", "ambiguous"]
    hit: Optional[FilmHit] = None
    candidates: list[FilmHit] = field(default_factory=list)


def strip_ui_chrome(title: str) -> str:
    """Drop Letterboxd Android poster overlay labels from a parsed title."""
    text = _UI_CHROME_RE.sub("", (title or "").strip()).strip()
    if text.lower() in _UI_CHROME_LABELS:
        return ""
    return text


def _strip_parentheticals(text: str) -> str:
    return re.sub(r"[\(\[][^\)\]]*[\)\]]", " ", text)


def core_title_tokens(title: str, *, strip_subtitle: bool = True) -> list[str]:
    text = _strip_parentheticals(strip_ui_chrome(title or ""))
    if strip_subtitle:
        text = re.split(r"[:\u2014\u2013]", text, maxsplit=1)[0]
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    return [
        token
        for token in text.split()
        if token and token not in _TITLE_STOP_WORDS
    ]


def titles_match(
    extracted: str,
    hit_title: str,
    *,
    strip_subtitle: bool = True,
) -> bool:
    left = core_title_tokens(extracted, strip_subtitle=strip_subtitle)
    right = core_title_tokens(hit_title, strip_subtitle=strip_subtitle)
    return bool(left) and left == right


def years_match(wanted: Optional[int], hit_year: Optional[int]) -> bool:
    if wanted is None:
        return True
    return hit_year == wanted


def _name_tokens(name: str) -> set[str]:
    text = (name or "").lower()
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    return {token for token in text.split() if len(token) > 1}


def directors_overlap(extracted: str, hit_director: str) -> bool:
    wanted = _name_tokens(extracted)
    if not wanted:
        return True
    return bool(wanted & _name_tokens(hit_director))


def _film_key(hit: FilmHit, *, strip_subtitle: bool = False) -> tuple:
    return (tuple(core_title_tokens(hit.title, strip_subtitle=strip_subtitle)), hit.year)


def _prefer_cleaner(left: FilmHit, right: FilmHit) -> FilmHit:
    """Keep the row whose title is closer to the real film name."""
    left_title = strip_ui_chrome(left.title)
    right_title = strip_ui_chrome(right.title)
    if len(right_title) < len(left_title):
        return right
    if len(right_title) == len(left_title) and len(right.title) < len(left.title):
        return right
    return left


def collapse_duplicate_hits(hits: list[FilmHit]) -> list[FilmHit]:
    """Poster chrome and the title node are the same Letterboxd row."""
    best: dict[tuple, FilmHit] = {}
    order: list[tuple] = []
    for hit in hits:
        key = _film_key(hit)
        if not key[0]:
            continue
        if key not in best:
            order.append(key)
            best[key] = hit
        else:
            best[key] = _prefer_cleaner(best[key], hit)
    return [best[key] for key in order]


def pick_match(
    title: str,
    hits: list[FilmHit],
    year: Optional[int] = None,
    director: str = "",
    *,
    use_director: bool = False,
) -> MatchDecision:
    """Conservative pick: skip rather than guess.

    `use_director` is True only after film pages have been opened and
    `FilmHit.director` is populated. Until then, director is ignored so we
    can ask the caller to fetch it for the remaining ties.

    Year disambiguates remakes. A unique title match is kept even when the
    year is off — Letterboxd search already returned that one film.
    """
    hits = collapse_duplicate_hits(hits)
    titled = [hit for hit in hits if titles_match(title, hit.title, strip_subtitle=False)]
    if not titled:
        titled = [hit for hit in hits if titles_match(title, hit.title)]

    year_ok = [hit for hit in titled if years_match(year, hit.year)]
    if year_ok:
        titled = year_ok
    elif len(titled) != 1:
        return MatchDecision(kind="no_match")

    if use_director and director.strip():
        titled = [hit for hit in titled if directors_overlap(director, hit.director)]

    if not titled:
        return MatchDecision(kind="no_match")
    if len(titled) == 1:
        return MatchDecision(kind="match", hit=titled[0])
    return MatchDecision(kind="ambiguous", candidates=titled)


def needs_director_probe(
    decision: MatchDecision,
    director: str,
) -> bool:
    """True when title+year tied and a director could still break the tie."""
    return (
        decision.kind == "ambiguous"
        and bool(director.strip())
        and any(not hit.director for hit in decision.candidates)
    )
