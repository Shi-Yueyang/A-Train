"""Phase 2.5 smoke tests — static demo page is served and the API still works.

The full Phase 2.5 acceptance is a manual human check; these tests only verify
that the FastAPI application serves the static browser client at the site root
and that the REST API remains reachable alongside the static mount.
"""

from __future__ import annotations


async def test_root_serves_demo_page(app_client) -> None:
    r = await app_client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "A-Train Simulator" in r.text
    assert "app.js" in r.text


async def test_static_assets_are_served(app_client) -> None:
    js = await app_client.get("/app.js")
    assert js.status_code == 200
    assert "fetch" in js.text

    css = await app_client.get("/style.css")
    assert css.status_code == 200
    assert "color-scheme" in css.text


async def test_api_remains_reachable_alongside_static_mount(app_client) -> None:
    r = await app_client.get("/api/status")
    assert r.status_code == 200
    body = r.json()
    assert body["simulation_state"] == "STOPPED"
    assert body["simulation_time"] == 0.0

    trains = await app_client.get("/api/trains")
    assert trains.status_code == 200
    assert isinstance(trains.json()["trains"], list)


async def test_unknown_static_path_returns_404(app_client) -> None:
    r = await app_client.get("/does-not-exist.js")
    assert r.status_code == 404
