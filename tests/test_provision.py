from wsdarr.provision.setup import endpoint_parts, get_field, run_setup


def test_endpoint_parts():
    assert endpoint_parts("http://wsdarr:9797") == {
        "host": "wsdarr",
        "port": 9797,
        "ssl": False,
        "prefix": "",
    }
    assert endpoint_parts("https://example.com/wsdarr/") == {
        "host": "example.com",
        "port": 443,
        "ssl": True,
        "prefix": "wsdarr",
    }


async def test_setup_with_prowlarr(services, fake_sonarr, fake_radarr, fake_prowlarr):
    # Simulate indexers Prowlarr already synced (Prowlarr names them "<name> (Prowlarr)").
    for fake, cat in ((fake_sonarr, 5000), (fake_radarr, 2000)):
        fake.state.data["indexer"].append(
            {
                "id": 50,
                "name": "Webshare (wsdarr) (Prowlarr)",
                "implementation": "Newznab",
                "downloadClientId": 0,
                "fields": [
                    {"name": "baseUrl", "value": "http://prowlarr:9696/2/"},
                    {"name": "categories", "value": [cat]},
                ],
            }
        )

    report = await run_setup(services, sync_timeout=1, sync_interval=0.01)
    assert report.ok, report.lines()

    sonarr_client = fake_sonarr.state.data["downloadclient"][0]
    assert sonarr_client["name"] == "wsdarr"
    assert get_field(sonarr_client, "host") == "wsdarr"
    assert get_field(sonarr_client, "port") == 9797
    assert get_field(sonarr_client, "urlBase") == "sabnzbd"
    assert get_field(sonarr_client, "apiKey") == "testkey"
    assert get_field(sonarr_client, "tvCategory") == "tv"
    radarr_client = fake_radarr.state.data["downloadclient"][0]
    assert get_field(radarr_client, "movieCategory") == "movies"

    prowlarr_indexer = fake_prowlarr.state.data["indexer"][0]
    assert prowlarr_indexer["definitionName"] == "Generic Newznab"
    assert get_field(prowlarr_indexer, "baseUrl") == "http://wsdarr:9797/newznab"
    assert get_field(prowlarr_indexer, "apiKey") == "testkey"
    assert prowlarr_indexer["appProfileId"] == 1
    assert {"name": "ApplicationIndexerSync"} in fake_prowlarr.state.data["commands"]

    assert fake_sonarr.state.data["indexer"][0]["downloadClientId"] == sonarr_client["id"]
    assert fake_radarr.state.data["indexer"][0]["downloadClientId"] == radarr_client["id"]

    # Second run is a no-op.
    again = await run_setup(services, sync_timeout=1, sync_interval=0.01)
    assert {a for _, a, _ in again.entries} <= {"ok"}
    assert len(fake_sonarr.state.data["downloadclient"]) == 1
    assert len(fake_prowlarr.state.data["indexer"]) == 1


async def test_setup_without_prowlarr(services, fake_sonarr, fake_radarr):
    services.prowlarr = None
    report = await run_setup(services)
    assert report.ok, report.lines()
    sonarr_indexer = fake_sonarr.state.data["indexer"][0]
    assert sonarr_indexer["name"] == "Webshare (wsdarr)"
    assert get_field(sonarr_indexer, "baseUrl") == "http://wsdarr:9797/newznab"
    assert get_field(sonarr_indexer, "categories") == [5000, 5030, 5040, 5045]
    assert sonarr_indexer["downloadClientId"] == fake_sonarr.state.data["downloadclient"][0]["id"]
    radarr_indexer = fake_radarr.state.data["indexer"][0]
    assert get_field(radarr_indexer, "categories") == [2000, 2030, 2040, 2045]


async def test_setup_reports_missing_sync(services, fake_sonarr):
    fake_sonarr.state.data["indexer"].clear()
    report = await run_setup(services, sync_timeout=0.05, sync_interval=0.01)
    assert any(a == "warning" and "not synced" in d for _, a, d in report.entries)
