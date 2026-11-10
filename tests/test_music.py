import io
import time

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.library import LOCAL_ALL, Library, LibraryError, playlist_uri
from app.main import create_app
from app.playback import LocalPlayer, PlaybackError, PlaybackRouter
from app.services import Services

from .fakes import FakeFallback, FakeSpotify, FakeVolume


def mp3_bytes(title=None, artist=None, art=None, frames=40) -> bytes:
    """A silent MPEG-1 Layer III file (128 kbps, 44.1 kHz), optionally with ID3 tags."""
    frame = b"\xff\xfb\x90\x00" + b"\x00" * 413
    data = frame * frames
    if not (title or artist or art):
        return data
    from mutagen.id3 import APIC, ID3, TIT2, TPE1

    tags = ID3()
    if title:
        tags.add(TIT2(encoding=3, text=title))
    if artist:
        tags.add(TPE1(encoding=3, text=artist))
    if art:
        tags.add(APIC(encoding=3, mime="image/png", type=3, desc="cover", data=art))
    buf = io.BytesIO(data)
    tags.save(buf)
    return buf.getvalue()


@pytest.fixture
def library(tmp_path):
    from app.db import Database
    return Library(Database(tmp_path / "db.sqlite"), tmp_path / "music")


def test_upload_reads_tags_and_art(library):
    track = library.add("Morning Song.mp3", io.BytesIO(mp3_bytes("Sunrise", "The Band", art=b"\x89PNG fake")))
    assert (track.title, track.artist) == ("Sunrise", "The Band")
    assert track.duration > 0
    assert library.path(track).exists()
    assert library.art_path(track).read_bytes() == b"\x89PNG fake"


def test_upload_without_tags_uses_filename(library):
    track = library.add("../../weird name!.mp3", io.BytesIO(mp3_bytes()))
    assert track.title == "weird name!"
    assert library.path(track).parent == library.dir


def test_upload_rejects_non_mp3(library):
    with pytest.raises(LibraryError):
        library.add("notes.txt", io.BytesIO(b"hello"))
    with pytest.raises(LibraryError):
        library.add("fake.mp3", io.BytesIO(b"not really audio" * 10))
    assert list(library.dir.glob("*.mp3")) == []


def test_playlist_order_move_remove_and_delete(library):
    a, b, c = (library.add(f"{n}.mp3", io.BytesIO(mp3_bytes(n))) for n in "abc")
    p = library.create_playlist("Wake")
    library.add_to_playlist(p.id, [a.id, b.id, c.id, a.id])  # duplicate ignored
    assert [t.title for t in library.tracks_for(playlist_uri(p.id))] == ["a", "b", "c"]

    entries = library.entries(p.id)
    library.move_entry(entries[2][0].id, -1)
    assert [t.title for t in library.tracks_for(playlist_uri(p.id))] == ["a", "c", "b"]
    library.move_entry(entries[0][0].id, -1)  # already first: no-op
    library.remove_entry(entries[1][0].id)  # b
    assert [t.title for t in library.tracks_for(playlist_uri(p.id))] == ["a", "c"]

    library.delete_track(a.id)
    assert [t.title for t in library.tracks_for(playlist_uri(p.id))] == ["c"]
    assert not library.path(a).exists()
    assert [t.title for t in library.tracks_for(LOCAL_ALL)] == ["b", "c"]

    library.delete_playlist(p.id)
    assert library.playlists() == [] and library.track_counts() == {}


def test_local_player_plays_through_and_pauses(library, tmp_path):
    a = library.add("a.mp3", io.BytesIO(mp3_bytes("A")))
    library.add("b.mp3", io.BytesIO(mp3_bytes("B")))
    log = tmp_path / "played.txt"
    # Stand-in player: logs each file then "plays" for a moment.
    cmd = ["sh", "-c", f'echo "$0" >> {log}; sleep 0.05']
    player = LocalPlayer(library, command=cmd)
    player.start(LOCAL_ALL, shuffle=False)
    time.sleep(0.4)
    assert player.current_track() is not None
    player.pause()
    assert player.current_track() is None
    played = log.read_text().split()
    assert played[:2] == [str(library.path(a)), str(library.path(library.tracks()[1]))]
    count = len(played)
    time.sleep(0.2)
    assert len(log.read_text().split()) == count  # nothing plays after pause


