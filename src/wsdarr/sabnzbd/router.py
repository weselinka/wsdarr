"""SABnzbd-compatible API (the subset Sonarr/Radarr use).

Field names and formats follow Sonarr's SABnzbd client models
(``SabnzbdQueueItem``, ``SabnzbdHistoryItem``, ``SabnzbdConfig``...).
"""

from __future__ import annotations

import logging
import shutil
import time
from typing import Any

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..db import Job
from ..downloader.manager import COMPLETED, DOWNLOADING, FAILED
from ..newznab.nzb import parse_nzb

log = logging.getLogger(__name__)

router = APIRouter()

SAB_VERSION = "4.3.3"
PRIORITY_NAMES = {-2: "Paused", -1: "Low", 0: "Normal", 1: "High", 2: "Force"}


def ok(**data: Any) -> JSONResponse:
    return JSONResponse({"status": True, **data})


def error(message: str) -> JSONResponse:
    return JSONResponse({"status": False, "error": message})


def format_timeleft(seconds: float | None) -> str:
    if seconds is None or seconds < 0:
        return "0:00:00"
    seconds = int(seconds)
    return f"{seconds // 3600}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def format_size(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num) < 1024 or unit == "TB":
            return f"{num:.1f} {unit}" if unit != "B" else f"{int(num)} B"
        num /= 1024
    return f"{num:.1f} TB"


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _ids(value: str | None) -> list[str]:
    return [v for v in (value or "").split(",") if v]


async def _params(request: Request) -> dict[str, Any]:
    params: dict[str, Any] = dict(request.query_params)
    if request.method == "POST":
        form = await request.form()
        for key, value in form.multi_items():
            params.setdefault(key, value)
    return params


def _authorized(svc, params: dict[str, Any]) -> bool:
    key = params.get("apikey")
    return bool(key) and key == svc.api_key


def queue_slot(svc, job: Job, index: int, paused_all: bool) -> dict:
    progress = svc.downloads.progress.get(job.nzo_id)
    done = progress.bytes_done if progress else job.bytes_done
    speed = progress.speed if progress else 0.0
    size = job.size or done
    left = max(size - done, 0)
    if job.paused or (paused_all and job.priority != 2):
        status = "Paused"
    elif job.status == DOWNLOADING:
        status = "Downloading"
    else:
        status = "Queued"
    timeleft = left / speed if speed > 0 and status == "Downloading" else 0
    return {
        "index": index,
        "nzo_id": job.nzo_id,
        "filename": job.name,
        "cat": job.category,
        "priority": PRIORITY_NAMES.get(job.priority, "Normal"),
        "status": status,
        "mb": f"{size / 1048576:.2f}",
        "mbleft": f"{left / 1048576:.2f}",
        "size": format_size(size),
        "sizeleft": format_size(left),
        "percentage": str(int(done * 100 / size) if size else 0),
        "timeleft": format_timeleft(timeleft),
        "labels": [],
        "script": "None",
        "unpackopts": "3",
        "avg_age": "0d",
        "password": "",
    }


def history_slot(job: Job) -> dict:
    return {
        "nzo_id": job.nzo_id,
        "name": job.name,
        "nzb_name": f"{job.name}.nzb",
        "category": job.category,
        "status": COMPLETED if job.status == COMPLETED else FAILED,
        "fail_message": job.fail_message if job.status == FAILED else "",
        "bytes": job.bytes_done or job.size,
        "size": format_size(job.bytes_done or job.size),
        "download_time": job.download_time,
        "completed": int(job.completed_at or time.time()),
        "storage": job.storage,
        "path": job.storage,
        "script": "None",
        "pp": "D",
        "retry": 1 if job.status == FAILED else 0,
    }


def _filter_category(jobs: list[Job], category: str | None) -> list[Job]:
    if not category or category == "*":
        return jobs
    return [j for j in jobs if j.category == category]


@router.api_route("/sabnzbd/api", methods=["GET", "POST"])
async def sab_api(request: Request):
    svc = request.app.state.svc
    params = await _params(request)
    return await handle(svc, params)


