from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class Outcome(str, Enum):
    added = "added"
    already_on_watchlist = "already_on_watchlist"
    no_match = "no_match"
    ambiguous = "ambiguous"
    auth_required = "auth_required"
    error = "error"


class WatchlistRequest(BaseModel):
    title: str
    year: Optional[int] = None
    director: str = ""

    @field_validator("title")
    @classmethod
    def title_required(cls, value: str) -> str:
        cleaned = (value or "").strip()
        if not cleaned:
            raise ValueError("title is required")
        return cleaned

    @field_validator("director", mode="before")
    @classmethod
    def coerce_director(cls, value: object) -> object:
        if value is None:
            return ""
        return str(value).strip()

    @field_validator("year")
    @classmethod
    def year_range(cls, value: Optional[int]) -> Optional[int]:
        if value is None:
            return None
        if value < 1870 or value > 2100:
            raise ValueError("year looks implausible")
        return value


class FilmCandidate(BaseModel):
    title: str
    year: Optional[int] = None
    url: str
    director: str = ""


class FilmHit(BaseModel):
    """A parsed Letterboxd search row. Director is filled only when we open the film."""

    title: str
    year: Optional[int] = None
    url: str
    slug: str = ""
    director: str = ""

    def as_candidate(self) -> FilmCandidate:
        return FilmCandidate(
            title=self.title,
            year=self.year,
            url=self.url,
            director=self.director,
        )


class WatchlistResponse(BaseModel):
    outcome: Outcome
    title: str = ""
    year: Optional[int] = None
    director: str = ""
    url: str = ""
    message: str = ""
    candidates: list[FilmCandidate] = Field(default_factory=list)

    @classmethod
    def from_hit(cls, outcome: Outcome, hit: FilmHit, message: str = "") -> WatchlistResponse:
        return cls(
            outcome=outcome,
            title=hit.title,
            year=hit.year,
            director=hit.director,
            url=hit.url,
            message=message,
        )
