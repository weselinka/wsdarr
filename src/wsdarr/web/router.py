"""Small server-rendered web UI (Jinja2 + htmx, no build step)."""

from __future__ import annotations

import base64
import binascii
import html
import secrets
import shutil
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .. import __version__
from ..matching.naming import build_release_title, newznab_languages
from ..matching.parser import parse_filename
from ..metadata.arr import ArrError
from ..sabnzbd.router import format_size, format_timeleft
from ..webshare import WebshareError

HERE = Path(__file__).parent
templates = Jinja2Templates(directory=HERE / "templates")
templates.env.filters["size"] = format_size
templates.env.filters["duration"] = lambda s: format_timeleft(s or 0)
templates.env.filters["ts"] = lambda t: time.strftime("%d.%m.%Y %H:%M", time.localtime(t)) if t else ""


def _basic_credentials(request: Request) -> tuple[str, str] | None:
    # Parsed by hand: FastAPI's HTTPBasic only accepts ASCII, but passwords may contain diacritics.
    scheme, _, param = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() != "basic":
        return None
    try:
        username, sep, password = base64.b64decode(param).decode("utf-8").partition(":")
    except (binascii.Error, UnicodeDecodeError):
        return None
    return (username, password) if sep else None


def ui_auth(request: Request) -> None:
    settings = request.app.state.svc.settings
    if request.method == "POST" and request.headers.get("HX-Request") != "true":
        # All UI actions are sent by htmx; a cross-site form cannot add this header (CSRF).
        raise HTTPException(403, "Missing HX-Request header")
    if not settings.ui_username:
        return
    credentials = _basic_credentials(request)
    if (
        credentials is None
        or not secrets.compare_digest(credentials[0].encode(), settings.ui_username.encode())
        or not secrets.compare_digest(credentials[1].encode(), (settings.ui_password or "").encode())
    ):
        raise HTTPException(401, "Unauthorized", headers={"WWW-Authenticate": 'Basic realm="wsdarr"'})


router = APIRouter(prefix="/ui", dependencies=[Depends(ui_auth)], include_in_schema=False)


def mount_static(app: FastAPI) -> None:
    app.mount("/ui/static", StaticFiles(directory=HERE / "static"), name="ui-static")


def render(request: Request, name: str, **context: Any) -> HTMLResponse:
    svc = request.app.state.svc
    return templates.TemplateResponse(
        request, name, {"svc": svc, "settings": svc.settings, "version": __version__, **context}
    )


async def _cached_status(svc, key: str, ttl: float, factory) -> dict:
    entry = svc.status.get(key)
    if entry and entry["expires"] > time.monotonic():
        return entry["value"]
    try:
        value = {"ok": True, "data": await factory()}
    except (WebshareError, ArrError, Exception) as exc:  # show any failure in the UI
        value = {"ok": False, "error": str(exc)}
    svc.status[key] = {"expires": time.monotonic() + ttl, "value": value}
    return value


async def webshare_status(svc, refresh: bool = False) -> dict:
    if not svc.webshare.has_credentials:
        return {"ok": False, "error": "Není nastaveno WEBSHARE_USERNAME / WEBSHARE_PASSWORD"}
    if refresh:
        svc.status.pop("webshare", None)
    return await _cached_status(svc, "webshare", 300, svc.webshare.user_data)


async def connection_status(svc, refresh: bool = False) -> list[dict]:
    results = []
    clients = [*svc.arr_clients, *([svc.prowlarr] if svc.prowlarr else [])]
    for client in clients:
        key = f"arr:{client.name}"
        if refresh:
            svc.status.pop(key, None)
        status = await _cached_status(svc, key, 60, client.system_status)
        results.append({"name": client.name, "kind": client.kind, "url": client.instance.url, **status})
    return results


def disk_free(path: Path) -> int | None:
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return None


def job_rows(svc) -> tuple[list[dict], list[dict]]:
    queue = []
    for job in svc.db.jobs_queue():
        progress = svc.downloads.progress.get(job.nzo_id)
        done = progress.bytes_done if progress else job.bytes_done
        speed = progress.speed if progress else 0
        size = job.size or done
        queue.append(
            {
                "job": job,
                "done": done,
                "speed": speed,
                "percent": int(done * 100 / size) if size else 0,
                "eta": (size - done) / speed if speed else None,
            }
        )
    history = [{"job": job} for job in svc.db.jobs_history(limit=100)]
    return queue, history


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, refresh: bool = False):
    svc = request.app.state.svc
    queue, history = job_rows(svc)
    return render(
        request,
        "dashboard.html",
        webshare=await webshare_status(svc, refresh),
        connections=await connection_status(svc, refresh),
        queue_count=len(queue),
        speed=sum(r["speed"] for r in queue),
        history_count=len(history),
        failed_count=sum(1 for h in history if h["job"].status == "Failed"),
        disk_free=disk_free(svc.settings.download_dir),
        paused=svc.downloads.paused,
    )


