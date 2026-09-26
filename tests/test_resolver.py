import httpx
from fastapi import FastAPI

from fake_arr import API_KEY, HostRouter, create_fake_arr
from wsdarr.config import ArrInstance
from wsdarr.db import Database
from wsdarr.metadata.arr import ArrClient
from wsdarr.metadata.resolver import MetadataResolver
from wsdarr.metadata.tmdb import TmdbClient

MOVIE_DETAILS = {
    "id": 603,
    "title": "Matrix",
    "original_title": "The Matrix",
    "release_date": "1999-03-30",
    "imdb_id": "tt0133093",
    "alternative_titles": {
        "titles": [{"iso_3166_1": "CZ", "title": "Matrix 1"}, {"iso_3166_1": "JP", "title": "マトリックス"}]
    },
    "translations": {
        "translations": [
            {"iso_639_1": "en", "iso_3166_1": "US", "data": {"title": "The Matrix"}},
            {"iso_639_1": "sk", "iso_3166_1": "SK", "data": {"title": "Matrix (SK)"}},
        ]
    },
}
TV_DETAILS = {
    "id": 1396,
    "name": "Perníkový táta",
    "original_name": "Breaking Bad",
    "first_air_date": "2008-01-20",
    "external_ids": {"imdb_id": "tt0903747", "tvdb_id": 81189},
    "alternative_titles": {"results": []},
    "translations": {"translations": [{"iso_639_1": "en", "data": {"name": "Breaking Bad"}}]},
}


def fake_tmdb() -> FastAPI:
    app = FastAPI()

    @app.get("/3/find/{external_id}")
    async def find(external_id: str, external_source: str):
        if external_id == "81189":
            return {"tv_results": [{"id": 1396}], "movie_results": []}
        if external_id == "tt0133093":
            return {"movie_results": [{"id": 603}], "tv_results": []}
        return {"movie_results": [], "tv_results": []}

    @app.get("/3/movie/{tmdb_id}")
    async def movie(tmdb_id: int, language: str):
        assert language == "cs-CZ"
        return MOVIE_DETAILS

    @app.get("/3/tv/{tmdb_id}")
    async def tv(tmdb_id: int, language: str):
        return TV_DETAILS

    @app.get("/3/search/{kind}")
    async def search(kind: str, query: str):
        return {"results": [{"id": 603}] if "matrix" in query.lower() else []}

    return app


def make_resolver(sonarr_library=None, with_tmdb=True):
    router = HostRouter({"tmdb": fake_tmdb(), "sonarr": create_fake_arr("sonarr", sonarr_library or [])})
    http = httpx.AsyncClient(transport=router)
    sonarr = ArrClient(ArrInstance(name="sonarr", url="http://sonarr", api_key=API_KEY), "sonarr", http)
    tmdb = TmdbClient(http, api_key="k", base_url="http://tmdb/3") if with_tmdb else None
    return MetadataResolver([sonarr], [], tmdb, Database(":memory:"))


async def test_tv_from_tmdb_when_not_in_sonarr():
    ctx = await make_resolver().resolve("tv", tvdb_id=81189, season=1, episodes=[2])
    assert ctx.canonical_title == "Breaking Bad"
    assert ctx.source == "tmdb"
    assert ctx.titles[:2] == ["Perníkový táta", "Breaking Bad"]
    assert ctx.tmdb_id == 1396 and ctx.imdb_id == "tt0903747"
    assert ctx.year == 2008


async def test_sonarr_title_wins_and_tmdb_adds_czech():
    library = [
        {
            "id": 1,
            "title": "Breaking Bad",
            "year": 2008,
            "tvdbId": 81189,
            "tmdbId": 1396,
            "alternateTitles": [{"title": "BB"}],
        }
    ]
    ctx = await make_resolver(library).resolve("tv", tvdb_id=81189, season=1, episodes=[2])
    assert ctx.source == "sonarr"
    assert ctx.canonical_title == "Breaking Bad"
    assert "Perníkový táta" in ctx.titles and "BB" in ctx.titles


async def test_movie_by_imdb_via_tmdb_find():
    ctx = await make_resolver().resolve("movie", imdb_id="tt0133093")
    assert ctx.canonical_title == "The Matrix"
    assert ctx.titles[0] == "Matrix"
    assert "Matrix 1" in ctx.titles and "Matrix (SK)" in ctx.titles
    assert "マトリックス" not in ctx.titles
    assert ctx.year == 1999


async def test_query_only_without_tmdb():
    ctx = await make_resolver(with_tmdb=False).resolve("unknown", q="Pelíšky 1999")
    assert ctx.kind == "movie"
    assert ctx.year == 1999
    assert ctx.titles == ["Pelíšky"]
    assert ctx.canonical_title == "Pelíšky"


async def test_query_found_in_sonarr_library():
    library = [
        {
            "id": 1,
            "title": "Breaking Bad",
            "year": 2008,
            "tvdbId": 81189,
            "alternateTitles": [{"title": "Pernikovy tata"}],
        }
    ]
    ctx = await make_resolver(library, with_tmdb=False).resolve(
        "tv", q="Pernikovy tata", season=1, episodes=[1]
    )
    assert ctx.source == "sonarr"
    assert ctx.canonical_title == "Breaking Bad"
    assert ctx.tvdb_id == 81189


async def test_missing_item_is_not_cached_for_long():
    resolver = make_resolver(with_tmdb=False)
    ctx = await resolver.resolve("tv", tvdb_id=81189, season=1, episodes=[2])
    assert ctx.titles == []
    # The user adds the series to Sonarr afterwards: the next search must find it.
    sonarr_app = resolver.sonarrs[0]._http._transport._transports["sonarr"].app
    sonarr_app.state.data["library"].append({"id": 1, "title": "Breaking Bad", "year": 2008, "tvdbId": 81189})
    ctx = await resolver.resolve("tv", tvdb_id=81189, season=1, episodes=[2])
    assert ctx.canonical_title == "Breaking Bad"
