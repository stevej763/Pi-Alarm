"""Plays on-device MP3 playlists, and routes each alarm to local playback or Spotify."""
from __future__ import annotations

import logging
import random
import shutil
import subprocess
import sys
import threading
from typing import Callable, List, Optional

from .library import Library, is_local
from .models import LocalTrack, Track

log = logging.getLogger(__name__)


class PlaybackError(Exception):
    pass


def mp3_player_command() -> Optional[List[str]]:
    if sys.platform == "darwin":
        return ["afplay"]
    if shutil.which("mpg123"):
        return ["mpg123", "-q"]
    if shutil.which("mpv"):
        return ["mpv", "--no-video", "--really-quiet"]
    if shutil.which("ffplay"):
        return ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"]
    return None


class LocalPlayer:
    """Plays a list of MP3s one after another through the default ALSA device, looping until paused."""

    def __init__(self, library: Library, command: Optional[List[str]] = None,
                 on_change: Callable[[], None] = lambda: None):
        self.library = library
        self.command = command if command is not None else mp3_player_command()
        self.on_change = on_change
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._proc: Optional[subprocess.Popen] = None
        self._current: Optional[LocalTrack] = None

    def start(self, uri: str, shuffle: bool = True) -> None:
        tracks = [t for t in self.library.tracks_for(uri) if self.library.path(t).exists()]
        if not tracks:
            raise PlaybackError("This playlist has no music on the device")
        if not self.command:
            raise PlaybackError("No MP3 player installed (sudo apt install mpg123)")
        self.pause()
        with self._lock:
            stop = self._stop = threading.Event()
        threading.Thread(target=self._loop, args=(tracks, shuffle, stop), name="local-player", daemon=True).start()

    def pause(self) -> None:
        with self._lock:
            self._stop.set()
            if self._proc and self._proc.poll() is None:
                self._proc.terminate()
            self._proc = None
            self._current = None

    def current_track(self) -> Optional[Track]:
        track = self._current
        if track is None:
            return None
        art = self.library.art_path(track)
        return Track(track.title, track.artist, track.album, art.as_uri() if art else None, True)

    def _loop(self, tracks: List[LocalTrack], shuffle: bool, stop: threading.Event) -> None:
        while not stop.is_set():
            order = random.sample(tracks, len(tracks)) if shuffle else list(tracks)
            failures = 0
            for track in order:
                with self._lock:
                    if stop.is_set():  # checked under the lock so pause() can't miss a new process
                        return
                    try:
                        proc = self._proc = subprocess.Popen(self.command + [str(self.library.path(track))],
                                                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    except OSError:
                        log.exception("Could not run %s", self.command)
                        failures += 1
                        continue
                    self._current = track
                self.on_change()
                if proc.wait() != 0 and not stop.is_set():
                    failures += 1
            if failures == len(order):
                log.error("Every track failed to play; retrying shortly")
                stop.wait(5)


class PlaybackRouter:
    """The alarm's player: local:… URIs play from the device, anything else goes to Spotify."""

    def __init__(self, local: LocalPlayer, spotify, spotify_enabled: Callable[[], bool]):
        self.local = local
        self.spotify = spotify
        self.spotify_enabled = spotify_enabled
        self._active = None

    def start(self, playlist_uri: str, shuffle: bool = True) -> None:
        if not playlist_uri:
            raise PlaybackError("Alarm has no playlist set")
        if is_local(playlist_uri):
            backend = self.local
        elif self.spotify_enabled():
            backend = self.spotify
        else:
            raise PlaybackError("Spotify is turned off; pick an on-device playlist for this alarm")
        self._active = backend
        backend.start(playlist_uri, shuffle)

    def pause(self) -> None:
        if self._active is not None:
            self._active.pause()

    def current_track(self) -> Optional[Track]:
        return self._active.current_track() if self._active is not None else None
