import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import _normalise_spotify_uri, create_app
from app.services import Services

from .fakes import FakeFallback, FakeSpotify, FakeVolume


@pytest.fixture
def client(tmp_path):
    settings = Settings(data_dir=tmp_path)
    svc = Services(settings, spotify=FakeSpotify(), volume=FakeVolume(), fallback=FakeFallback(), use_hardware=False)
    with TestClient(create_app(svc)) as c:
        c.svc = svc
        yield c


def test_create_edit_toggle_delete(client):
    r = client.post("/alarms", data={"label": "Weekdays", "time": "06:45", "days": ["mon", "fri"],
                                     "playlist_uri": "spotify:playlist:abc", "shuffle": "true",
                                     "fade_minutes": "3", "start_volume": "5", "target_volume": "65"},
                    follow_redirects=False)
    assert r.status_code == 303
    [alarm] = client.svc.db.alarms()
    assert (alarm.hour, alarm.minute, alarm.days, alarm.playlist_name) == (6, 45, "mon,fri", "Morning Mix")
    assert [j.id for j in client.svc.scheduler.alarm_jobs()] == [f"alarm-{alarm.id}"]

    page = client.get("/").text
    assert "06:45" in page and "Morning Mix" in page
    assert client.get(f"/alarms/{alarm.id}/edit").status_code == 200

    client.post(f"/alarms/{alarm.id}/toggle")
    assert client.svc.scheduler.alarm_jobs() == []

    client.post(f"/alarms/{alarm.id}/delete")
    assert client.svc.db.alarms() == []


def test_snooze_stop_and_status(client):
    from app.models import AlarmSpec
    client.svc.runner.ring(AlarmSpec(1, "Wake", "spotify:playlist:abc", fade_minutes=0))
    assert "Ringing" in client.get("/status").text
    client.post("/snooze")
    assert "Snoozing" in client.get("/status").text
    client.post("/stop")
    assert "Ringing" not in client.get("/status").text


def test_pause_all(client):
    client.post("/pause-all")
    assert client.svc.db.alarms_paused
    assert "paused" in client.get("/status").text


def test_display_png_served(client):
    client.svc.display.tick()
    r = client.get("/display.png")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"


def test_normalise_spotify_links():
    assert _normalise_spotify_uri("https://open.spotify.com/playlist/37i9dQ?si=xyz") == "spotify:playlist:37i9dQ"
    assert _normalise_spotify_uri("https://open.spotify.com/intl-de/album/abc") == "spotify:album:abc"
    assert _normalise_spotify_uri("spotify:playlist:abc") == "spotify:playlist:abc"


def test_callback_error_shows_help_page(client):
    r = client.get("/callback?error=server_error")
    assert r.status_code == 400
    assert "server_error" in r.text and "Try again" in r.text
