async def test_dashboard(client):
    resp = await client.get("/ui/")
    assert resp.status_code == 200
    assert "Přihlášen" in resp.text
    assert "42" in resp.text  # VIP days from fake Webshare
    assert "sonarr" in resp.text


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
    resp = await client.post(f"/ui/jobs/{job.nzo_id}/delete")
    assert resp.status_code == 200
    assert services.db.job_get(job.nzo_id) is None


async def test_settings_setup_and_tests(client, services):
    services.prowlarr = None
    page = await client.get("/ui/settings")
    assert "Auto-setup" in page.text
    report = await client.post("/ui/setup")
    assert "created" in report.text
    tests = await client.post("/ui/settings/test")
    assert "OK" in tests.text


async def test_basic_auth(client, services):
    services.settings.ui_username = "admin"
    services.settings.ui_password = "secret"
    assert (await client.get("/ui/")).status_code == 401
    assert (await client.get("/ui/", auth=("admin", "secret"))).status_code == 200
    # The *arr facing APIs use the API key, not basic auth.
    assert (await client.get("/newznab/api", params={"t": "caps"})).status_code == 200
