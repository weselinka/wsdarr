"""Turn Newznab search parameters (IDs and/or free text) into a :class:`MediaContext`.

Sources, in order of authority:
1. Sonarr/Radarr libraries – the canonical title they will map releases back with, plus their
   alternate titles.
2. TMDB (optional) – localized Czech/Slovak titles, original and English titles.
3. The free-text query itself.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

import httpx
from guessit import guessit

from ..db import Database
from ..matching.parser import normalize
from ..models import Kind, MediaContext
from .arr import ArrClient, ArrError
from .tmdb import TmdbClient, titles_from_details

log = logging.getLogger(__name__)

LOOKUP_TTL = 6 * 3600
TMDB_TTL = 7 * 24 * 3600
LIBRARY_TTL = 600


class MetadataResolver:
    def __init__(
        self,
        sonarrs: list[ArrClient],
        radarrs: list[ArrClient],
        tmdb: TmdbClient | None,
        db: Database,
    ):
        self.sonarrs = sonarrs
        self.radarrs = radarrs
        self.tmdb = tmdb if tmdb and tmdb.enabled else None
        self.db = db
        self._library: dict[str, tuple[float, list[dict]]] = {}

    # --- helpers -------------------------------------------------------------------------
    async def _cached(self, key: str, ttl: float, factory) -> Any:
        cached = self.db.cache_get(key)
        if cached is not None:
            return cached.get("v")
        try:
            value = await factory()
        except (ArrError, httpx.HTTPError) as exc:
            log.warning("Metadata lookup %s failed: %s", key, exc)
            return None
        self.db.cache_set(key, {"v": value}, ttl)
        return value

    async def _library_items(self, client: ArrClient) -> list[dict]:
        key = f"{client.kind}:{client.name}"
        cached = self._library.get(key)
        if cached and cached[0] > time.monotonic():
            return cached[1]
        try:
            items = await (client.all_series() if client.kind == "sonarr" else client.all_movies())
        except (ArrError, httpx.HTTPError) as exc:
            log.warning("Could not list %s library: %s", client.name, exc)
            items = []
        slim = [
            {
                "title": i.get("title"),
                "year": i.get("year"),
                "alternateTitles": [{"title": a.get("title")} for a in i.get("alternateTitles") or []],
                "originalTitle": i.get("originalTitle"),
                "tvdbId": i.get("tvdbId"),
                "tmdbId": i.get("tmdbId"),
                "imdbId": i.get("imdbId"),
            }
            for i in items
        ]
        self._library[key] = (time.monotonic() + LIBRARY_TTL, slim)
        return slim

    async def _find_in_library(self, clients: list[ArrClient], title: str, year: int | None) -> dict | None:
        wanted = normalize(title)
        if not wanted:
            return None
        for client in clients:
            for item in await self._library_items(client):
                names = [item.get("title"), item.get("originalTitle")]
                names += [a.get("title") for a in item.get("alternateTitles") or []]
                if any(n and normalize(n) == wanted for n in names) and (
                    not year or not item.get("year") or abs(int(item["year"]) - year) <= 1
                ):
                    return item
        return None

    async def _sonarr_series(self, tvdb_id: int | None) -> dict | None:
        if not tvdb_id:
            return None
        for client in self.sonarrs:
            series = await self._cached(
                f"sonarr:{client.name}:tvdb:{tvdb_id}", LOOKUP_TTL, lambda c=client: c.series_by_tvdb(tvdb_id)
            )
            if series:
                return series
        return None

    async def _radarr_movie(self, tmdb_id: int | None, imdb_id: str | None) -> dict | None:
        for client in self.radarrs:
            if tmdb_id:
                movie = await self._cached(
                    f"radarr:{client.name}:tmdb:{tmdb_id}",
                    LOOKUP_TTL,
                    lambda c=client: c.movie_by_tmdb(tmdb_id),
                )
                if movie:
                    return movie
            if imdb_id:
                movie = await self._cached(
                    f"radarr:{client.name}:imdb:{imdb_id}",
                    LOOKUP_TTL,
                    lambda c=client: c.movie_lookup_imdb(imdb_id),
                )
                if movie:
                    return movie
        return None

    async def _tmdb_info(self, ctx: MediaContext, query: str | None) -> dict | None:
        if not self.tmdb or ctx.kind == "unknown":
            return None
        kind = ctx.kind
        tmdb = self.tmdb
        tmdb_id = ctx.tmdb_id
        if not tmdb_id:
            for ext_id, source in ((ctx.tvdb_id, "tvdb_id"), (ctx.imdb_id, "imdb_id")):
                if not ext_id:
                    continue
                found = await self._cached(
                    f"tmdb:find:{source}:{ext_id}", TMDB_TTL, lambda e=ext_id, s=source: tmdb.find(e, s)
                )
                results = (found or {}).get("movie_results" if kind == "movie" else "tv_results") or []
                if results:
                    tmdb_id = results[0]["id"]
                    break
        if not tmdb_id and query:
            results = await self._cached(
                f"tmdb:search:{kind}:{normalize(query)}:{ctx.year}",
                TMDB_TTL,
                lambda: tmdb.search(kind, query, ctx.year),
            )
            if results:
                tmdb_id = results[0]["id"]
        if not tmdb_id:
            return None
        details = await self._cached(
            f"tmdb:{kind}:{tmdb_id}:{tmdb.language}", TMDB_TTL, lambda: tmdb.details(kind, tmdb_id)
        )
        if not details:
            return None
        info = titles_from_details(details, kind)
        info["tmdb_id"] = tmdb_id
        return info

    # --- public --------------------------------------------------------------------------
    async def resolve(
        self,
        kind: Kind,
        *,
        q: str | None = None,
        tvdb_id: int | None = None,
        tmdb_id: int | None = None,
        imdb_id: str | None = None,
        season: int | None = None,
        episodes: list[int] | None = None,
        year: int | None = None,
    ) -> MediaContext:
        query_title = None
        if q:
            query_title, q_year, q_season, q_episodes, q_kind = parse_query(q)
            year = year or q_year
            season = season if season is not None else q_season
            episodes = episodes or q_episodes
            if kind == "unknown":
                kind = q_kind
        if imdb_id and not str(imdb_id).startswith("tt"):
            imdb_id = f"tt{int(imdb_id):07d}"

        ctx = MediaContext(
            kind=kind,
            season=season,
            episodes=list(episodes or []),
            tvdb_id=tvdb_id,
            tmdb_id=tmdb_id,
            imdb_id=imdb_id,
            year=year,
        )
        titles: list[str] = []
        library_titles: list[str] = []

        item: dict | None = None
        if kind == "tv":
            item = await self._sonarr_series(tvdb_id)
            if not item and query_title and not tvdb_id:
                item = await self._find_in_library(self.sonarrs, query_title, None)
            source = "sonarr"
        elif kind == "movie":
            item = await self._radarr_movie(tmdb_id, imdb_id)
            if not item and query_title and not (tmdb_id or imdb_id):
                item = await self._find_in_library(self.radarrs, query_title, year)
            source = "radarr"
        if item:
            ctx.source = source
            ctx.canonical_title = item.get("title")
            ctx.year = item.get("year") or ctx.year
            ctx.tvdb_id = ctx.tvdb_id or item.get("tvdbId") or None
            ctx.tmdb_id = ctx.tmdb_id or item.get("tmdbId") or None
            ctx.imdb_id = ctx.imdb_id or item.get("imdbId") or None
            titles.append(item["title"])
            library_titles = [item.get("originalTitle")] + [
                a.get("title") for a in item.get("alternateTitles") or []
            ]

        info = await self._tmdb_info(ctx, query_title)
        if info:
            ctx.tmdb_id = ctx.tmdb_id or info.get("tmdb_id")
            ctx.imdb_id = ctx.imdb_id or info.get("imdb_id")
            ctx.tvdb_id = ctx.tvdb_id or info.get("tvdb_id")
            ctx.year = ctx.year or info.get("year")
            titles.extend(info["local"])
            titles.extend([info.get("original"), info.get("english")])
            if not ctx.canonical_title:
                ctx.canonical_title = info.get("english") or info.get("original")
                ctx.source = "tmdb"

        if query_title:
            titles.append(query_title)
            if not ctx.canonical_title:
                ctx.canonical_title = query_title
        titles.extend(library_titles)

        seen: set[str] = set()
        for title in titles:
            norm = normalize(title or "")
            if norm and norm not in seen:
                seen.add(norm)
                ctx.titles.append(title)  # type: ignore[arg-type]
        return ctx


def parse_query(q: str) -> tuple[str, int | None, int | None, list[int], Kind]:
    """Split a free-text query ("Breaking Bad S01E02", "Dune 2021") into its parts."""
    text = q.strip()
    try:
        info = dict(guessit(text, {"type": None}))
    except Exception:  # guessit can raise on odd input
        info = {}
    season = info.get("season") if isinstance(info.get("season"), int) else None
    episode = info.get("episode")
    episodes = [e for e in (episode if isinstance(episode, list) else [episode]) if isinstance(e, int)]
    # Keep the typed title verbatim (guessit drops subtitles such as "A New Hope"); only cut
    # the season/episode marker and a trailing year.
    title = re.split(r"(?i)\s+(?:s\d{1,2}(?:\s*e\d{1,3})*|\d{1,2}x\d{1,3})\b", text)[0].strip()
    year = None
    m = re.search(r"\s\(?((?:19|20)\d{2})\)?$", title)
    if m:
        year, title = int(m.group(1)), title[: m.start()].strip()
    title = title or text
    kind: Kind = "tv" if (season is not None or episodes) else ("movie" if year else "unknown")
    return title, year, season, episodes, kind
