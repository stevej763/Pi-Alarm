"""Thin wrapper around spotipy for the handful of calls the alarm needs."""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable, List, Optional

import spotipy
from spotipy.cache_handler import CacheFileHandler
from spotipy.oauth2 import SpotifyOAuth

from .config import Settings
from .models import Track

log = logging.getLogger(__name__)

SCOPES = "user-modify-playback-state user-read-playback-state playlist-read-private"


class SpotifyError(Exception):
    pass


def _same_name(a: str, b: str) -> bool:
    """Case-insensitive, and treat curly and straight apostrophes alike (macOS uses "Steve’s Mac")."""
    def norm(x: str) -> str:
        return " ".join(x.replace("\u2019", "'").replace("\u2018", "'").casefold().split())
    return norm(a) == norm(b)


class SpotifyService:
    def __init__(self, settings: Settings, sleep: Callable[[float], None] = time.sleep):
        self.settings = settings
        self._sleep = sleep
        self._lock = threading.Lock()
        self._client: Optional[spotipy.Spotify] = None
        self._playlist_cache: tuple = (0.0, [])
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        self.oauth: Optional[SpotifyOAuth] = None
        self.reconfigure()

    # --- auth -----------------------------------------------------------------
    def reconfigure(self) -> None:
        """Rebuild the OAuth client from the current settings (after they're edited in the web UI)."""
        s = self.settings
        self.oauth = SpotifyOAuth(
            client_id=s.spotify_client_id,
            client_secret=s.spotify_client_secret or None,
            redirect_uri=s.spotify_redirect_uri,
            scope=SCOPES,
            cache_handler=CacheFileHandler(cache_path=str(s.token_cache)),
            open_browser=False,
        ) if s.spotify_client_id else None
        self._client = None
        self._playlist_cache = (0.0, [])

    def disconnect(self) -> None:
        """Forget the saved Spotify login."""
        self.settings.token_cache.unlink(missing_ok=True)
        self._client = None
        self._playlist_cache = (0.0, [])

    @property
    def configured(self) -> bool:
        return self.oauth is not None

    @property
    def authorized(self) -> bool:
        return bool(self.oauth and self.oauth.validate_token(self.oauth.cache_handler.get_cached_token()))

    def authorize_url(self) -> str:
        if not self.oauth:
            raise SpotifyError("Spotify client id/secret not configured")
        return self.oauth.get_authorize_url()

    def handle_callback(self, code: str) -> None:
        if not self.oauth:
            raise SpotifyError("Spotify client id/secret not configured")
        self.oauth.get_access_token(code, as_dict=False, check_cache=False)
        self._client = None

    @property
    def client(self) -> spotipy.Spotify:
        if not self.authorized:
            raise SpotifyError("Spotify is not connected; open the web UI and click 'Connect Spotify'")
        with self._lock:
            if self._client is None:
                self._client = spotipy.Spotify(auth_manager=self.oauth, requests_timeout=10, retries=2)
            return self._client

    # --- queries --------------------------------------------------------------
    def list_playlists(self, max_age: float = 300) -> List[dict]:
        fetched_at, cached = self._playlist_cache
        if cached and time.time() - fetched_at < max_age:
            return cached
        playlists = []
        page = self.client.current_user_playlists(limit=50)
        while page:
            playlists += [{"name": p["name"], "uri": p["uri"]} for p in page["items"] if p]
            page = self.client.next(page) if page.get("next") else None
        self._playlist_cache = (time.time(), playlists)
        return playlists

    def list_devices(self) -> List[dict]:
        return self.client.devices().get("devices", [])

    def find_device(self, name: str, wait_seconds: float = 0, interval: float = 3) -> str:
        """Return the device id for `name`, polling for up to `wait_seconds` (librespot may be idle)."""
        deadline = time.monotonic() + wait_seconds
        while True:
            devices = self.list_devices()
            for device in devices:
                if _same_name(device["name"], name):
                    return device["id"]
            if time.monotonic() >= deadline:
                seen = ", ".join(f"'{d['name']}'" for d in devices) or "none"
                raise SpotifyError(f"Spotify device '{name}' not found (available: {seen})")
            self._sleep(interval)

    def current_track(self) -> Optional[Track]:
        playback = self.client.current_playback()
        if not playback or not playback.get("item"):
            return None
        item = playback["item"]
        images = item.get("album", {}).get("images") or []
        return Track(
            title=item.get("name", ""),
            artist=", ".join(a["name"] for a in item.get("artists", [])),
            album=item.get("album", {}).get("name", ""),
            art_url=images[0]["url"] if images else None,
            is_playing=playback.get("is_playing", False),
        )

    # --- playback -------------------------------------------------------------
    def start(self, playlist_uri: str, shuffle: bool = True) -> None:
        """Start `playlist_uri` on the alarm device, skipping to a random track if shuffling."""
        if not playlist_uri:
            raise SpotifyError("Alarm has no playlist set")
        try:
            device_id = self.find_device(self.settings.spotify_device_name, self.settings.device_wait_seconds)
            sp = self.client
            sp.start_playback(device_id=device_id, context_uri=playlist_uri)
            sp.shuffle(shuffle, device_id=device_id)
            if shuffle:
                # Playback always begins at track 1; with shuffle now on, "next" lands somewhere random.
                sp.next_track(device_id=device_id)
        except spotipy.SpotifyException as e:
            raise SpotifyError(f"Spotify error: {e.msg or e}") from e
        except Exception as e:
            if isinstance(e, SpotifyError):
                raise
            raise SpotifyError(f"Could not reach Spotify: {e}") from e

    def pause(self) -> None:
        try:
            device_id = self.find_device(self.settings.spotify_device_name)
            self.client.pause_playback(device_id=device_id)
        except Exception as e:
            log.warning("Pause failed: %s", e)
