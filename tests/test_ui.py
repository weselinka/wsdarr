HX = {"HX-Request": "true"}


async def test_dashboard(client):
    resp = await client.get("/ui/")
    assert resp.status_code == 200
    assert "Přihlášen" in resp.text
    assert "42" in resp.text  # VIP days from fake Webshare
    assert "sonarr" in resp.text
    # Setup guide for Sonarr/Radarr derived from PUBLIC_URL
    assert "<code>wsdarr</code> / <code>9797</code>" in resp.text
    assert "http://wsdarr:9797/newznab" in resp.text
    assert "<code>sabnzbd</code>" in resp.text
    assert "testkey" in resp.text


async def test_root_redirects_to_ui(client):
    resp = await client.get("/")
    assert resp.status_code == 307
    assert resp.headers["location"] == "/ui/"


async def test_search_and_manual_download(client, services):
    resp = await client.get("/ui/search", params={"q": "Breaking Bad S01E02"})
    assert resp.status_code == 200
    assert "Breaking.Bad.S01E02.1080p.BluRay.x264.CZ-WS" in resp.text

    raw = await client.get("/ui/search", params={"q": "breaking bad", "raw": 1})
    assert "Breaking.Bad.S01E03.720p.CZ.mkv" in raw.text

    resp = await client.post(
        "/ui/download",
        headers=HX,
        data={
            "ident": "a3",
            "title": "Breaking.Bad.S01E03.720p.CZ-WS",
            "ws_name": "x.mkv",
            "size": 3_000_000,
            "category": "tv",
        },
    )
    assert "Přidáno" in resp.text
    assert any(j.ident == "a3" for j in [*services.db.jobs_queue(), *services.db.jobs_history()])


async def test_queue_page_and_actions(client, services):
    services.downloads.pause_all()
    job = services.downloads.add(ident="b3", name="Some.Movie.2001-WS", category="movies", size=2_500_000)
    page = await client.get("/ui/queue")
    assert "Some.Movie.2001-WS" in page.text
    resp = await client.post(f"/ui/jobs/{job.nzo_id}/delete", headers=HX)
    assert resp.status_code == 200
    assert services.db.job_get(job.nzo_id) is None


async def test_settings_and_connection_test(client, services):
    page = await client.get("/ui/settings")
    assert "Test spojení" in page.text
    tests = await client.post("/ui/settings/test", headers=HX)
    assert "OK" in tests.text


async def test_basic_auth(client, services):
    services.settings.ui_username = "admin"
    services.settings.ui_password = "secret"
    assert (await client.get("/ui/")).status_code == 401
    assert (await client.get("/ui/", auth=("admin", "secret"))).status_code == 200
    # The *arr facing APIs use the API key, not basic auth.
    assert (await client.get("/newznab/api", params={"t": "caps"})).status_code == 200


async def test_post_without_htmx_header_is_rejected(client, services):
    resp = await client.post("/ui/settings/test")
    assert resp.status_code == 403
    resp = await client.post("/ui/queue/pause")
    assert resp.status_code == 403
    assert not services.downloads.paused


async def test_manual_download_escapes_category(client):
    resp = await client.post(
        "/ui/download",
        headers=HX,
        data={
            "ident": "a3",
            "title": "X-WS",
            "ws_name": "x.mkv",
            "size": 1,
            "category": "<script>x</script>",
        },
    )
    assert "<script>" not in resp.text
    assert "&lt;script&gt;" in resp.text


async def test_basic_auth_non_ascii_password(client, services):
    services.settings.ui_username = "admin"
    services.settings.ui_password = "heslo-ěšč"
    assert (await client.get("/ui/", auth=("admin", "spatne-ěšč"))).status_code == 401
    assert (await client.get("/ui/", auth=("admin", "heslo-ěšč"))).status_code == 200


async def test_dashboard_survives_invalid_public_url(client, services):
    services.settings.public_url = "http://wsdarr:abc"
    resp = await client.get("/ui/")
    assert resp.status_code == 200
    assert "<code>wsdarr</code> / <code>80</code>" in resp.text
