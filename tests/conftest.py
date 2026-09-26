from __future__ import annotations

import asyncio

import httpx
import pytest

from fake_arr import API_KEY, HostRouter, create_fake_arr
from fake_webshare import create_fake_app
from wsdarr.app import create_app
from wsdarr.config import ArrInstance, Settings
from wsdarr.services import build_services

SONARR_LIBRARY = [
    {
        "id": 1,
        "title": "Breaking Bad",
        "year": 2008,
        "tvdbId": 81189,
        "tmdbId": 1396,
        "imdbId": "tt0903747",
        "alternateTitles": [{"title": "Pernikovy tata", "seasonNumber": -1}],
    }
]
RADARR_LIBRARY = [
    {
        "id": 7,
        "title": "The Lord of the Rings: The Return of the King",
        "originalTitle": "The Lord of the Rings: The Return of the King",
        "year": 2003,
        "tmdbId": 122,
        "imdbId": "tt0167260",
        "alternateTitles": [{"title": "Pán prstenů: Návrat krále", "sourceType": "tmdb"}],
    }
]


@pytest.fixture
def fake_ws():
    return create_fake_app()


@pytest.fixture
def fake_sonarr():
    return create_fake_arr("sonarr", SONARR_LIBRARY)


@pytest.fixture
def fake_radarr():
    return create_fake_arr("radarr", RADARR_LIBRARY)


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        config_dir=tmp_path / "config",
        download_dir=tmp_path / "downloads",
        public_url="http://wsdarr:9797",
        api_key="testkey",
        webshare_username="user",
        webshare_password="pass",
        webshare_base_url="http://ws/api/",
        sonarr=[ArrInstance(name="sonarr", url="http://sonarr:8989", api_key=API_KEY)],
        radarr=[ArrInstance(name="radarr", url="http://radarr:7878", api_key=API_KEY)],
        min_file_size_mb=0,
        download_retries=2,
        max_concurrent_downloads=2,
    )


@pytest.fixture
async def services(settings, fake_ws, fake_sonarr, fake_radarr):
    router = HostRouter({"ws": fake_ws, "sonarr": fake_sonarr, "radarr": fake_radarr})
    http = httpx.AsyncClient(transport=router)
    ws_http = httpx.AsyncClient(transport=router)
    dl_http = httpx.AsyncClient(transport=router)
    svc = build_services(settings, http=http, webshare_http=ws_http, download_http=dl_http)
    await svc.start()
    yield svc
    await svc.stop()
    await ws_http.aclose()
    await dl_http.aclose()


@pytest.fixture
async def client(services):
    app = create_app(services=services)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://wsdarr:9797") as c:
        yield c


async def wait_for(predicate, timeout: float = 10.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        result = await predicate()
        if result:
            return result
        await asyncio.sleep(0.05)
    raise AssertionError("condition not met in time")
