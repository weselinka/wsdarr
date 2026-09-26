"""End-to-end: Sonarr/Radarr style requests against the Newznab and SABnzbd APIs."""

from __future__ import annotations

from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

from conftest import wait_for
from fake_webshare import file_bytes

NS = {"newznab": "http://www.newznab.com/DTD/2010/feeds/attributes/"}
KEY = "testkey"


def items(xml: bytes) -> list[dict]:
    root = ET.fromstring(xml)
    out = []
    for item in root.iter("item"):
        attrs: dict[str, list[str]] = {}
        for a in item.findall("newznab:attr", NS):
            attrs.setdefault(a.get("name"), []).append(a.get("value"))
        enclosure = item.find("enclosure")
        out.append(
            {
                "title": item.findtext("title"),
                "guid": item.findtext("guid"),
                "url": enclosure.get("url"),
                "type": enclosure.get("type"),
                "length": int(enclosure.get("length")),
                "attrs": attrs,
                "description": item.findtext("description"),
            }
        )
    return out


def validate_nzb_like_sonarr(content: bytes) -> None:
    """Mirror of Sonarr's NzbValidationService."""
    root = ET.fromstring(content)
    assert root.tag.rsplit("}", 1)[-1] == "nzb"
    ns = root.tag.split("}")[0] + "}" if root.tag.startswith("{") else ""
    assert root.findall(f"{ns}file"), "NZB has no files"


async def test_caps(client):
    resp = await client.get("/newznab/api", params={"t": "caps"})
    root = ET.fromstring(resp.content)
    tv = root.find("searching/tv-search")
    assert tv.get("available") == "yes"
    assert {"q", "season", "ep", "tvdbid"} <= set(tv.get("supportedParams").split(","))
    assert root.find("searching/movie-search").get("supportedParams") == "q,imdbid,tmdbid"
    assert {c.get("id") for c in root.iter("category")} == {"2000", "5000"}


async def test_bad_api_key(client):
    resp = await client.get("/newznab/api", params={"t": "tvsearch", "apikey": "nope", "q": "x"})
    assert ET.fromstring(resp.content).get("code") == "100"
    resp = await client.get("/sabnzbd/api", params={"mode": "queue", "apikey": "nope", "output": "json"})
    assert resp.json() == {"status": False, "error": "API Key Incorrect"}


