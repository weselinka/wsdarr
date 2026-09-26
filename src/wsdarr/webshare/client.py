"""Async client for the Webshare.cz XML API (https://webshare.cz/apidoc/)."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from xml.etree import ElementTree as ET

import httpx

from .md5crypt import webshare_password_hash
from .models import WsFile, WsSearchPage

log = logging.getLogger(__name__)

USER_AGENT = "wsdarr/0.1 (+https://github.com/weselinka/wsdarr)"


class WebshareError(Exception):
    def __init__(self, code: str, message: str = ""):
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code
        self.message = message


class WebshareClient:
    def __init__(
        self,
        username: str | None,
        password: str | None,
        base_url: str = "https://webshare.cz/api/",
        http: httpx.AsyncClient | None = None,
        load_token: Callable[[], str | None] | None = None,
        save_token: Callable[[str | None], None] | None = None,
        concurrency: int = 4,
        cache_ttl: float = 600,
    ):
        self.username = username
        self.password = password
        self.base_url = base_url.rstrip("/") + "/"
        self._http = http or httpx.AsyncClient(timeout=httpx.Timeout(30.0), follow_redirects=True)
        self._owns_http = http is None
        self._load_token = load_token
        self._save_token = save_token
        self._token: str | None = load_token() if load_token else None
        self._sem = asyncio.Semaphore(concurrency)
        self._login_lock = asyncio.Lock()
        self._last_login = 0.0
        self._cache_ttl = cache_ttl
        self._search_cache: dict[tuple, tuple[float, WsSearchPage]] = {}

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    # --- low level -----------------------------------------------------------------------
    @property
    def has_credentials(self) -> bool:
        return bool(self.username and self.password)

    @property
    def token(self) -> str | None:
        return self._token

    def _set_token(self, token: str | None) -> None:
        self._token = token
        if self._save_token:
            self._save_token(token)

    async def _request(self, endpoint: str, data: dict) -> ET.Element:
        url = f"{self.base_url}{endpoint.strip('/')}/"
        headers = {
            "Accept": "text/xml; charset=UTF-8",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "User-Agent": USER_AGENT,
        }
        async with self._sem:
            try:
                resp = await self._http.post(url, data=data, headers=headers)
            except httpx.HTTPError as exc:
                raise WebshareError("HTTP_ERROR", str(exc)) from exc
        if resp.status_code != 200:
            raise WebshareError(f"HTTP_{resp.status_code}", resp.text[:200])
        try:
            root = ET.fromstring(resp.content)
        except ET.ParseError as exc:
            raise WebshareError("INVALID_XML", resp.text[:200]) from exc
        status = (root.findtext("status") or "").strip()
        if status != "OK":
            raise WebshareError(
                (root.findtext("code") or status or "UNKNOWN").strip(),
                (root.findtext("message") or "").strip(),
            )
        return root

    async def login(self) -> str:
        if not self.has_credentials:
            raise WebshareError("NO_CREDENTIALS", "Webshare username/password not configured")
        async with self._login_lock:
            salt_root = await self._request("salt", {"username_or_email": self.username})
            salt = (salt_root.findtext("salt") or "").strip()
            if not salt:
                raise WebshareError("NO_SALT", "Webshare returned an empty salt")
            root = await self._request(
                "login",
                {
                    "username_or_email": self.username,
                    "password": webshare_password_hash(self.password or "", salt),
                    "keep_logged_in": 1,
                },
            )
            token = (root.findtext("token") or "").strip()
            if not token:
                raise WebshareError("NO_TOKEN", "Webshare login returned no token")
            self._last_login = time.monotonic()
            self._set_token(token)
            log.info("Logged in to Webshare as %s", self.username)
            return token

    async def _authed(self, endpoint: str, data: dict, require_login: bool = True) -> ET.Element:
        if self._token is None and self.has_credentials and require_login:
            await self.login()
        payload = dict(data)
        if self._token:
            payload["wst"] = self._token
        try:
            return await self._request(endpoint, payload)
        except WebshareError as exc:
            # The token may have expired; re-login once (not more often than every 30 s).
            if (
                not self.has_credentials
                or exc.code.startswith("HTTP")
                or time.monotonic() - self._last_login < 30
            ):
                raise
            log.info("Webshare %s failed (%s), re-logging in and retrying", endpoint, exc)
            await self.login()
            payload["wst"] = self._token
            return await self._request(endpoint, payload)

    # --- API -----------------------------------------------------------------------------
    async def search(
        self,
        what: str,
        category: str = "video",
        sort: str = "",
        limit: int = 100,
        offset: int = 0,
    ) -> WsSearchPage:
        key = (what, category, sort, limit, offset)
        cached = self._search_cache.get(key)
        now = time.monotonic()
        if cached and cached[0] > now:
            return cached[1]
        root = await self._authed(
            "search",
            {"what": what, "category": category, "sort": sort, "limit": limit, "offset": offset},
            require_login=False,
        )
        try:
            total = int((root.findtext("total") or "0").strip() or 0)
        except ValueError:
            total = 0
        page = WsSearchPage(total=total, files=[WsFile.from_xml(f) for f in root.findall("file")])
        self._search_cache[key] = (now + self._cache_ttl, page)
        if len(self._search_cache) > 2000:
            self._search_cache = {k: v for k, v in self._search_cache.items() if v[0] > now}
        return page

    async def file_link(self, ident: str, password: str | None = None) -> str:
        data = {
            "ident": ident,
            "download_type": "file_download",
            "force_https": 1,
            "device_vendor": "wsdarr",
            "device_model": "wsdarr",
        }
        if password:
            data["password"] = password
        root = await self._authed("file_link", data)
        link = (root.findtext("link") or "").strip()
        if not link:
            raise WebshareError("NO_LINK", f"No download link for {ident}")
        return link

    async def file_info(self, ident: str) -> dict[str, str]:
        root = await self._authed("file_info", {"ident": ident}, require_login=False)
        return {child.tag: (child.text or "").strip() for child in root if len(child) == 0}

    async def user_data(self) -> dict[str, str]:
        root = await self._authed("user_data", {})
        return {child.tag: (child.text or "").strip() for child in root if len(child) == 0}
