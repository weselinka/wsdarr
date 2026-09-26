"""TMDB client used to add localized (Czech/Slovak) titles to searches."""

from __future__ import annotations

import logging
from typing import Any, Literal

import httpx

log = logging.getLogger(__name__)

LOCAL_LANGS = ("cs", "sk")
LOCAL_COUNTRIES = ("CZ", "SK")


class TmdbClient:
    def __init__(
        self,
        http: httpx.AsyncClient,
        api_key: str | None = None,
        token: str | None = None,
        language: str = "cs-CZ",
        base_url: str = "https://api.themoviedb.org/3",
    ):
        self._http = http
        self.api_key = api_key
        self.token = token
        self.language = language
        self.base_url = base_url.rstrip("/")

    @property
    def enabled(self) -> bool:
        return bool(self.api_key or self.token)

    async def _get(self, path: str, **params: Any) -> dict:
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        else:
            params["api_key"] = self.api_key
        resp = await self._http.get(
            f"{self.base_url}/{path.lstrip('/')}", params=params, headers=headers, timeout=20
        )
        resp.raise_for_status()
        return resp.json()

    async def find(self, external_id: str | int, source: Literal["tvdb_id", "imdb_id"]) -> dict:
        return await self._get(f"find/{external_id}", external_source=source)

    async def details(self, kind: Literal["movie", "tv"], tmdb_id: int) -> dict:
        return await self._get(
            f"{kind}/{tmdb_id}",
            language=self.language,
            append_to_response="alternative_titles,translations,external_ids",
        )

    async def search(self, kind: Literal["movie", "tv"], query: str, year: int | None = None) -> list[dict]:
        params: dict[str, Any] = {"query": query, "language": self.language}
        if year:
            params["year" if kind == "movie" else "first_air_date_year"] = year
        data = await self._get(f"search/{kind}", **params)
        return data.get("results", [])


def titles_from_details(details: dict, kind: Literal["movie", "tv"]) -> dict[str, Any]:
    """Extract ``{"local": [...], "original": str, "english": str|None, "year": int|None}``."""
    title_key = "title" if kind == "movie" else "name"
    original_key = "original_title" if kind == "movie" else "original_name"
    date_key = "release_date" if kind == "movie" else "first_air_date"

    local: list[str] = []
    english: str | None = None
    if details.get(title_key):
        local.append(details[title_key])
    for tr in (details.get("translations") or {}).get("translations", []):
        name = (tr.get("data") or {}).get(title_key)
        if not name:
            continue
        if tr.get("iso_639_1") in LOCAL_LANGS:
            local.append(name)
        elif tr.get("iso_639_1") == "en" and not english:
            english = name
    alt = details.get("alternative_titles") or {}
    for item in alt.get("titles", alt.get("results", [])):
        if item.get("iso_3166_1") in LOCAL_COUNTRIES and item.get("title"):
            local.append(item["title"])
    year = None
    if details.get(date_key):
        try:
            year = int(str(details[date_key])[:4])
        except ValueError:
            year = None
    return {
        "local": list(dict.fromkeys(local)),
        "original": details.get(original_key),
        "english": english,
        "year": year,
        "imdb_id": details.get("imdb_id") or (details.get("external_ids") or {}).get("imdb_id"),
        "tvdb_id": (details.get("external_ids") or {}).get("tvdb_id"),
    }
