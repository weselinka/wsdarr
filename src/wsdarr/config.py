"""Application settings.

Values are read (highest priority first) from environment variables, ``/config/config.yml``
and the defaults below. Single Sonarr/Radarr instances can be configured with the
``SONARR_URL``/``SONARR_API_KEY`` style variables; multiple instances via the YAML file::

    sonarr:
      - name: sonarr
        url: http://sonarr:8989
        api_key: xxx
      - name: sonarr-4k
        url: http://sonarr4k:8989
        api_key: yyy
        category: tv-4k
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, Field, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)


class ArrInstance(BaseModel):
    """A Sonarr or Radarr instance."""

    name: str
    url: str
    api_key: str
    # Download category used by this instance's download client (defaults per kind).
    category: str | None = None

    @property
    def base_url(self) -> str:
        return self.url.rstrip("/")


def _config_dir() -> Path:
    return Path(os.environ.get("CONFIG_DIR", "/config"))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    # --- service -------------------------------------------------------------------------
    config_dir: Path = Field(default_factory=_config_dir)
    download_dir: Path = Path("/downloads")
    host: str = "0.0.0.0"
    port: int = 9797
    # URL under which Sonarr/Radarr reach wsdarr (used in the NZB links of search results).
    public_url: str = "http://wsdarr:9797"
    api_key: str | None = Field(default=None, validation_alias=AliasChoices("wsdarr_api_key", "api_key"))
    log_level: str = "INFO"
    ui_username: str | None = None
    ui_password: str | None = None

    # --- webshare ------------------------------------------------------------------------
    webshare_username: str | None = None
    webshare_password: str | None = None
    webshare_base_url: str = "https://webshare.cz/api/"
    webshare_concurrency: int = 4

    # --- metadata ------------------------------------------------------------------------
    tmdb_api_key: str | None = None
    tmdb_token: str | None = None  # TMDB v4 "API Read Access Token" (alternative to api key)
    tmdb_language: str = "cs-CZ"
    tmdb_base_url: str = "https://api.themoviedb.org/3"

    sonarr_url: str | None = None
    sonarr_api_key: str | None = None
    radarr_url: str | None = None
    radarr_api_key: str | None = None
    sonarr: list[ArrInstance] = Field(default_factory=list)
    radarr: list[ArrInstance] = Field(default_factory=list)

    # --- search --------------------------------------------------------------------------
    search_page_size: int = 100
    search_max_pages: int = 3
    search_cache_ttl: int = 600
    min_file_size_mb: int = 50
    max_alt_titles: int = 4
    match_threshold: int = 88
    rss_mode: Literal["recent", "off"] = "recent"
    rss_query: str = ""
    rss_limit: int = 50
    release_group: str = "WS"
    # "extension": give files without quality markers the quality Sonarr/Radarr assign from the
    # file extension (instead of "Unknown", which default profiles reject); "keep": leave unknown.
    unknown_quality: Literal["extension", "keep"] = "extension"

    # --- downloads -----------------------------------------------------------------------
    tv_category: str = "tv"
    movie_category: str = "movies"
    extra_categories: list[str] = Field(default_factory=list)
    max_concurrent_downloads: int = 2
    download_retries: int = 3
    speed_limit_kbps: int = 0
    notify_arr_on_complete: bool = True

    @model_validator(mode="after")
    def _merge_single_instances(self) -> Settings:
        if (
            self.sonarr_url
            and self.sonarr_api_key
            and not any(i.base_url == self.sonarr_url.rstrip("/") for i in self.sonarr)
        ):
            self.sonarr.insert(
                0, ArrInstance(name="sonarr", url=self.sonarr_url, api_key=self.sonarr_api_key)
            )
        if (
            self.radarr_url
            and self.radarr_api_key
            and not any(i.base_url == self.radarr_url.rstrip("/") for i in self.radarr)
        ):
            self.radarr.insert(
                0, ArrInstance(name="radarr", url=self.radarr_url, api_key=self.radarr_api_key)
            )
        for inst in self.sonarr:
            inst.category = inst.category or self.tv_category
        for inst in self.radarr:
            inst.category = inst.category or self.movie_category
        return self

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        yaml_file = _config_dir() / "config.yml"
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            YamlConfigSettingsSource(settings_cls, yaml_file=yaml_file),
            file_secret_settings,
        )

    # --- derived paths -------------------------------------------------------------------
    @property
    def complete_dir(self) -> Path:
        return self.download_dir / "complete"

    @property
    def incomplete_dir(self) -> Path:
        return self.download_dir / "incomplete"

    @property
    def db_path(self) -> Path:
        return self.config_dir / "wsdarr.db"

    @property
    def categories(self) -> list[str]:
        cats: list[str] = []
        for c in [self.tv_category, self.movie_category, *self.extra_categories]:
            if c not in cats:
                cats.append(c)
        for inst in [*self.sonarr, *self.radarr]:
            if inst.category and inst.category not in cats:
                cats.append(inst.category)
        return cats

    def public_summary(self) -> dict[str, Any]:
        """Settings safe to show in the UI (no secrets)."""
        return {
            "public_url": self.public_url,
            "download_dir": str(self.download_dir),
            "config_dir": str(self.config_dir),
            "webshare_username": self.webshare_username,
            "tmdb": bool(self.tmdb_api_key or self.tmdb_token),
            "tmdb_language": self.tmdb_language,
            "sonarr": [{"name": i.name, "url": i.url, "category": i.category} for i in self.sonarr],
            "radarr": [{"name": i.name, "url": i.url, "category": i.category} for i in self.radarr],
            "categories": self.categories,
            "max_concurrent_downloads": self.max_concurrent_downloads,
            "min_file_size_mb": self.min_file_size_mb,
            "search_max_pages": self.search_max_pages,
            "speed_limit_kbps": self.speed_limit_kbps,
            "rss_mode": self.rss_mode,
        }