async def handle(svc, params: dict[str, Any]):
    mode = params.get("mode", "")
    if mode == "version":
        return JSONResponse({"version": SAB_VERSION})
    if not _authorized(svc, params):
        return error("API Key Incorrect" if params.get("apikey") else "API Key Required")

    downloads = svc.downloads
    settings = svc.settings
    name = params.get("name")

    if mode in ("addfile", "addlocalfile"):
        upload = params.get("name") or params.get("nzbfile")
        if upload is None or not hasattr(upload, "read"):
            return error("No NZB file provided")
        content = await upload.read()
        filename = getattr(upload, "filename", "") or ""
        return _add(svc, content, params, filename)

    if mode == "addurl":
        url = params.get("name")
        if not url:
            return error("No URL provided")
        try:
            resp = await svc.http.get(url, timeout=60, follow_redirects=True)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            return error(f"Failed to fetch NZB: {exc}")
        filename = ""
        disposition = resp.headers.get("Content-Disposition", "")
        if "filename=" in disposition:
            filename = disposition.split("filename=", 1)[1].strip('"; ')
        return _add(svc, resp.content, params, filename)

    if mode == "queue":
        if name == "delete":
            value = params.get("value", "")
            ids = [j.nzo_id for j in downloads.db.jobs_queue()] if value == "all" else _ids(value)
            removed = downloads.delete(ids, delete_files=params.get("del_files") in ("1", 1, True))
            return ok(nzo_ids=removed)
        if name == "pause":
            downloads.pause_jobs(_ids(params.get("value")))
            return ok(nzo_ids=_ids(params.get("value")))
        if name == "resume":
            downloads.resume_jobs(_ids(params.get("value")))
            return ok(nzo_ids=_ids(params.get("value")))
        jobs = _filter_category(downloads.db.jobs_queue(), params.get("category") or params.get("cat"))
        start = _to_int(params.get("start"))
        limit = _to_int(params.get("limit"))
        page = jobs[start : start + limit] if limit else jobs[start:]
        paused_all = downloads.paused
        slots = [queue_slot(svc, job, start + i, paused_all) for i, job in enumerate(page)]
        speed = sum(p.speed for p in downloads.progress.values())
        mb_left = sum(float(s["mbleft"]) for s in slots)
        return JSONResponse(
            {
                "queue": {
                    "version": SAB_VERSION,
                    "paused": paused_all,
                    "status": "Paused" if paused_all else ("Downloading" if speed else "Idle"),
                    "speed": format_size(speed).rstrip("B").strip(),
                    "kbpersec": f"{speed / 1024:.2f}",
                    "speedlimit": str(settings.speed_limit_kbps or ""),
                    "noofslots": len(jobs),
                    "noofslots_total": len(jobs),
                    "start": start,
                    "limit": limit,
                    "mbleft": f"{mb_left:.2f}",
                    "my_home": str(settings.download_dir),
                    "diskspace1": _free_gb(settings.complete_dir),
                    "slots": slots,
                }
            }
        )

    if mode == "history":
        if name == "delete":
            value = params.get("value", "")
            if value in ("all", "failed", "completed"):
                wanted = {"all": None, "failed": FAILED, "completed": COMPLETED}[value]
                ids = [j.nzo_id for j in downloads.db.jobs_history() if wanted is None or j.status == wanted]
            else:
                ids = _ids(value)
            removed = downloads.delete(ids, delete_files=params.get("del_files") in ("1", 1, True))
            return ok(nzo_ids=removed)
        jobs = _filter_category(downloads.db.jobs_history(), params.get("category") or params.get("cat"))
        start = _to_int(params.get("start"))
        limit = _to_int(params.get("limit"))
        page = jobs[start : start + limit] if limit else jobs[start:]
        return JSONResponse(
            {
                "history": {
                    "version": SAB_VERSION,
                    "paused": downloads.paused,
                    "noofslots": len(jobs),
                    "total_size": format_size(sum(j.bytes_done or j.size for j in jobs)),
                    "slots": [history_slot(job) for job in page],
                }
            }
        )

    if mode == "retry":
        value = params.get("value", "")
        if downloads.retry(value):
            return ok(nzo_id=value)
        return error("Job not found or not failed")

    if mode == "pause":
        downloads.pause_all()
        return ok()
    if mode == "resume":
        downloads.resume_all()
        return ok()

    if mode == "get_config":
        return JSONResponse({"config": sab_config(settings)})
    if mode == "fullstatus":
        return JSONResponse(
            {
                "status": {
                    "completedir": str(settings.complete_dir),
                    "downloaddir": str(settings.incomplete_dir),
                    "paused": downloads.paused,
                    "version": SAB_VERSION,
                }
            }
        )
    if mode == "get_cats":
        return JSONResponse({"categories": ["*", *settings.categories]})
    if mode == "get_scripts":
        return JSONResponse({"scripts": ["None"]})
    if mode == "warnings":
        return JSONResponse({"warnings": []})
    if mode == "server_stats":
        return JSONResponse({"total": 0, "month": 0, "week": 0, "day": 0, "servers": {}})
    if mode == "change_cat":
        downloads.set_category(params.get("value", ""), params.get("value2", ""))
        return ok()
    if mode == "priority":
        try:
            downloads.set_priority(params.get("value", ""), int(params.get("value2", 0)))
        except ValueError:
            return error("Invalid priority")
        return ok()
    if mode in ("config", "set_config", "switch", "change_opts", "change_script", "restart"):
        return ok()
    return error("not implemented")


def _free_gb(path) -> str:
    try:
        return f"{shutil.disk_usage(path).free / 1024**3:.2f}"
    except OSError:
        return "0"


def _add(svc, content: bytes, params: dict[str, Any], filename: str) -> JSONResponse:
    try:
        info = parse_nzb(content)
    except ValueError as exc:
        log.warning("Rejected NZB %s: %s", filename, exc)
        return error(str(exc))
    name = params.get("nzbname") or (filename[:-4] if filename.lower().endswith(".nzb") else filename)
    name = name or info.title or info.ident
    try:
        priority = int(params.get("priority", 0))
    except ValueError:
        priority = 0
    if priority == -100:
        priority = 0
    job = svc.downloads.add(
        ident=info.ident,
        name=name,
        category=params.get("cat") or "*",
        ws_name=info.ws_name,
        size=info.size,
        priority=priority,
    )
    return ok(nzo_ids=[job.nzo_id])


def sab_config(settings) -> dict:
    categories = [
        {"name": "*", "order": 0, "pp": "3", "script": "None", "dir": "", "newzbin": "", "priority": 0}
    ]
    for i, cat in enumerate(settings.categories, start=1):
        categories.append(
            {
                "name": cat,
                "order": i,
                "pp": "",
                "script": "Default",
                "dir": cat,
                "newzbin": "",
                "priority": -100,
            }
        )
    return {
        "misc": {
            "complete_dir": str(settings.complete_dir),
            "download_dir": str(settings.incomplete_dir),
            "tv_categories": [],
            "enable_tv_sorting": False,
            "movie_categories": [],
            "enable_movie_sorting": False,
            "date_categories": [],
            "enable_date_sorting": False,
            "pre_check": False,
            "history_retention": "",
            "history_retention_option": "all",
            "history_retention_number": 0,
            "port": str(settings.port),
            "version": SAB_VERSION,
        },
        "categories": categories,
        "servers": [],
        "sorters": [],
    }
