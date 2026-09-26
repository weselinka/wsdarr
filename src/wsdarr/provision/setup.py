"""Auto-setup: register wsdarr in Prowlarr/Sonarr/Radarr through their APIs.

Idempotent – resources are found by name and updated in place:

* Sonarr/Radarr: a SABnzbd download client pointing at wsdarr's ``/sabnzbd`` API.
* With Prowlarr: a "Generic Newznab" indexer in Prowlarr; after Prowlarr syncs it to the apps,
  the synced indexers are bound (``downloadClientId``) to the wsdarr download client so the
  NZB envelopes never reach a real SABnzbd/NZBGet.
* Without Prowlarr: a Newznab indexer directly in Sonarr/Radarr, bound the same way.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from ..metadata.arr import ArrClient, ArrError

log = logging.getLogger(__name__)

TV_CATEGORIES = [5000, 5030, 5040, 5045]
MOVIE_CATEGORIES = [2000, 2030, 2040, 2045]
CATEGORY_FIELDS = ("tvCategory", "movieCategory", "category")


@dataclass
class SetupReport:
    entries: list[tuple[str, str, str]] = field(default_factory=list)

    def add(self, target: str, action: str, detail: str = "") -> None:
        self.entries.append((target, action, detail))
        log.info("setup %s: %s %s", target, action, detail)

    @property
    def ok(self) -> bool:
        return not any(action == "error" for _, action, _ in self.entries)

    def lines(self) -> list[str]:
        return [
            f"{target}: {action}" + (f" – {detail}" if detail else "")
            for target, action, detail in self.entries
        ]

    def as_dicts(self) -> list[dict]:
        return [{"target": t, "action": a, "detail": d} for t, a, d in self.entries]


def get_field(resource: dict, name: str) -> Any:
    for f in resource.get("fields", []):
        if f.get("name") == name:
            return f.get("value")
    return None


def set_field(resource: dict, name: str, value: Any) -> bool:
    for f in resource.get("fields", []):
        if f.get("name") == name:
            f["value"] = value
            return True
    return False


def _fields_dict(resource: dict) -> dict:
    return {f.get("name"): f.get("value") for f in resource.get("fields", [])}


MASKED = "********"


async def _unchanged(client: ArrClient, resource: str, existing: dict, before: dict, after: dict) -> bool:
    """True when ``existing`` already matches the desired fields.

    The *arr APIs mask secrets (API keys) as ``********``; those fields cannot be compared, so
    the app's own connection test decides whether the stored secret still works.
    """
    for name, value in after.items():
        if before.get(name) != MASKED and before.get(name) != value:
            return False
    try:
        await client.post(f"{resource}/test", existing)
    except ArrError:
        return False
    return True


def endpoint_parts(public_url: str) -> dict:
    parts = urlsplit(public_url)
    ssl = parts.scheme == "https"
    return {
        "host": parts.hostname or "localhost",
        "port": parts.port or (443 if ssl else 80),
        "ssl": ssl,
        "prefix": parts.path.strip("/"),
    }


async def _save(client: ArrClient, resource: str, body: dict, report: SetupReport, label: str) -> dict:
    """Create or update a provider, retrying with ``forceSave`` when the *arr's own test fails."""
    item_id = body.get("id")
    method = client.put if item_id else client.post
    path = f"{resource}/{item_id}" if item_id else resource
    try:
        return await method(path, body)
    except ArrError as exc:
        report.add(label, "warning", f"test failed, saving anyway: {exc}")
        return await method(path, body, forceSave="true")