@router.get("/queue", response_class=HTMLResponse)
async def queue_page(request: Request):
    svc = request.app.state.svc
    queue, history = job_rows(svc)
    return render(request, "queue.html", queue=queue, history=history, paused=svc.downloads.paused)


@router.get("/partials/queue", response_class=HTMLResponse)
async def queue_partial(request: Request):
    svc = request.app.state.svc
    queue, history = job_rows(svc)
    return render(request, "_queue.html", queue=queue, history=history, paused=svc.downloads.paused)


@router.post("/jobs/{nzo_id}/{action}", response_class=HTMLResponse)
async def job_action(request: Request, nzo_id: str, action: str):
    downloads = request.app.state.svc.downloads
    if action == "pause":
        downloads.pause_jobs([nzo_id])
    elif action == "resume":
        downloads.resume_jobs([nzo_id])
    elif action == "retry":
        downloads.retry(nzo_id)
    elif action == "delete":
        downloads.delete([nzo_id], delete_files=False)
    elif action == "delete-files":
        downloads.delete([nzo_id], delete_files=True)
    else:
        raise HTTPException(404)
    return await queue_partial(request)


@router.post("/queue/{action}", response_class=HTMLResponse)
async def queue_action(request: Request, action: str):
    downloads = request.app.state.svc.downloads
    if action == "pause":
        downloads.pause_all()
    elif action == "resume":
        downloads.resume_all()
    else:
        raise HTTPException(404)
    return await queue_partial(request)


@router.get("/search", response_class=HTMLResponse)
async def search_page(request: Request, q: str = "", kind: str = "unknown", raw: bool = False):
    svc = request.app.state.svc
    context: dict[str, Any] = {"q": q, "kind": kind, "raw": raw, "results": None, "ctx": None, "error": None}
    if q.strip():
        try:
            if raw:
                page = await svc.webshare.search(
                    q.strip(), category="video", limit=svc.settings.search_page_size
                )
                rows = []
                for f in page.files:
                    parsed = parse_filename(f.name)
                    rows.append(
                        {
                            "ident": f.ident,
                            "title": build_release_title(
                                parsed,
                                None,
                                svc.settings.release_group,
                                svc.settings.unknown_quality == "extension",
                            ),
                            "ws_name": f.name,
                            "size": f.size,
                            "languages": newznab_languages(parsed),
                            "resolution": parsed.resolution,
                            "votes": f.positive_votes - f.negative_votes,
                            "kind": "tv" if parsed.is_episode else "movie",
                            "note": "chráněno heslem" if f.password else "",
                        }
                    )
                context["results"] = rows
            else:
                ctx = await svc.resolver.resolve(kind if kind in ("tv", "movie") else "unknown", q=q.strip())
                context["ctx"] = ctx
                context["results"] = [r.summary() for r in await svc.search.search(ctx)]
        except WebshareError as exc:
            context["error"] = f"Webshare: {exc}"
    return render(request, "search.html", **context)


@router.post("/download", response_class=HTMLResponse)
async def manual_download(
    request: Request,
    ident: str = Form(...),
    title: str = Form(...),
    ws_name: str = Form(""),
    size: int = Form(0),
    category: str = Form(...),
):
    svc = request.app.state.svc
    job = svc.downloads.add(ident=ident, name=title, category=category, ws_name=ws_name, size=size)
    return HTMLResponse(f'<span class="badge ok">Přidáno do fronty ({html.escape(job.category)})</span>')


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    svc = request.app.state.svc
    return render(request, "settings.html", summary=svc.settings.public_summary())


@router.post("/settings/test", response_class=HTMLResponse)
async def settings_test(request: Request):
    svc = request.app.state.svc
    return render(
        request,
        "_tests.html",
        webshare=await webshare_status(svc, refresh=True),
        connections=await connection_status(svc, refresh=True),
    )


@router.post("/setup", response_class=HTMLResponse)
async def setup(request: Request):
    from ..provision.setup import run_setup

    report = await run_setup(request.app.state.svc)
    return render(request, "_report.html", report=report)