def test_local_player_errors(library):
    player = LocalPlayer(library, command=["true"])
    with pytest.raises(PlaybackError):
        player.start(LOCAL_ALL)  # empty library
    library.add("a.mp3", io.BytesIO(mp3_bytes()))
    with pytest.raises(PlaybackError):
        LocalPlayer(library, command=[]).start(LOCAL_ALL)


class FakeLocal:
    def __init__(self):
        self.started = []

    def start(self, uri, shuffle=True):
        self.started.append(uri)

    def pause(self):
        pass

    def current_track(self):
        return None


def test_router_picks_backend_and_respects_spotify_toggle():
    local, spotify = FakeLocal(), FakeSpotify()
    enabled = [True]
    router = PlaybackRouter(local, spotify, lambda: enabled[0])
    router.start("local:playlist:1")
    router.start("spotify:playlist:abc")
    assert local.started == ["local:playlist:1"]
    assert spotify.started == [("spotify:playlist:abc", True)]
    enabled[0] = False
    with pytest.raises(PlaybackError):
        router.start("spotify:playlist:abc")
    with pytest.raises(PlaybackError):
        router.start("")


@pytest.fixture
def client(tmp_path):
    settings = Settings(data_dir=tmp_path)
    svc = Services(settings, spotify=FakeSpotify(), volume=FakeVolume(), fallback=FakeFallback(),
                   use_hardware=False, local_player=FakeLocal())
    with TestClient(create_app(svc)) as c:
        c.svc = svc
        yield c


def test_web_upload_playlist_and_alarm(client):
    r = client.post("/music/upload", files=[("files", ("one.mp3", mp3_bytes("One"), "audio/mpeg")),
                                            ("files", ("bad.txt", b"x", "text/plain"))],
                    follow_redirects=False)
    assert r.status_code == 303 and "Added+1+track" in r.headers["location"] and "bad.txt" in r.headers["location"]
    [track] = client.svc.library.tracks()
    assert "One" in client.get("/music").text
    assert client.get(f"/music/tracks/{track.id}/audio").status_code == 200

    r = client.post("/music/playlists", data={"name": "Gentle"}, follow_redirects=False)
    pid = int(r.headers["location"].rsplit("/", 1)[1])
    client.post(f"/music/playlists/{pid}/add", data={"track_ids": [str(track.id)]})
    page = client.get(f"/music/playlists/{pid}").text
    assert "Gentle" in page and "One" in page

    assert "On this device" in client.get("/alarms/new").text
    client.post("/alarms", data={"label": "Local", "time": "07:00", "days": ["mon"],
                                 "playlist_uri": f"local:playlist:{pid}", "fade_minutes": "0"})
    [alarm] = client.svc.db.alarms()
    assert alarm.playlist_name == "Gentle"

    client.post(f"/music/playlists/{pid}/rename", data={"name": "Calm"})
    assert client.svc.db.alarms()[0].playlist_name == "Calm"

    client.post(f"/alarms/{alarm.id}/test")
    time.sleep(0.1)
    assert client.svc.local_player.started == [f"local:playlist:{pid}"]


def test_settings_toggle_spotify(client):
    assert client.svc.spotify_enabled
    client.post("/settings/spotify", data={"client_id": "abc", "device_name": "Bedroom"})
    assert not client.svc.spotify_enabled
    assert client.svc.settings.spotify_device_name == "Bedroom"
    assert client.svc.db.get_setting("spotify_client_id") == "abc"
    assert not client.svc.spotify.authorized  # new app id: the old login was dropped
    page = client.get("/alarms/new").text
    assert "Spotify" not in page.split("<select")[1].split("</select>")[0]

    client.post("/settings/spotify", data={"enabled": "true"})
    assert client.svc.spotify_enabled
    assert client.svc.settings.spotify_client_id == "abc"  # blank fields keep saved values
    client.svc.spotify.authorized = True
    assert "Morning Mix" in client.get("/alarms/new").text
