"""Wiring of all long-lived components."""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass, field

import httpx

from .config import Settings
from .db import Database, Job
from .downloader.manager import DownloadManager
from .metadata.arr import ArrClient, ArrError
from .metadata.resolver import MetadataResolver
from .metadata.tmdb import TmdbClient
from .search import SearchService
from .webshare import WebshareClient

log = logging.getLogger(__name__)


@dataclass
class Services:
    settings: Settings
    db: Database
    http: httpx.AsyncClient
    webshare: WebshareClient
    sonarrs: list[ArrClient]
    radarrs: list[ArrClient]
    prowlarr: ArrClient | None
    tmdb: TmdbClient | None
    resolver: MetadataResolver
    search: SearchService
    downloads: DownloadManager
    api_key: str
    release_secret: str
    status: dict = field(default_factory=dict)

    @property
    def arr_clients(self) -> list[ArrClient]:
        return [*self.sonarrs, *self.radarrs]

    async def notify_complete(self, job: Job) -> None:
        """Ask the matching Sonarr/Radarr to pick up the finished download right away."""
        if not self.settings.notify_arr_on_complete:
            return
        for client in self.arr_clients:
            if client.category == job.category:
                try:
                    await client.command("RefreshMonitoredDownloads")
                except ArrError as exc:
                    log.debug("Could not notify %s: %s", client.name, exc)

    async def start(self) -> None:
        await self.downloads.start()

    async def stop(self) -> None:
        await self.downloads.stop()
        await self.webshare.aclose()
        await self.http.aclose()
        self.db.close()


def build_services(
    settings: Settings,
    *,
    http: httpx.AsyncClient | None = None,
    webshare_http: httpx.AsyncClient | None = None,
    download_http: httpx.AsyncClient | None = None,
) -> Services:
    settings.config_dir.mkdir(parents=True, exist_ok=True)
    db = Database(settings.db_path)
    api_key = settings.api_key or db.kv_get_or_create("api_key", lambda: secrets.token_hex(16))
    release_secret = db.kv_get_or_create("release_secret", lambda: secrets.token_hex(32))
    http = http or httpx.AsyncClient(timeout=httpx.Timeout(30.0), follow_redirects=True)

    token_key = f"webshare_token:{settings.webshare_username or ''}"
    webshare = WebshareClient(
        settings.webshare_username,
        settings.webshare_password,
        base_url=settings.webshare_base_url,
        http=webshare_http,
        load_token=lambda: db.kv_get(token_key),
        save_token=lambda t: db.kv_set(token_key, t) if t else db.kv_delete(token_key),
        concurrency=settings.webshare_concurrency,
        cache_ttl=settings.search_cache_ttl,
    )
    sonarrs = [ArrClient(i, "sonarr", http) for i in settings.sonarr]
    radarrs = [ArrClient(i, "radarr", http) for i in settings.radarr]
    prowlarr = ArrClient(settings.prowlarr, "prowlarr", http) if settings.prowlarr else None
    tmdb = None
    if settings.tmdb_api_key or settings.tmdb_token:
        tmdb = TmdbClient(
            http,
            api_key=settings.tmdb_api_key,
            token=settings.tmdb_token,
            language=settings.tmdb_language,
            base_url=settings.tmdb_base_url,
        )
    resolver = MetadataResolver(sonarrs, radarrs, tmdb, db)
    search = SearchService(webshare, settings)

    services = Services(
        settings=settings,
        db=db,
        http=http,
        webshare=webshare,
        sonarrs=sonarrs,
        radarrs=radarrs,
        prowlarr=prowlarr,
        tmdb=tmdb,
        resolver=resolver,
        search=search,
        downloads=None,  # type: ignore[arg-type]
        api_key=api_key,
        release_secret=release_secret,
    )
    services.downloads = DownloadManager(
        settings, db, webshare, http=download_http, on_complete=services.notify_complete
    )
    return services
