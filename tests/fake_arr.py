"""In-memory stand-ins for the parts of the Sonarr/Radarr v3 API that wsdarr calls."""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

API_KEY = "arrkey"


def create_fake_arr(kind: str, library: list[dict] | None = None) -> FastAPI:
    app = FastAPI()
    state: dict[str, Any] = {"commands": [], "library": list(library or [])}
    app.state.data = state

    @app.middleware("http")
    async def auth(request: Request, call_next):
        if request.headers.get("X-Api-Key") != API_KEY:
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        return await call_next(request)

    @app.get("/api/v3/system/status")
    async def status():
        return {"appName": kind.capitalize(), "version": "4.0.0.0"}

    @app.get("/api/v3/series")
    async def series(tvdbId: int | None = None):
        return [s for s in state["library"] if tvdbId is None or s.get("tvdbId") == tvdbId]

    @app.get("/api/v3/movie")
    async def movies(tmdbId: int | None = None):
        return [m for m in state["library"] if tmdbId is None or m.get("tmdbId") == tmdbId]

    @app.get("/api/v3/movie/lookup/imdb")
    async def movie_imdb(imdbId: str):
        for m in state["library"]:
            if m.get("imdbId") == imdbId:
                return m
        raise HTTPException(404)

    @app.post("/api/v3/command")
    async def command(request: Request):
        body = await request.json()
        state["commands"].append(body)
        return {"id": len(state["commands"]), "name": body["name"], "status": "queued"}

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
