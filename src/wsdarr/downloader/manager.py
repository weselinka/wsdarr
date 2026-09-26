"""Download queue: fetches Webshare files for jobs added through the SABnzbd API."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import secrets
import shutil
import string
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

import httpx

from ..config import Settings
from ..db import Database, Job
from ..webshare import WebshareClient, WebshareError

log = logging.getLogger(__name__)

CHUNK = 1024 * 1024

QUEUED = "Queued"
DOWNLOADING = "Downloading"
COMPLETED = "Completed"
FAILED = "Failed"


class Cancelled(Exception):
    def __init__(self, action: str):
        super().__init__(action)
        self.action = action  # "pause" | "delete" | "stop"


class DownloadError(Exception):
    pass


@dataclass
class Progress:
    bytes_done: int = 0
    speed: float = 0.0  # bytes per second (smoothed)
    updated: float = 0.0


def new_nzo_id() -> str:
    alphabet = string.ascii_lowercase + string.digits
    return "SABnzbd_nzo_" + "".join(secrets.choice(alphabet) for _ in range(10))


def safe_name(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', " ", name).strip().strip(".")
    name = re.sub(r"\s+", " ", name)
    return name[:200] or "download"


def extension_for(job: Job) -> str:
    if "." in job.ws_name:
        ext = job.ws_name.rsplit(".", 1)[-1].lower()
        if 1 < len(ext) <= 5 and ext.isalnum():
            return ext
    return "mkv"


class DownloadManager:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        webshare: WebshareClient,
        http: httpx.AsyncClient | None = None,
        on_complete: Callable[[Job], Awaitable[None]] | None = None,
    ):
        self.settings = settings
        self.db = db
        self.ws = webshare
        self._http = http or httpx.AsyncClient(
            timeout=httpx.Timeout(connect=30, read=120, write=30, pool=30), follow_redirects=True
        )
        self._owns_http = http is None
        self.on_complete = on_complete
        self.progress: dict[str, Progress] = {}
        self._cancel: dict[str, str] = {}
        self._active: set[str] = set()
        self._retry_at: dict[str, float] = {}
        self._wake = asyncio.Event()
        self._workers: list[asyncio.Task] = []
        self._pick_lock = asyncio.Lock()
        self._running = False

    # --- lifecycle -----------------------------------------------------------------------
    @property
    def paused(self) -> bool:
        return self.db.kv_get("queue_paused") == "1"

    async def start(self) -> None:
        self.settings.incomplete_dir.mkdir(parents=True, exist_ok=True)
        # Sonarr/Radarr health checks expect the category folders to exist.
        for category in self.settings.categories:
            (self.settings.complete_dir / safe_name(category)).mkdir(parents=True, exist_ok=True)
        for job in self.db.jobs_queue():
            if job.status == DOWNLOADING:
                self.db.job_update(job.nzo_id, status=QUEUED, speed=0)
        self._running = True
        self._workers = [
            asyncio.create_task(self._worker(i), name=f"wsdarr-download-{i}")
            for i in range(max(1, self.settings.max_concurrent_downloads))
        ]

    async def stop(self) -> None:
        self._running = False
        for nzo_id in list(self._active):
            self._cancel[nzo_id] = "stop"
        self._wake.set()
        for task in self._workers:
            task.cancel()
        for task in self._workers:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._workers = []
        if self._owns_http:
            await self._http.aclose()

    def wake(self) -> None:
        self._wake.set()

    # --- queue operations ----------------------------------------------------------------
    def add(
        self,
        *,
        ident: str,
        name: str,
        category: str,
        ws_name: str = "",
        size: int = 0,
        priority: int = 0,
    ) -> Job:
        paused = priority == -2
        job = Job(
            nzo_id=new_nzo_id(),
            name=safe_name(name),
            category=category or "*",
            ident=ident,
            ws_name=ws_name,
            size=size,
            status=QUEUED,
            priority=0 if paused else priority,
            paused=paused,
        )
        self.db.job_insert(job)
        log.info("Queued %s (%s) as %s in category %s", job.name, ident, job.nzo_id, job.category)
        self.wake()
        return job

    def _job_dir(self, job: Job) -> Path:
        return self.settings.incomplete_dir / job.nzo_id

    def delete(self, nzo_ids: list[str], delete_files: bool = False) -> list[str]:
        removed = []
        for nzo_id in nzo_ids:
            job = self.db.job_get(nzo_id)
            if not job:
                continue
            if nzo_id in self._active:
                self._cancel[nzo_id] = "delete"
            shutil.rmtree(self._job_dir(job), ignore_errors=True)
            if delete_files and job.storage:
                storage = Path(job.storage)
                with contextlib.suppress(OSError):
                    if storage.is_dir() and self._inside_complete(storage):
                        shutil.rmtree(storage)
                    elif storage.is_file() and self._inside_complete(storage):
                        storage.unlink()
            self.db.job_delete(nzo_id)
            self.progress.pop(nzo_id, None)
            removed.append(nzo_id)
        return removed

    def _inside_complete(self, path: Path) -> bool:
        try:
            path.resolve().relative_to(self.settings.complete_dir.resolve())
            return True
        except ValueError:
            return False

    def pause_jobs(self, nzo_ids: list[str]) -> None:
        for nzo_id in nzo_ids:
            self.db.job_update(nzo_id, paused=True)
            if nzo_id in self._active:
                self._cancel[nzo_id] = "pause"

    def resume_jobs(self, nzo_ids: list[str]) -> None:
        for nzo_id in nzo_ids:
            self.db.job_update(nzo_id, paused=False)
        self.wake()

    def pause_all(self) -> None:
        self.db.kv_set("queue_paused", "1")
        for nzo_id in list(self._active):
            self._cancel[nzo_id] = "pause"

    def resume_all(self) -> None:
        self.db.kv_set("queue_paused", "0")
        self.wake()

    def retry(self, nzo_id: str) -> bool:
        job = self.db.job_get(nzo_id)
        if not job or job.status != FAILED:
            return False
        self.db.job_update(
            nzo_id,
            status=QUEUED,
            in_history=False,
            fail_message="",
            attempts=0,
            completed_at=None,
            paused=False,
        )
        self._retry_at.pop(nzo_id, None)
        self.wake()
        return True

    def set_category(self, nzo_id: str, category: str) -> None:
        self.db.job_update(nzo_id, category=category)

    def set_priority(self, nzo_id: str, priority: int) -> None:
        self.db.job_update(nzo_id, priority=priority)

    # --- workers -------------------------------------------------------------------------
    async def _next_job(self) -> Job | None:
        async with self._pick_lock:
            paused = self.paused
            now = time.time()
            for job in self.db.jobs_queue():
                if job.paused or job.status != QUEUED or job.nzo_id in self._active:
                    continue
                if paused and job.priority != 2:  # like SABnzbd, "Force" ignores the global pause
                    continue
                if self._retry_at.get(job.nzo_id, 0) > now:
                    continue
                self._active.add(job.nzo_id)
                return job
            return None

    async def _worker(self, index: int) -> None:
        while self._running:
            job = await self._next_job()
            if job is None:
                self._wake.clear()
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self._wake.wait(), timeout=5)
                continue
            try:
                await self._run_job(job)
            except asyncio.CancelledError:
                raise
            except Exception:  # never let a worker die
                log.exception("Unexpected error while downloading %s", job.nzo_id)
            finally:
                self._active.discard(job.nzo_id)
                self._cancel.pop(job.nzo_id, None)
            self.wake()

    async def _run_job(self, job: Job) -> None:
        started = time.time()
        self.db.job_update(job.nzo_id, status=DOWNLOADING, started_at=job.started_at or started)
        try:
            final_path = await self._download(job)
        except Cancelled as exc:
            if exc.action in ("pause", "stop"):
                self.db.job_update(job.nzo_id, status=QUEUED, speed=0)
            log.info(
                "Download %s %s",
                job.nzo_id,
                {"pause": "paused", "delete": "deleted"}.get(exc.action, "stopped"),
            )
            return
        except (WebshareError, DownloadError, httpx.HTTPError, OSError) as exc:
            attempts = job.attempts + 1
            message = str(exc) or exc.__class__.__name__
            if attempts < self.settings.download_retries and not _is_permanent(exc):
                delay = min(600, 30 * 2 ** (attempts - 1))
                self._retry_at[job.nzo_id] = time.time() + delay
                self.db.job_update(
                    job.nzo_id, status=QUEUED, attempts=attempts, speed=0, fail_message=message
                )
                log.warning("Download %s failed (%s), retry %d in %ds", job.nzo_id, message, attempts, delay)
                return
            self.db.job_update(
                job.nzo_id,
                status=FAILED,
                attempts=attempts,
                speed=0,
                fail_message=message,
                in_history=True,
                completed_at=time.time(),
                download_time=int(time.time() - started),
            )
            self.progress.pop(job.nzo_id, None)
            log.error("Download %s failed permanently: %s", job.nzo_id, message)
            return

        self.db.job_update(
            job.nzo_id,
            status=COMPLETED,
            in_history=True,
            storage=str(final_path.parent),
            completed_at=time.time(),
            download_time=int(time.time() - started),
            bytes_done=final_path.stat().st_size,
            speed=0,
            fail_message="",
        )
        self.progress.pop(job.nzo_id, None)
        log.info("Completed %s -> %s", job.name, final_path)
        if self.on_complete:
            done = self.db.job_get(job.nzo_id)
            if done:
                try:
                    await self.on_complete(done)
                except Exception as exc:  # notification problems must not fail the job
                    log.warning("on_complete hook failed for %s: %s", job.nzo_id, exc)

    def _check_cancel(self, nzo_id: str) -> None:
        action = self._cancel.get(nzo_id)
        if action:
            raise Cancelled(action)

    async def _download(self, job: Job) -> Path:
        ext = extension_for(job)
        work_dir = self._job_dir(job)
        work_dir.mkdir(parents=True, exist_ok=True)
        part = work_dir / f"{job.name}.{ext}.part"

        link = await self.ws.file_link(job.ident)
        self._check_cancel(job.nzo_id)

        existing = part.stat().st_size if part.exists() else 0
        if job.size and existing >= job.size:
            existing = job.size
        headers = {"User-Agent": "wsdarr/0.1"}
        if existing and (not job.size or existing < job.size):
            headers["Range"] = f"bytes={existing}-"

        progress = self.progress.setdefault(job.nzo_id, Progress())
        progress.bytes_done = existing
        progress.updated = time.monotonic()

        if not (job.size and existing >= job.size):
            async with self._http.stream("GET", link, headers=headers) as resp:
                if resp.status_code == 416 and existing:
                    pass  # already complete
                elif resp.status_code not in (200, 206):
                    raise DownloadError(f"HTTP {resp.status_code} from Webshare download server")
                else:
                    if resp.status_code == 200 and existing:
                        existing = 0  # server ignored Range; start over
                        progress.bytes_done = 0
                    total = job.size or int(resp.headers.get("Content-Length", 0)) + existing
                    if total and not job.size:
                        self.db.job_update(job.nzo_id, size=total)
                        job.size = total
                    await self._write_stream(job, resp, part, append=bool(existing), progress=progress)

        size = part.stat().st_size
        if job.size and size != job.size:
            raise DownloadError(f"Size mismatch: got {size} bytes, expected {job.size}")

        target_dir = self.settings.complete_dir / safe_name(job.category) / job.name
        if target_dir.exists():
            target_dir = target_dir.with_name(f"{job.name}.{job.nzo_id[-6:]}")
        target_dir.mkdir(parents=True, exist_ok=True)
        final = target_dir / f"{job.name}.{ext}"
        shutil.move(str(part), final)
        shutil.rmtree(work_dir, ignore_errors=True)
        return final

    async def _write_stream(
        self, job: Job, resp: httpx.Response, part: Path, append: bool, progress: Progress
    ):
        limit = self.settings.speed_limit_kbps * 1024
        window_start = time.monotonic()
        window_bytes = 0
        last_db = 0.0
        fh = await asyncio.to_thread(open, part, "ab" if append else "wb")
        try:
            async for chunk in resp.aiter_bytes(CHUNK):
                self._check_cancel(job.nzo_id)
                await asyncio.to_thread(fh.write, chunk)
                progress.bytes_done += len(chunk)
                window_bytes += len(chunk)
                now = time.monotonic()
                elapsed = now - window_start
                if elapsed >= 1:
                    current = window_bytes / elapsed
                    progress.speed = current if not progress.speed else 0.7 * progress.speed + 0.3 * current
                    window_start, window_bytes = now, 0
                if limit:
                    share = limit / max(1, len(self._active))
                    expected = window_bytes / share
                    if expected > elapsed:
                        await asyncio.sleep(expected - elapsed)
                if now - last_db > 5:
                    last_db = now
                    self.db.job_update(job.nzo_id, bytes_done=progress.bytes_done, speed=progress.speed)
        finally:
            await asyncio.to_thread(fh.close)
        self.db.job_update(job.nzo_id, bytes_done=progress.bytes_done)


PERMANENT_HINTS = ("not found", "nenalezen", "neexist", "password", "heslo", "deleted", "smazan", "removed")


def _is_permanent(exc: Exception) -> bool:
    """File removed or password protected: retrying will not help."""
    if isinstance(exc, WebshareError):
        text = f"{exc.code} {exc.message}".lower()
        return any(hint in text for hint in PERMANENT_HINTS)
    return False
