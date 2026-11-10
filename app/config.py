"""Settings loaded from environment variables (and an optional .env file)."""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent


def load_dotenv(path: Path) -> None:
    """Minimal .env loader: KEY=VALUE lines, existing env vars win."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _parse_hhmm(value: str) -> time:
    hour, minute = value.split(":")
    return time(int(hour), int(minute))


@dataclass
class Settings:
    spotify_client_id: str = ""
    spotify_client_secret: str = ""
    spotify_redirect_uri: str = "http://127.0.0.1:8550/callback"
    spotify_device_name: str = "Radio Alarm"

    data_dir: Path = ROOT / "data"
    hardware: str = "auto"  # pi | none | auto
    audio_backend: str = "alsa" if sys.platform.startswith("linux") else "none"
    alsa_control: str = "Master"
    alsa_card: Optional[str] = None

    snooze_minutes: int = 9
    normal_volume: int = 60
    device_wait_seconds: int = 30

    button_pins: dict = field(default_factory=lambda: {"a": 5, "b": 6, "x": 16, "y": 24})
    backlight_pin: int = 13
    night_start: time = time(22, 0)
    night_end: time = time(6, 30)
    night_brightness: int = 8

    timezone: Optional[str] = None

    @property
    def db_path(self) -> Path:
        return self.data_dir / "radio_alarm.db"

    @property
    def token_cache(self) -> Path:
        return self.data_dir / "spotify-token.json"

    @property
    def music_dir(self) -> Path:
        return self.data_dir / "music"

    @property
    def fallback_file(self) -> Path:
        return self.data_dir / "fallback-alarm.wav"

    @property
    def display_preview(self) -> Path:
        return self.data_dir / "display.png"

    @property
    def use_pi_hardware(self) -> bool:
        if self.hardware == "auto":
            return sys.platform.startswith("linux") and Path("/dev/spidev0.1").exists()
        return self.hardware == "pi"

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv(ROOT / ".env")
        env = os.environ.get
        s = cls()
        s.spotify_client_id = env("SPOTIFY_CLIENT_ID", s.spotify_client_id)
        s.spotify_client_secret = env("SPOTIFY_CLIENT_SECRET", s.spotify_client_secret)
        s.spotify_redirect_uri = env("SPOTIFY_REDIRECT_URI", s.spotify_redirect_uri)
        s.spotify_device_name = env("SPOTIFY_DEVICE_NAME", s.spotify_device_name)
        if env("DATA_DIR"):
            s.data_dir = Path(env("DATA_DIR"))
        s.hardware = env("HARDWARE", s.hardware).lower()
        s.audio_backend = env("AUDIO_BACKEND", s.audio_backend).lower()
        s.alsa_control = env("ALSA_CONTROL", s.alsa_control)
        s.alsa_card = env("ALSA_CARD", s.alsa_card)
        s.snooze_minutes = int(env("SNOOZE_MINUTES", s.snooze_minutes))
        s.normal_volume = int(env("NORMAL_VOLUME", s.normal_volume))
        s.device_wait_seconds = int(env("DEVICE_WAIT_SECONDS", s.device_wait_seconds))
        s.button_pins["y"] = int(env("BUTTON_Y_PIN", s.button_pins["y"]))
        if env("NIGHT_START"):
            s.night_start = _parse_hhmm(env("NIGHT_START"))
        if env("NIGHT_END"):
            s.night_end = _parse_hhmm(env("NIGHT_END"))
        s.night_brightness = int(env("NIGHT_BRIGHTNESS", s.night_brightness))
        s.timezone = env("TIMEZONE", s.timezone)
        return s
