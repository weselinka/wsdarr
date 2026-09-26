"""Shared domain models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Kind = Literal["movie", "tv", "unknown"]


@dataclass
class MediaContext:
    """What Sonarr/Radarr (or a user) is looking for, resolved to searchable titles."""

    kind: Kind = "unknown"
    # Titles to search/match with, most important first (canonical, localized, original, alternates).
    titles: list[str] = field(default_factory=list)
    # Title Sonarr/Radarr know the item by; used to build release names they can map back.
    canonical_title: str | None = None
    year: int | None = None
    season: int | None = None
    episodes: list[int] = field(default_factory=list)
    tvdb_id: int | None = None
    tmdb_id: int | None = None
    imdb_id: str | None = None
    # Where the context came from: sonarr, radarr, tmdb or query (free text only).
    source: str = "query"

    @property
    def trusted(self) -> bool:
        """True when matched releases may be renamed to ``canonical_title``.

        Set for items identified by ID (Sonarr/Radarr/TMDB) as well as for text searches, where
        the searched text itself is the title the caller (Sonarr's title search or a user in the
        web UI) will map releases back with.
        """
        return bool(self.canonical_title)

    def cache_key(self) -> str:
        return "|".join(
            str(x)
            for x in (
                self.kind,
                self.tvdb_id,
                self.tmdb_id,
                self.imdb_id,
                self.season,
                ",".join(map(str, self.episodes)),
                ";".join(self.titles),
            )
        )
