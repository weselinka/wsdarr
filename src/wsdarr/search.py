"""Search Webshare for a :class:`MediaContext` and turn matching files into releases."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field

from .config import Settings
from .matching.matcher import match
from .matching.naming import build_release_title, newznab_languages
from .matching.parser import ParsedFile, normalize, parse_filename, strip_diacritics
from .models import Kind, MediaContext
from .webshare import WebshareClient, WebshareError, WsFile

log = logging.getLogger(__name__)

MB = 1024 * 1024


@dataclass
class Release:
    ident: str
    title: str
    ws_name: str
    size: int
    kind: Kind
    languages: list[str] = field(default_factory=list)
    season: int | None = None
    episodes: list[int] = field(default_factory=list)
    resolution: str | None = None
    tvdb_id: int | None = None
    tmdb_id: int | None = None
    imdb_id: str | None = None
    votes: int = 0
    score: int = 0
    pub_date: float = field(default_factory=time.time)

    @property
    def categories(self) -> list[int]:
        base = 5000 if self.kind == "tv" else 2000
        if self.resolution == "2160p":
            sub = base + 45
        elif self.resolution in ("720p", "1080p", "1080i"):
            sub = base + 40
        else:
            sub = base + 30
        return [base, sub]

    def summary(self) -> dict:
        return {
            "ident": self.ident,
            "title": self.title,
            "ws_name": self.ws_name,
            "size": self.size,
            "kind": self.kind,
            "languages": self.languages,
            "resolution": self.resolution,
            "votes": self.votes,
            "score": self.score,
        }


def _query_text(title: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w]+", " ", title)).strip()


class SearchService:
    def __init__(self, webshare: WebshareClient, settings: Settings):
        self.ws = webshare
        self.settings = settings
        self._cache: dict[str, tuple[float, list[Release]]] = {}

    # --- queries -------------------------------------------------------------------------
    def queries_for(self, ctx: MediaContext) -> tuple[list[str], list[str]]:
        """Return (specific queries fetched once, broad title queries that may be paged)."""
        titles: list[str] = []
        for title in ctx.titles[: self.settings.max_alt_titles]:
            for variant in (_query_text(title), _query_text(strip_diacritics(title))):
                if variant and variant.lower() not in (t.lower() for t in titles):
                    titles.append(variant)
        specific: list[str] = []
        if ctx.kind == "tv" and ctx.season is not None:
            for title in titles:
                if ctx.episodes:
                    ep = ctx.episodes[0]
                    specific.append(f"{title} S{ctx.season:02d}E{ep:02d}")
                    specific.append(f"{title} {ctx.season}x{ep:02d}")
                else:
                    specific.append(f"{title} S{ctx.season:02d}")
        return specific, titles

    async def _fetch(self, what: str, pages: int, sort: str = "") -> list[WsFile]:
        files: list[WsFile] = []
        limit = self.settings.search_page_size
        for page in range(pages):
            try:
                result = await self.ws.search(
                    what, category="video", sort=sort, limit=limit, offset=page * limit
                )
            except WebshareError as exc:
                log.warning("Webshare search %r failed: %s", what, exc)
                break
            files.extend(result.files)
            if len(result.files) < limit or (page + 1) * limit >= result.total:
                break
        return files

    # --- search --------------------------------------------------------------------------
    async def search(self, ctx: MediaContext) -> list[Release]:
        key = ctx.cache_key()
        cached = self._cache.get(key)
        if cached and cached[0] > time.monotonic():
            return cached[1]

        specific, broad = self.queries_for(ctx)
        tasks = [self._fetch(q, 1) for q in specific]
        tasks += [self._fetch(q, self.settings.search_max_pages) for q in broad]
        results = await asyncio.gather(*tasks)

        releases = self.filter_files([f for batch in results for f in batch], ctx)
        self._cache[key] = (time.monotonic() + self.settings.search_cache_ttl, releases)
        if len(self._cache) > 500:
            now = time.monotonic()
            self._cache = {k: v for k, v in self._cache.items() if v[0] > now}
        log.info(
            "Search %s (%s) -> %d queries, %d releases",
            ctx.canonical_title or ctx.titles[:1],
            ctx.kind,
            len(tasks),
            len(releases),
        )
        return releases

    async def recent(self) -> list[Release]:
        """Newest videos on Webshare (used for RSS sync and the indexer test)."""
        if self.settings.rss_mode == "off":
            return []
        files = await self._fetch(self.settings.rss_query, 1, sort="recent")
        return self.filter_files(files[: self.settings.rss_limit], None)

    def filter_files(self, files: list[WsFile], ctx: MediaContext | None) -> list[Release]:
        min_size = self.settings.min_file_size_mb * MB
        best: dict[str, tuple[WsFile, ParsedFile, int]] = {}
        seen_idents: set[str] = set()
        for f in files:
            if f.ident in seen_idents:
                continue
            seen_idents.add(f.ident)
            if f.password or f.size < min_size or not f.ident:
                continue
            parsed = parse_filename(f.name)
            if ctx is not None:
                result = match(parsed, ctx, self.settings.match_threshold)
                if not result.ok:
                    log.debug("Rejected %r: %s", f.name, result.reason)
                    continue
                score = result.score
            else:
                if not parsed.is_video or parsed.is_sample:
                    continue
                score = 0
            # The same file is often uploaded several times; keep the best voted copy.
            dedupe_key = f"{normalize(f.name)}|{f.size}"
            current = best.get(dedupe_key)
            votes = f.positive_votes - f.negative_votes
            if current is None or votes > current[0].positive_votes - current[0].negative_votes:
                best[dedupe_key] = (f, parsed, score)

        releases: list[Release] = []
        for f, parsed, score in best.values():
            kind: Kind = (
                ctx.kind if ctx and ctx.kind != "unknown" else ("tv" if parsed.is_episode else "movie")
            )
            releases.append(
                Release(
                    ident=f.ident,
                    title=build_release_title(
                        parsed, ctx, self.settings.release_group, self.settings.unknown_quality == "extension"
                    ),
                    ws_name=f.name,
                    size=f.size,
                    kind=kind,
                    languages=newznab_languages(parsed),
                    season=parsed.season if parsed.season is not None else (ctx.season if ctx else None),
                    episodes=parsed.episodes,
                    resolution=parsed.resolution,
                    tvdb_id=ctx.tvdb_id if ctx else None,
                    tmdb_id=ctx.tmdb_id if ctx else None,
                    imdb_id=ctx.imdb_id if ctx else None,
                    votes=f.positive_votes - f.negative_votes,
                    score=score,
                )
            )
        releases.sort(key=lambda r: (r.score, r.votes, r.size), reverse=True)
        return releases
