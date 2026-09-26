"""A tiny stand-in for the Webshare.cz API, used by tests and for local end-to-end runs.

Run standalone::

    python tests/fake_webshare.py --port 9999
    WEBSHARE_BASE_URL=http://127.0.0.1:9999/api/ WEBSHARE_USERNAME=user WEBSHARE_PASSWORD=pass wsdarr serve
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import re
import unicodedata
from urllib.parse import quote

from fastapi import FastAPI, Form, Header, Request
from fastapi.responses import Response

from wsdarr.webshare.md5crypt import webshare_password_hash

SALT = "AbCd1234"
TOKEN = "fake-wst-token"

DEFAULT_FILES = [
    ("a1", "Breaking.Bad.S01E02.720p.WEB-DL.CZ.titulky.mkv", 3_000_000),
    ("a2", "Breaking Bad S01E02 CZ dabing 1080p BluRay x264.mkv", 4_000_000),
    ("a3", "Breaking.Bad.S01E03.720p.CZ.mkv", 3_000_000),
    ("a4", "Perníkový táta S01E02 (CZ) 720p.avi", 2_000_000),
    ("a5", "Breaking.Bad.S01E02.sample.mkv", 10_000),
    ("b1", "Pán prstenů - Návrat krále (2003) 1080p CZ dabing.mkv", 5_000_000),
    ("b2", "The.Lord.of.the.Rings.The.Return.of.the.King.2003.2160p.UHD.BluRay.x265.CZ.EN.mkv", 6_000_000),
    ("b3", "Pan prstenu - Spolecenstvo prstenu (2001) CZ.avi", 2_500_000),
    ("c1", "Heslem chraneny film 2003 1080p.mkv", 5_000_000),
]


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def file_bytes(ident: str, size: int) -> bytes:
    seed = hashlib.sha256(ident.encode()).digest()
    return (seed * (size // len(seed) + 1))[:size]


def _xml(body: str) -> Response:
    return Response(
        f'<?xml version="1.0" encoding="UTF-8"?><response>{body}</response>', media_type="text/xml"
    )


def create_fake_app(
    files: list[tuple[str, str, int]] | None = None,
    username: str = "user",
    password: str = "pass",
    protected: tuple[str, ...] = ("c1",),
) -> FastAPI:
    app = FastAPI()
    catalog = {ident: (name, size) for ident, name, size in (files or DEFAULT_FILES)}
    app.state.catalog = catalog
    app.state.calls = []
    app.state.fail_links = set()
    expected_hash = webshare_password_hash(password, SALT)

    def err(code: str, message: str) -> Response:
        return _xml(f"<status>FATAL</status><code>{code}</code><message>{message}</message>")

    @app.post("/api/salt/")
    async def salt(username_or_email: str = Form(...)):
        await asyncio.sleep(0.02)  # let concurrent callers overlap
        app.state.calls.append(("salt", username_or_email))
        if username_or_email != username:
            return err("SALT_FATAL_1", "User not found")
        return _xml(f"<status>OK</status><salt>{SALT}</salt>")

    @app.post("/api/login/")
    async def login(username_or_email: str = Form(...), password: str = Form(...)):
        app.state.calls.append(("login", username_or_email))
        if username_or_email != username or password != expected_hash:
            return err("LOGIN_FATAL_1", "Bad credentials")
        return _xml(f"<status>OK</status><token>{TOKEN}</token>")

    @app.post("/api/search/")
    async def search(
        what: str = Form(""),
        category: str = Form(""),
        sort: str = Form(""),
        limit: int = Form(25),
        offset: int = Form(0),
        wst: str = Form(""),
    ):
        app.state.calls.append(("search", what))
        tokens = _norm(what).split()
        hits = []
        for ident, (name, size) in catalog.items():
            name_tokens = _norm(name).split()
            if all(t in name_tokens for t in tokens):
                hits.append((ident, name, size))
        if sort == "recent":
            hits = list(reversed(hits))
        page = hits[offset : offset + limit]
        body = "".join(
            f"<file><ident>{i}</ident><name>{n}</name><type>{n.rsplit('.', 1)[-1]}</type>"
            f"<img></img><stripe></stripe><stripe_count>0</stripe_count><size>{s}</size>"
            f"<queued>0</queued><positive_votes>1</positive_votes><negative_votes>0</negative_votes>"
            f"<password>{1 if i in protected else 0}</password></file>"
            for i, n, s in page
        )
        return _xml(f"<status>OK</status><total>{len(hits)}</total>{body}")

    @app.post("/api/file_link/")
    async def file_link(request: Request, ident: str = Form(...), wst: str = Form("")):
        app.state.calls.append(("file_link", ident))
        if wst != TOKEN:
            return err("FILE_LINK_FATAL_2", "Not logged in")
        if ident not in catalog or ident in app.state.fail_links:
            return err("FILE_LINK_FATAL_1", "File not found")
        base = str(request.base_url).rstrip("/")
        return _xml(f"<status>OK</status><link>{base}/dl/{ident}/{catalog[ident][0]}</link>")

    @app.post("/api/file_info/")
    async def file_info(ident: str = Form(...)):
        if ident not in catalog:
            return err("FILE_INFO_FATAL_1", "File not found")
        name, size = catalog[ident]
        return _xml(f"<status>OK</status><name>{name}</name><size>{size}</size><type>video</type>")

    @app.post("/api/user_data/")
    async def user_data(wst: str = Form("")):
        if wst != TOKEN:
            return err("USER_DATA_FATAL_1", "Not logged in")
        return _xml(
            f"<status>OK</status><id>1</id><username>{username}</username><email>u@example.com</email>"
            "<vip>1</vip><vip_days>42</vip_days><vip_until>2099-01-01 00:00:00</vip_until>"
        )

    @app.get("/dl/{ident}/{name}")
    async def download(ident: str, name: str, range: str | None = Header(default=None)):
        app.state.calls.append(("download", ident, range))
        data = file_bytes(ident, catalog[ident][1])
        headers = {
            "Accept-Ranges": "bytes",
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}",
        }
        if range:
            m = re.match(r"bytes=(\d+)-", range)
            start = int(m.group(1)) if m else 0
            headers["Content-Range"] = f"bytes {start}-{len(data) - 1}/{len(data)}"
            return Response(data[start:], status_code=206, headers=headers, media_type="video/x-matroska")
        return Response(data, headers=headers, media_type="video/x-matroska")

    return app


if __name__ == "__main__":
    import uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9999)
    args = parser.parse_args()
    uvicorn.run(create_fake_app(), host=args.host, port=args.port)
