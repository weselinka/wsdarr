"""In-memory stand-ins for the Sonarr/Radarr (v3) and Prowlarr (v1) APIs used by wsdarr."""

from __future__ import annotations

import copy
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request

API_KEY = "arrkey"

SONARR_CLIENT_FIELDS = [
    {"name": "host", "value": "localhost"},
    {"name": "port", "value": 8080},
    {"name": "useSsl", "value": False},
    {"name": "urlBase"},
    {"name": "apiKey"},
    {"name": "username"},
    {"name": "password"},
    {"name": "tvCategory", "value": "tv"},
    {"name": "recentTvPriority", "value": -100},
    {"name": "olderTvPriority", "value": -100},
]
RADARR_CLIENT_FIELDS = [
    {"name": "host", "value": "localhost"},
    {"name": "port", "value": 8080},
    {"name": "useSsl", "value": False},
    {"name": "urlBase"},
    {"name": "apiKey"},
    {"name": "username"},
    {"name": "password"},
    {"name": "movieCategory", "value": "movies"},
    {"name": "recentMoviePriority", "value": -100},
    {"name": "olderMoviePriority", "value": -100},
]
SONARR_INDEXER_FIELDS = [
    {"name": "baseUrl"},
    {"name": "apiPath", "value": "/api"},
    {"name": "apiKey"},
    {"name": "categories", "value": [5030, 5040]},
    {"name": "animeCategories", "value": []},
    {"name": "animeStandardFormatSearch", "value": False},
    {"name": "additionalParameters"},
]
RADARR_INDEXER_FIELDS = [
    {"name": "baseUrl"},
    {"name": "apiPath", "value": "/api"},
    {"name": "apiKey"},
    {"name": "categories", "value": [2000]},
    {"name": "additionalParameters"},
    {"name": "removeYear", "value": False},
]
PROWLARR_INDEXER_FIELDS = [
    {"name": "baseUrl"},
    {"name": "apiPath", "value": "/api"},
    {"name": "apiKey"},
    {"name": "additionalParameters"},
    {"name": "vipExpiration"},
]


def _schema_client(fields):
    return {
        "implementation": "Sabnzbd",
        "implementationName": "SABnzbd",
        "configContract": "SabnzbdSettings",
        "protocol": "usenet",
        "enable": True,
        "priority": 1,
        "removeCompletedDownloads": True,
        "removeFailedDownloads": True,
        "name": "",
        "tags": [],
        "fields": fields,
    }


def _schema_indexer(fields, **extra):
    return {
        "implementation": "Newznab",
        "implementationName": "Newznab",
        "configContract": "NewznabSettings",
        "protocol": "usenet",
        "enableRss": True,
        "enableAutomaticSearch": True,
        "enableInteractiveSearch": True,
        "priority": 25,
        "downloadClientId": 0,
        "name": "",
        "tags": [],
        "fields": fields,
        **extra,
    }


def create_fake_arr(kind: str, library: list[dict] | None = None) -> FastAPI:
    app = FastAPI()
    version = "v1" if kind == "prowlarr" else "v3"
    state: dict[str, Any] = {
        "downloadclient": [],
        "indexer": [],
        "commands": [],
        "library": library or [],
        "next_id": 1,
    }
    app.state.data = state
    client_fields = RADARR_CLIENT_FIELDS if kind == "radarr" else SONARR_CLIENT_FIELDS
    indexer_fields = {
        "sonarr": SONARR_INDEXER_FIELDS,
        "radarr": RADARR_INDEXER_FIELDS,
        "prowlarr": PROWLARR_INDEXER_FIELDS,
    }[kind]

    @app.middleware("http")
    async def auth(request: Request, call_next):
        if request.headers.get("X-Api-Key") != API_KEY:
            from fastapi.responses import JSONResponse

            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        return await call_next(request)

    @app.get(f"/api/{version}/system/status")
    async def status():
        return {"appName": kind.capitalize(), "version": "4.0.0.0"}

    @app.get(f"/api/{version}/series")
    async def series(tvdbId: int | None = None):
        items = state["library"]
        return [s for s in items if tvdbId is None or s.get("tvdbId") == tvdbId]

    @app.get(f"/api/{version}/movie")
    async def movies(tmdbId: int | None = None):
        items = state["library"]
        return [m for m in items if tmdbId is None or m.get("tmdbId") == tmdbId]

    @app.get(f"/api/{version}/movie/lookup/imdb")
    async def movie_imdb(imdbId: str):
        for m in state["library"]:
            if m.get("imdbId") == imdbId:
                return m
        raise HTTPException(404)

    @app.post(f"/api/{version}/command")
    async def command(request: Request):
        body = await request.json()
        state["commands"].append(body)
        return {"id": len(state["commands"]), "name": body["name"], "status": "queued"}

    @app.get(f"/api/{version}/downloadclient/schema")
    async def client_schema():
        return [
            _schema_client(copy.deepcopy(client_fields)),
            {**_schema_client([]), "implementation": "QBittorrent", "protocol": "torrent"},
        ]

    @app.get(f"/api/{version}/indexer/schema")
    async def indexer_schema():
        items = [_schema_indexer(copy.deepcopy(indexer_fields))]
        if kind == "prowlarr":
            items[0].update({"definitionName": "Newznab", "appProfileId": 0})
            items.insert(0, _schema_indexer(copy.deepcopy(indexer_fields), definitionName="NZBgeek"))
        return items

    @app.get(f"/api/{version}/appprofile")
    async def app_profiles():
        return [{"id": 1, "name": "Standard"}]

    for resource in ("downloadclient", "indexer"):

        def make(resource=resource):
            @app.get(f"/api/{version}/{resource}")
            async def list_items():
                return state[resource]

            @app.post(f"/api/{version}/{resource}")
            async def create(request: Request):
                body = await request.json()
                body["id"] = state["next_id"]
                state["next_id"] += 1
                state[resource].append(body)
                return body

            @app.put(f"/api/{version}/{resource}/{{item_id}}")
            async def update(item_id: int, request: Request):
                body = await request.json()
                for i, item in enumerate(state[resource]):
                    if item["id"] == item_id:
                        state[resource][i] = body
                        return body
                raise HTTPException(404)

            @app.post(f"/api/{version}/{resource}/test")
            async def test(request: Request):
                return {}

        make()

    return app


class HostRouter(httpx.AsyncBaseTransport):
    """Route requests to different ASGI apps by host name."""

    def __init__(self, apps: dict[str, Any]):
        self._transports = {host: httpx.ASGITransport(app=app) for host, app in apps.items()}

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        transport = self._transports.get(request.url.host)
        if transport is None:
            return httpx.Response(502, text=f"no fake for {request.url.host}")
        return await transport.handle_async_request(request)
