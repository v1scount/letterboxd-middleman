from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal, Optional

from app.models import FilmHit

_TITLE_STOP_WORDS = frozenset({"a", "an", "the", "film", "movie"})


@dataclass(frozen=True)
class MatchDecision:
    kind: Literal["match", "no_match", "ambiguous"]
    hit: Optional[FilmHit] = None
    candidates: list[FilmHit] = field(default_factory=list)


def _strip_parentheticals(text: str) -> str:
    return re.sub(r"[\(\[][^\)\]]*[\)\]]", " ", text)


def core_title_tokens(title: str, *, strip_subtitle: bool = True) -> list[str]:
    text = _strip_parentheticals(title or "")
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
    """
    titled = [hit for hit in hits if titles_match(title, hit.title, strip_subtitle=False)]
    if not titled:
        titled = [hit for hit in hits if titles_match(title, hit.title)]
    titled = [hit for hit in titled if years_match(year, hit.year)]

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