async def test_tv_search_download_and_import_flow(client, services, fake_sonarr, settings):
    # 1) Sonarr's ID based episode search (see NewznabRequestGenerator.AddTvIdPageableRequests)
    resp = await client.get(
        "/newznab/api",
        params={
            "t": "tvsearch",
            "cat": "5030,5040",
            "extended": 1,
            "apikey": KEY,
            "offset": 0,
            "limit": 100,
            "tvdbid": 81189,
            "season": 1,
            "ep": 2,
        },
    )
    found = items(resp.content)
    titles = {i["title"] for i in found}
    assert titles == {
        "Breaking.Bad.S01E02.720p.WEB-DL.CZ.SUBS-WS",
        "Breaking.Bad.S01E02.1080p.BluRay.x264.CZ-WS",
        "Breaking.Bad.S01E02.720p.CZ-WS",  # "Perníkový táta" file, matched via Sonarr alternate title
    }
    dub = next(i for i in found if "BluRay" in i["title"])
    assert dub["type"] == "application/x-nzb"
    assert dub["attrs"]["language"] == ["Czech"]
    assert dub["attrs"]["tvdbid"] == ["81189"]
    assert dub["attrs"]["size"] == ["4000000"]
    assert dub["description"] == "Breaking Bad S01E02 CZ dabing 1080p BluRay x264.mkv"
    subs = next(i for i in found if "SUBS" in i["title"])
    assert "language" not in subs["attrs"]

    # 2) Sonarr downloads the NZB from the enclosure url
    url = urlsplit(dub["url"])
    nzb = await client.get(f"{url.path}?{url.query}")
    assert nzb.status_code == 200
    validate_nzb_like_sonarr(nzb.content)

    # 3) ... and pushes it to "SABnzbd" (SabnzbdProxy.DownloadNzb)
    filename = f"{dub['title']}.nzb"
    resp = await client.post(
        "/sabnzbd/api",
        params={"mode": "addfile", "cat": "tv", "priority": 0, "apikey": KEY, "output": "json"},
        files={"name": (filename, nzb.content, "application/x-nzb")},
    )
    body = resp.json()
    assert body["status"] is True and len(body["nzo_ids"]) == 1
    nzo_id = body["nzo_ids"][0]

    # 4) Sonarr polls queue/history until the job is completed
    async def completed():
        r = await client.get(
            "/sabnzbd/api",
            params={
                "mode": "history",
                "start": 0,
                "limit": 60,
                "category": "tv",
                "apikey": KEY,
                "output": "json",
            },
        )
        slots = r.json()["history"]["slots"]
        return slots if slots and slots[0]["status"] == "Completed" else None

    slots = await wait_for(completed)
    slot = slots[0]
    assert slot["nzo_id"] == nzo_id
    assert slot["name"] == dub["title"]
    assert slot["category"] == "tv"
    storage = settings.complete_dir / "tv" / dub["title"]
    assert slot["storage"] == str(storage)
    video = storage / f"{dub['title']}.mkv"
    assert video.read_bytes() == file_bytes("a2", 4_000_000)
    assert slot["bytes"] == 4_000_000

    queue = await client.get(
        "/sabnzbd/api",
        params={"mode": "queue", "start": 0, "limit": 0, "category": "tv", "apikey": KEY, "output": "json"},
    )
    assert queue.json()["queue"]["slots"] == []

    # wsdarr nudges Sonarr to import right away
    # (the job shows as completed just before the notification is sent, so wait for it)
    async def notified():
        return {"name": "RefreshMonitoredDownloads"} in fake_sonarr.state.data["commands"]

    await wait_for(notified)

    # 5) After import Sonarr removes the item (RemoveFromHistory with del_files)
    resp = await client.get(
        "/sabnzbd/api",
        params={
            "mode": "history",
            "name": "delete",
            "del_files": 1,
            "value": nzo_id,
            "archive": 1,
            "apikey": KEY,
            "output": "json",
        },
    )
    assert resp.json()["status"] is True
    assert not storage.exists()


async def test_movie_search_by_tmdb_and_imdb(client):
    params = {"t": "movie", "cat": "2000", "extended": 1, "apikey": KEY, "offset": 0, "limit": 100}
    by_tmdb = items((await client.get("/newznab/api", params={**params, "tmdbid": 122})).content)
    titles = {i["title"] for i in by_tmdb}
    assert titles == {
        "The.Lord.of.the.Rings.The.Return.of.the.King.2003.1080p.CZ-WS",
        "The.Lord.of.the.Rings.The.Return.of.the.King.2003.2160p.BluRay.x265.CZ-WS",
    }
    uhd = next(i for i in by_tmdb if "2160p" in i["title"])
    assert uhd["attrs"]["category"] == ["2000", "2045"]
    assert uhd["attrs"]["imdb"] == ["0167260"]

    # Radarr strips the "tt" prefix from imdb ids
    by_imdb = items((await client.get("/newznab/api", params={**params, "imdbid": "0167260"})).content)
    assert {i["title"] for i in by_imdb} == titles


async def test_text_search_and_paging(client):
    params = {"t": "search", "apikey": KEY, "q": "Breaking Bad S01E02", "limit": 1, "offset": 0}
    first = items((await client.get("/newznab/api", params=params)).content)
    second = items((await client.get("/newznab/api", params={**params, "offset": 1})).content)
    assert len(first) == 1 and len(second) == 1
    assert first[0]["guid"] != second[0]["guid"]


