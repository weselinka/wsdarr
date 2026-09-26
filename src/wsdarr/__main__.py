"""Command line: ``wsdarr serve | search <query> | apikey``."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sqlite3
import sys

from .config import Settings


def _logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)


def cmd_serve(settings: Settings, args) -> int:
    import uvicorn

    from .app import create_app

    uvicorn.run(
        create_app(settings),
        host=args.host or settings.host,
        port=args.port or settings.port,
        log_level=settings.log_level.lower(),
        proxy_headers=True,
    )
    return 0


async def _with_services(settings: Settings, func):
    from .services import build_services

    svc = build_services(settings)
    try:
        return await func(svc)
    finally:
        await svc.webshare.aclose()
        await svc.http.aclose()
        svc.db.close()


def cmd_search(settings: Settings, args) -> int:
    async def run(svc):
        ctx = await svc.resolver.resolve(args.kind, q=args.query)
        releases = await svc.search.search(ctx)
        return ctx, releases

    ctx, releases = asyncio.run(_with_services(settings, run))
    print(f"# {ctx.kind} / {ctx.source}: {ctx.canonical_title!r}, titles={ctx.titles}", file=sys.stderr)
    for release in releases:
        print(json.dumps(release.summary(), ensure_ascii=False))
    return 0


def cmd_apikey(settings: Settings, args) -> int:
    """Print the API key without starting anything.

    Meant for ``docker exec wsdarr wsdarr apikey`` next to the running server: the database is
    opened read-only, so running as root inside the container cannot create files the server
    (running as PUID) could not write later.
    """
    if settings.api_key:
        print(settings.api_key)
        return 0
    if not settings.db_path.exists():
        print(
            f"No API key yet: start wsdarr first (database {settings.db_path} does not exist).",
            file=sys.stderr,
        )
        return 1
    try:
        # A running server keeps the -wal/-shm files; reuse them read-only. Otherwise open the
        # database as immutable so SQLite does not create them (they would be owned by root).
        wal = settings.db_path.with_name(settings.db_path.name + "-wal")
        mode = "mode=ro" if wal.exists() else "immutable=1"
        conn = sqlite3.connect(f"file:{settings.db_path}?{mode}", uri=True)
        try:
            row = conn.execute("SELECT value FROM kv WHERE key = 'api_key'").fetchone()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        print(f"Cannot read {settings.db_path}: {exc}", file=sys.stderr)
        return 1
    if not row:
        print("No API key yet: start wsdarr first.", file=sys.stderr)
        return 1
    print(row[0])
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wsdarr", description="Webshare.cz indexer + downloader for *arr")
    sub = parser.add_subparsers(dest="command")
    serve = sub.add_parser("serve", help="run the web server (default)")
    serve.add_argument("--host")
    serve.add_argument("--port", type=int)
    search = sub.add_parser("search", help="search Webshare like Sonarr/Radarr would (debugging)")
    search.add_argument("query")
    search.add_argument("--kind", choices=["tv", "movie", "unknown"], default="unknown")
    sub.add_parser("apikey", help="print the API key used by the indexer and download client")
    args = parser.parse_args(argv)

    settings = Settings()
    _logging(settings.log_level)
    command = args.command or "serve"
    if command == "serve" and not hasattr(args, "host"):
        args.host, args.port = None, None
    return {"serve": cmd_serve, "search": cmd_search, "apikey": cmd_apikey}[command](settings, args)


if __name__ == "__main__":
    sys.exit(main())
