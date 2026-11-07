"""Polls Spotify for the current track (only while the alarm is ringing) and caches album art."""
from __future__ import annotations

import io
import logging
import threading
import urllib.request
from typing import Callable, Optional

from PIL import Image

from .spotify import SpotifyService, Track

log = logging.getLogger(__name__)


class NowPlaying:
    def __init__(self, spotify: SpotifyService, should_poll: Callable[[], bool], interval: float = 5.0,
                 on_change: Callable[[], None] = lambda: None):
        self.spotify = spotify
        self.should_poll = should_poll
        self.interval = interval
        self.on_change = on_change
        self.track: Optional[Track] = None
        self.art: Optional[Image.Image] = None
        self._art_url: Optional[str] = None
        self._stop = threading.Event()
        self._kick = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._run, name="now-playing", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        self._kick.set()

    def poke(self) -> None:
        """Poll soon (e.g. right after the alarm starts)."""
        self._kick.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            if self.should_poll():
                self._poll()
            elif self.track is not None:
                self.track, self.art, self._art_url = None, None, None
                self.on_change()
            self._kick.wait(self.interval)
            self._kick.clear()

    def _poll(self) -> None:
        try:
            track = self.spotify.current_track()
        except Exception as e:
            log.debug("current_track failed: %s", e)
            return
        changed = track != self.track
        self.track = track
        url = track.art_url if track else None
        if url != self._art_url:
            self._art_url = url
            self.art = self._fetch_art(url) if url else None
            changed = True
        if changed:
            self.on_change()

    @staticmethod
    def _fetch_art(url: str) -> Optional[Image.Image]:
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                img = Image.open(io.BytesIO(resp.read()))
                img.load()
                return img.convert("RGB")
        except Exception as e:
            log.debug("Album art fetch failed: %s", e)
            return None
