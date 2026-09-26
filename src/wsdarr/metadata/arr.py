"""Minimal async client for the Sonarr/Radarr REST API (v3)."""

from __future__ import annotations

import logging
from typing import Any, Literal

import httpx

from ..config import ArrInstance

log = logging.getLogger(__name__)

ArrKind = Literal["sonarr", "radarr"]


class ArrError(Exception):
    pass


class ArrClient:
    def __init__(self, instance: ArrInstance, kind: ArrKind, http: httpx.AsyncClient):
        self.instance = instance
        self.kind = kind
        self._http = http
        self.api_version = "v3"

    @property
    def name(self) -> str:
        return self.instance.name

    @property
    def category(self) -> str | None:
        return self.instance.category

    def _url(self, path: str) -> str:
        return f"{self.instance.base_url}/api/{self.api_version}/{path.lstrip('/')}"

    async def request(self, method: str, path: str, **kwargs: Any) -> Any:
        headers = {"X-Api-Key": self.instance.api_key, "Accept": "application/json"}
        try:
            resp = await self._http.request(method, self._url(path), headers=headers, timeout=30, **kwargs)
        except httpx.HTTPError as exc:
            raise ArrError(f"{self.name}: {method} {path} failed: {exc}") from exc
        if resp.status_code >= 400:
            raise ArrError(f"{self.name}: {method} {path} -> HTTP {resp.status_code}: {resp.text[:300]}")
        if not resp.content:
            return None
        try:
            return resp.json()
        except ValueError:
            return resp.text

    async def get(self, path: str, **params: Any) -> Any:
        return await self.request("GET", path, params={k: v for k, v in params.items() if v is not None})

    async def post(self, path: str, json: Any, **params: Any) -> Any:
        return await self.request("POST", path, json=json, params=params or None)

    async def put(self, path: str, json: Any, **params: Any) -> Any:
        return await self.request("PUT", path, json=json, params=params or None)

    async def delete(self, path: str) -> Any:
        return await self.request("DELETE", path)

    # --- common --------------------------------------------------------------------------
    async def system_status(self) -> dict:
        return await self.get("system/status")

    async def command(self, name: str, **body: Any) -> Any:
        return await self.post("command", {"name": name, **body})

    # --- sonarr --------------------------------------------------------------------------
    async def series_by_tvdb(self, tvdb_id: int) -> dict | None:
        result = await self.get("series", tvdbId=tvdb_id)
        return result[0] if result else None

    async def all_series(self) -> list[dict]:
        return await self.get("series") or []

    async def series_lookup(self, term: str) -> list[dict]:
        return await self.get("series/lookup", term=term) or []

    # --- radarr --------------------------------------------------------------------------
    async def movie_by_tmdb(self, tmdb_id: int) -> dict | None:
        result = await self.get("movie", tmdbId=tmdb_id)
        return result[0] if result else None

    async def all_movies(self) -> list[dict]:
        return await self.get("movie") or []

    async def movie_lookup_imdb(self, imdb_id: str) -> dict | None:
        try:
            return await self.get("movie/lookup/imdb", imdbId=imdb_id)
        except ArrError:
            return None