async def test_rss_returns_results_for_indexer_test(client):
    resp = await client.get(
        "/newznab/api",
        params={"t": "tvsearch", "cat": "5030,5040", "extended": 1, "apikey": KEY, "offset": 0, "limit": 100},
    )
    found = items(resp.content)
    assert found, "Sonarr/Prowlarr indexer test needs results for an empty query"
    assert all(i["attrs"]["category"][0] == "5000" for i in found)


async def test_sab_status_endpoints(client, settings):
    version = await client.get("/sabnzbd/api", params={"mode": "version", "output": "json"})
    assert version.json()["version"].startswith("4.")
    config = (await client.get("/sabnzbd/api", params={"mode": "get_config", "apikey": KEY})).json()["config"]
    assert config["misc"]["complete_dir"] == str(settings.complete_dir)
    assert config["misc"]["history_retention_option"] == "all"
    tv = next(c for c in config["categories"] if c["name"] == "tv")
    assert tv["dir"] == "tv"
    full = (
        await client.get("/sabnzbd/api", params={"mode": "fullstatus", "skip_dashboard": 1, "apikey": KEY})
    ).json()
    assert full["status"]["completedir"] == str(settings.complete_dir)


async def test_combined_api_path(client):
    caps = await client.get("/api", params={"t": "caps"})
    assert b"<caps>" in caps.content
    version = await client.get("/api", params={"mode": "version"})
    assert "version" in version.json()


async def test_failed_download_reported(client, services, fake_ws):
    fake_ws.state.fail_links.add("a1")
    job = services.downloads.add(
        ident="a1", name="Breaking.Bad.S01E02.720p-WS", category="tv", size=3_000_000
    )
    services.downloads._retry_at.clear()

    async def failed():
        services.downloads._retry_at.clear()  # skip the retry back-off in tests
        r = await client.get("/sabnzbd/api", params={"mode": "history", "apikey": KEY})
        slots = [s for s in r.json()["history"]["slots"] if s["nzo_id"] == job.nzo_id]
        return slots if slots and slots[0]["status"] == "Failed" else None

    slot = (await wait_for(failed))[0]
    assert "File not found" in slot["fail_message"]


async def test_resume_uses_range(client, services, fake_ws, settings):
    job = services.downloads.add(
        ident="b3", name="Partial", category="movies", ws_name="x.avi", size=2_500_000
    )
    # Nothing to assert on timing; instead pre-seed a part file for a second job and pause the queue first.
    services.downloads.pause_all()
    job2 = services.downloads.add(
        ident="b1", name="Resumed", category="movies", ws_name="x.mkv", size=5_000_000
    )
    part_dir = settings.incomplete_dir / job2.nzo_id
    part_dir.mkdir(parents=True)
    (part_dir / "Resumed.mkv.part").write_bytes(file_bytes("b1", 5_000_000)[:1_000_000])
    services.downloads.resume_all()

    async def done():
        j = services.db.job_get(job2.nzo_id)
        return j if j and j.status == "Completed" else None

    await wait_for(done)
    final = settings.complete_dir / "movies" / "Resumed" / "Resumed.mkv"
    assert final.read_bytes() == file_bytes("b1", 5_000_000)
    assert ("download", "b1", "bytes=1000000-") in fake_ws.state.calls
    assert job.nzo_id != job2.nzo_id


async def test_force_priority_downloads_while_paused(client, services):
    services.downloads.pause_all()
    job = services.downloads.add(
        ident="b3", name="Forced", category="movies", ws_name="x.avi", size=2_500_000, priority=2
    )

    async def done():
        j = services.db.job_get(job.nzo_id)
        return j if j and j.status == "Completed" else None

    await wait_for(done)


async def test_sab_bad_numeric_params(client):
    resp = await client.get(
        "/sabnzbd/api", params={"mode": "queue", "start": "x", "limit": "", "apikey": KEY}
    )
    assert resp.status_code == 200 and "queue" in resp.json()
    resp = await client.get("/sabnzbd/api", params={"mode": "history", "limit": "abc", "apikey": KEY})
    assert resp.status_code == 200 and "history" in resp.json()
