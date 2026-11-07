from __future__ import annotations

import threading
from datetime import datetime
from typing import Callable, List, Optional


class FakeSpotify:
    configured = True
    authorized = True

    def __init__(self, fail: bool = False):
        self.fail = fail
        self.started: List[tuple] = []
        self.paused = threading.Event()

    def start(self, playlist_uri: str, shuffle: bool = True) -> None:
        if self.fail:
            raise RuntimeError("network down")
        self.started.append((playlist_uri, shuffle))

    def pause(self) -> None:
        self.paused.set()

    def list_playlists(self):
        return [{"name": "Morning Mix", "uri": "spotify:playlist:abc"}]

    def current_track(self):
        return None


class FakeVolume:
    def __init__(self, initial: int = 50):
        self.value = initial
        self.history: List[int] = []

    def set_volume(self, pct: int) -> None:
        self.value = pct
        self.history.append(pct)

    def get_volume(self) -> int:
        return self.value


class FakeFallback:
    def __init__(self):
        self.playing = False

    def start(self) -> None:
        self.playing = True

    def stop(self) -> None:
        self.playing = False


class FakeTimer:
    def __init__(self):
        self.run_at: Optional[datetime] = None
        self.callback: Optional[Callable[[], None]] = None

    def schedule_snooze(self, run_at, callback) -> None:
        self.run_at, self.callback = run_at, callback

    def cancel_snooze(self) -> None:
        self.run_at, self.callback = None, None

    def fire(self) -> None:
        cb = self.callback
        self.cancel_snooze()
        cb()