async def ensure_download_client(svc, client: ArrClient, report: SetupReport) -> int | None:
    settings = svc.settings
    label = f"{client.name} download client"
    ep = endpoint_parts(settings.public_url)
    url_base = "/".join(p for p in (ep["prefix"], "sabnzbd") if p)
    try:
        existing = next(
            (c for c in await client.get("downloadclient") if c.get("name") == settings.setup_client_name),
            None,
        )
        if existing:
            body = copy.deepcopy(existing)
        else:
            schema = await client.get("downloadclient/schema")
            template = next((s for s in schema if s.get("implementation") == "Sabnzbd"), None)
            if template is None:
                report.add(label, "error", "SABnzbd implementation not found in schema")
                return None
            body = copy.deepcopy(template)
            body.update({"name": settings.setup_client_name, "enable": True, "priority": 1, "tags": []})
        before = _fields_dict(body)
        set_field(body, "host", ep["host"])
        set_field(body, "port", ep["port"])
        set_field(body, "useSsl", ep["ssl"])
        set_field(body, "urlBase", url_base)
        set_field(body, "apiKey", svc.api_key)
        set_field(body, "username", "")
        set_field(body, "password", "")
        category = client.category or ("prowlarr" if client.kind == "prowlarr" else None)
        for name in CATEGORY_FIELDS:
            if category:
                set_field(body, name, category)
        if (
            existing
            and existing.get("enable")
            and await _unchanged(client, "downloadclient", existing, before, _fields_dict(body))
        ):
            report.add(label, "ok", f"'{settings.setup_client_name}' already configured")
            return existing["id"]
        body["enable"] = True
        saved = await _save(client, "downloadclient", body, report, label)
        report.add(
            label, "updated" if existing else "created", f"'{settings.setup_client_name}' -> {url_base}"
        )
        return saved.get("id") if isinstance(saved, dict) else None
    except ArrError as exc:
        report.add(label, "error", str(exc))
        return None


async def ensure_direct_indexer(svc, client: ArrClient, client_id: int | None, report: SetupReport) -> None:
    settings = svc.settings
    label = f"{client.name} indexer"
    base_url = f"{settings.public_url.rstrip('/')}/newznab"
    try:
        existing = next(
            (i for i in await client.get("indexer") if i.get("name") == settings.setup_indexer_name), None
        )
        if existing:
            body = copy.deepcopy(existing)
        else:
            schema = await client.get("indexer/schema")
            template = next((s for s in schema if s.get("implementation") == "Newznab"), None)
            if template is None:
                report.add(label, "error", "Newznab implementation not found in schema")
                return
            body = copy.deepcopy(template)
            body.update(
                {
                    "name": settings.setup_indexer_name,
                    "enableRss": True,
                    "enableAutomaticSearch": True,
                    "enableInteractiveSearch": True,
                    "tags": [],
                }
            )
            body.pop("presets", None)
        set_field(body, "baseUrl", base_url)
        set_field(body, "apiPath", "/api")
        set_field(body, "apiKey", svc.api_key)
        set_field(body, "categories", TV_CATEGORIES if client.kind == "sonarr" else MOVIE_CATEGORIES)
        if client.kind == "sonarr":
            set_field(body, "animeCategories", [])
        if client_id:
            body["downloadClientId"] = client_id
        await _save(client, "indexer", body, report, label)
        report.add(
            label, "updated" if existing else "created", f"'{settings.setup_indexer_name}' -> {base_url}"
        )
    except ArrError as exc:
        report.add(label, "error", str(exc))


async def ensure_prowlarr_indexer(svc, prowlarr: ArrClient, report: SetupReport) -> int | None:
    settings = svc.settings
    label = f"{prowlarr.name} indexer"
    base_url = f"{settings.public_url.rstrip('/')}/newznab"
    try:
        existing = next(
            (i for i in await prowlarr.get("indexer") if i.get("name") == settings.setup_indexer_name), None
        )
        if existing:
            body = copy.deepcopy(existing)
        else:
            schema = await prowlarr.get("indexer/schema")
            newznab = [s for s in schema if s.get("implementation") == "Newznab"]
            template = next(
                (
                    s
                    for s in newznab
                    if (s.get("definitionName") or s.get("name") or "").lower()
                    in ("generic newznab", "newznab")
                ),
                next((s for s in newznab if not get_field(s, "baseUrl")), None),
            )
            if template is None:
                report.add(label, "error", "Generic Newznab definition not found in schema")
                return None
            body = copy.deepcopy(template)
            profiles = await prowlarr.get("appprofile")
            body.update(
                {
                    "name": settings.setup_indexer_name,
                    "enable": True,
                    "appProfileId": profiles[0]["id"] if profiles else 1,
                    "tags": [],
                }
            )
            body.pop("presets", None)
        before = _fields_dict(body)
        set_field(body, "baseUrl", base_url)
        set_field(body, "apiPath", "/api")
        set_field(body, "apiKey", svc.api_key)
        if existing and await _unchanged(prowlarr, "indexer", existing, before, _fields_dict(body)):
            report.add(label, "ok", f"'{settings.setup_indexer_name}' already configured")
            return existing["id"]
        saved = await _save(prowlarr, "indexer", body, report, label)
        report.add(
            label, "updated" if existing else "created", f"'{settings.setup_indexer_name}' -> {base_url}"
        )
        return saved.get("id") if isinstance(saved, dict) else None
    except ArrError as exc:
        report.add(label, "error", str(exc))
        return None


