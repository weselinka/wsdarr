"""Newznab indexer API: ``t=caps``, ``t=search|tvsearch|movie`` and ``t=get``."""

from __future__ import annotations

import logging
import re
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import Response

from ..models import Kind
from ..search import Release
from .nzb import NzbInfo, build_nzb, decode_release_id, encode_release_id
from .xml import caps_xml, error_xml, rss_xml

log = logging.getLogger(__name__)

router = APIRouter()

XML = "application/xml; charset=utf-8"
SEARCH_FUNCTIONS = {
    "search": "unknown",
    "tvsearch": "tv",
    "tv": "tv",
    "movie": "movie",
    "movie-search": "movie",
}


def _int(value: str | None) -> int | None:
    if value is None or value == "":
        return None
    m = re.search(r"\d+", value)
    return int(m.group()) if m else None


def _categories(value: str | None) -> list[int]:
    return [int(c) for c in re.findall(r"\d+", value or "")]


def _kind_from_categories(cats: list[int]) -> Kind:
    if cats and all(5000 <= c < 6000 for c in cats):
        return "tv"
    if cats and all(2000 <= c < 3000 for c in cats):
        return "movie"
    return "unknown"


def _filter_by_categories(releases: list[Release], cats: list[int]) -> list[Release]:
    if not cats:
        return releases
    wanted = {c // 1000 for c in cats}
    filtered = [r for r in releases if r.categories[0] // 1000 in wanted]
    # The *arr indexer tests need at least one result for an empty query.
    return filtered or releases


@router.get("/newznab/api")
async def newznab_api(request: Request):
    return await handle(request.app.state.svc, dict(request.query_params))


async def handle(svc, params: dict[str, str]) -> Response:
    function = (params.get("t") or "").lower()
    if function == "caps":
        return Response(caps_xml(svc.settings.public_url, svc.settings.search_page_size), media_type=XML)
    if params.get("apikey") != svc.api_key:
        return Response(error_xml(100), media_type=XML)

    if function == "get":
        token = params.get("id")
        if not token:
            return Response(error_xml(200), media_type=XML)
        try:
            info = decode_release_id(svc.release_secret, token)
        except (ValueError, KeyError) as exc:
            log.warning("Invalid release id: %s", exc)
            return Response(error_xml(300), media_type=XML)
        filename = f"{info.title}.nzb"
        return Response(
            build_nzb(info),
            media_type="application/x-nzb",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
        )

    if function not in SEARCH_FUNCTIONS:
        return Response(error_xml(202), media_type=XML)

    cats = _categories(params.get("cat"))
    kind: Kind = SEARCH_FUNCTIONS[function]  # type: ignore[assignment]
    if kind == "unknown":
        kind = _kind_from_categories(cats)

    q = (params.get("q") or "").strip() or None
    tvdb_id = _int(params.get("tvdbid"))
    tmdb_id = _int(params.get("tmdbid"))
    imdb_raw = params.get("imdbid") or ""
    imdb_id = f"tt{int(_int(imdb_raw)):07d}" if _int(imdb_raw) else None
    season_raw = params.get("season")
    ep_raw = params.get("ep")
    offset = _int(params.get("offset")) or 0
    limit = min(_int(params.get("limit")) or svc.settings.search_page_size, svc.settings.search_page_size)

    if ep_raw and "/" in ep_raw:
        # Daily episodes (season=YYYY&ep=MM/DD) are not supported on Webshare.
        return _rss(svc, [], offset, 0)

    if not any([q, tvdb_id, tmdb_id, imdb_id]):
        releases = _filter_by_categories(await svc.search.recent(), cats)
        if not releases and svc.settings.rss_placeholder:
            releases = [placeholder_release(kind, cats)]
    else:
        season = _int(season_raw) if season_raw is not None else None
        episode = _int(ep_raw)
        ctx = await svc.resolver.resolve(
            kind,
            q=q,
            tvdb_id=tvdb_id,
            tmdb_id=tmdb_id,
            imdb_id=imdb_id,
            season=season,
            episodes=[episode] if episode is not None else None,
        )
        releases = await svc.search.search(ctx) if ctx.titles else []

    return _rss(svc, releases[offset : offset + limit], offset, len(releases))


def placeholder_release(kind: Kind, cats: list[int]) -> Release:
    """A single RSS item for when Webshare returns nothing.

    Sonarr/Radarr refuse to save an indexer whose RSS feed is empty. The title cannot be parsed
    as an episode or a movie (no season/episode, no year), so they never grab it.
    """
    if kind == "unknown":
        kind = "tv" if cats and all(5000 <= c < 6000 for c in cats) else "movie"
    return Release(
        ident="wsdarr-placeholder",
        title="wsdarr.indexer.placeholder",
        ws_name="Webshare returned no results for the RSS feed (see wsdarr log)",
        size=1,
        kind=kind,
    )


def nzb_link(svc, release: Release) -> str:
    token = encode_release_id(
        svc.release_secret,
        NzbInfo(ident=release.ident, title=release.title, ws_name=release.ws_name, size=release.size),
    )
    return f"{svc.settings.public_url.rstrip('/')}/newznab/api?t=get&id={token}&apikey={svc.api_key}"


def _rss(svc, releases: list[Release], offset: int, total: int) -> Response:
    body = rss_xml(releases, lambda r: nzb_link(svc, r), svc.settings.public_url, offset=offset, total=total)
    return Response(body, media_type=XML)
