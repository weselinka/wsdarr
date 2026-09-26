"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse

from . import __version__
from .config import Settings
from .newznab import router as newznab
from .sabnzbd import router as sabnzbd
from .services import Services, build_services
from .webshare import WebshareError

log = logging.getLogger(__name__)


async def _startup_tasks(svc: Services) -> None:
    if svc.webshare.has_credentials and not svc.webshare.token:
        try:
            await svc.webshare.login()
        except WebshareError as exc:
            log.error("Webshare login failed: %s", exc)
    elif not svc.webshare.has_credentials:
        log.warning("WEBSHARE_USERNAME/WEBSHARE_PASSWORD not set - downloads will not work")
    if svc.settings.auto_setup:
        from .provision.setup import run_setup

        try:
            report = await run_setup(svc)
            for line in report.lines():
                log.info("setup: %s", line)
        except Exception:
            log.exception("Automatic setup failed")


def create_app(settings: Settings | None = None, services: Services | None = None) -> FastAPI:
    settings = settings or (services.settings if services else Settings())

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        svc = services or build_services(settings)
        app.state.svc = svc
        await svc.start()
        log.info("wsdarr %s listening, API key: %s…", __version__, svc.api_key[:4])
        task = asyncio.create_task(_startup_tasks(svc))
        try:
            yield
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
            await svc.stop()

    app = FastAPI(title="wsdarr", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None)
    if services is not None:
        app.state.svc = services
    app.include_router(newznab.router)
    app.include_router(sabnzbd.router)

    @app.api_route("/api", methods=["GET", "POST"])
    async def combined_api(request: Request):
        """``/api`` is both the Newznab and the SABnzbd default path; dispatch by parameters."""
        svc = request.app.state.svc
        if "mode" in request.query_params or request.method == "POST":
            params = await sabnzbd._params(request)
            if "mode" in params:
                return await sabnzbd.handle(svc, params)
        if "t" in request.query_params:
            return await newznab.handle(svc, dict(request.query_params))
        return JSONResponse({"status": False, "error": "expected 't' (Newznab) or 'mode' (SABnzbd)"}, 400)

    @app.get("/health")
    async def health(request: Request):
        svc = request.app.state.svc
        return {"status": "ok", "version": __version__, "webshare_logged_in": bool(svc.webshare.token)}

    from .web.router import mount_static
    from .web.router import router as web_router

    app.include_router(web_router)
    mount_static(app)

    @app.get("/", include_in_schema=False)
    async def root():
        return RedirectResponse("/ui/")

    return app