def _is_synced_indexer(indexer: dict, name: str, prowlarr_id: int | None) -> bool:
    if indexer.get("name", "").startswith(name):
        return True
    base = str(get_field(indexer, "baseUrl") or "")
    return bool(prowlarr_id and re.search(rf"/{prowlarr_id}/?$", base))


async def bind_synced_indexers(
    svc,
    prowlarr_id: int | None,
    client_ids: dict[str, int | None],
    report: SetupReport,
    wait: float = 60,
    interval: float = 3,
) -> None:
    """Point the indexers Prowlarr synced into Sonarr/Radarr at the wsdarr download client."""
    pending = {c.name: c for c in svc.arr_clients if client_ids.get(c.name)}
    loop = asyncio.get_running_loop()
    deadline = loop.time() + wait
    while pending:
        for name, client in list(pending.items()):
            try:
                indexers = await client.get("indexer")
            except ArrError as exc:
                report.add(f"{name} indexer", "error", str(exc))
                pending.pop(name)
                continue
            synced = [
                i for i in indexers if _is_synced_indexer(i, svc.settings.setup_indexer_name, prowlarr_id)
            ]
            if not synced:
                continue
            for indexer in synced:
                if indexer.get("downloadClientId") == client_ids[name]:
                    report.add(f"{name} indexer", "ok", f"'{indexer['name']}' already uses wsdarr")
                    continue
                indexer["downloadClientId"] = client_ids[name]
                try:
                    await client.put(f"indexer/{indexer['id']}", indexer, forceSave="true")
                    report.add(f"{name} indexer", "updated", f"'{indexer['name']}' bound to wsdarr client")
                except ArrError as exc:
                    report.add(f"{name} indexer", "error", str(exc))
            pending.pop(name)
        if not pending or loop.time() > deadline:
            break
        await asyncio.sleep(interval)
    for name in pending:
        report.add(
            f"{name} indexer",
            "warning",
            "Prowlarr has not synced the indexer yet – check Prowlarr Settings > Apps, then run setup again",
        )


async def run_setup(svc, sync_timeout: float = 60, sync_interval: float = 3) -> SetupReport:
    report = SetupReport()
    if not svc.arr_clients and not svc.prowlarr:
        report.add("setup", "skipped", "no Sonarr/Radarr/Prowlarr configured")
        return report

    client_ids: dict[str, int | None] = {}
    for client in svc.arr_clients:
        client_ids[client.name] = await ensure_download_client(svc, client, report)

    if svc.prowlarr:
        await ensure_download_client(svc, svc.prowlarr, report)
        prowlarr_id = await ensure_prowlarr_indexer(svc, svc.prowlarr, report)
        if prowlarr_id is not None and svc.arr_clients:
            try:
                await svc.prowlarr.command("ApplicationIndexerSync")
                report.add(f"{svc.prowlarr.name}", "ok", "application sync triggered")
            except ArrError as exc:
                report.add(f"{svc.prowlarr.name}", "warning", f"could not trigger sync: {exc}")
            await bind_synced_indexers(
                svc, prowlarr_id, client_ids, report, wait=sync_timeout, interval=sync_interval
            )
    else:
        for client in svc.arr_clients:
            await ensure_direct_indexer(svc, client, client_ids.get(client.name), report)
    return report
