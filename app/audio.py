"""Volume control and the local fallback alarm sound."""
from __future__ import annotations

import logging
import math
import re
import shutil
import struct
import subprocess
import sys
import threading
import wave
from pathlib import Path
from typing import List, Optional

log = logging.getLogger(__name__)


def clamp(value: int) -> int:
    return max(0, min(100, int(value)))


class AlsaVolume:
    """Controls an ALSA mixer control (the softvol "Master" control on the Pirate Audio)."""

    def __init__(self, control: str = "Master", card: Optional[str] = None):
        self.control = control
        self.card = card

    def _amixer(self, *args: str) -> str:
        cmd = ["amixer"]
        if self.card:
            cmd += ["-c", self.card]
        cmd += list(args)
        return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout

    def set_volume(self, pct: int) -> None:
        self._amixer("-q", "sset", self.control, f"{clamp(pct)}%")

    def get_volume(self) -> int:
        match = re.search(r"\[(\d+)%\]", self._amixer("sget", self.control))
        return int(match.group(1)) if match else 0


class MemoryVolume:
    """Development stand-in: remembers the volume without touching hardware."""

    def __init__(self, initial: int = 50):
        self._volume = initial

    def set_volume(self, pct: int) -> None:
        self._volume = clamp(pct)
        log.debug("volume -> %d%%", self._volume)

    def get_volume(self) -> int:
        return self._volume


def make_volume(backend: str, control: str, card: Optional[str]):
    if backend == "alsa":
        return AlsaVolume(control, card)
    return MemoryVolume()


def ensure_fallback_wav(path: Path) -> Path:
    """Generate a gentle two-tone chime so the alarm can sound with no network."""
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    rate = 44100
    frames = bytearray()

    def tone(freq: float, seconds: float) -> None:
        n = int(rate * seconds)
        for i in range(n):
            envelope = math.exp(-3.0 * i / n)
            sample = int(12000 * envelope * math.sin(2 * math.pi * freq * i / rate))
            frames.extend(struct.pack("<hh", sample, sample))

    tone(880.0, 0.35)
    tone(659.25, 0.5)
    frames.extend(b"\x00\x00\x00\x00" * int(rate * 1.2))  # pause between chimes

    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))
    return path


def default_player_command() -> List[str]:
    if sys.platform == "darwin":
        return ["afplay"]
    if shutil.which("aplay"):
        return ["aplay", "-q"]
    return ["mpv", "--no-video", "--really-quiet"]


class FallbackPlayer:
    """Loops a local sound file until stopped."""

    def __init__(self, path: Path, command: Optional[List[str]] = None):
        self.path = path
        self.command = command or default_player_command()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._proc: Optional[subprocess.Popen] = None

    @property
    def active(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.active:
            return
        ensure_fallback_wav(self.path)
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="fallback-player", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._proc = subprocess.Popen(self.command + [str(self.path)])
                self._proc.wait()
            except OSError:
                log.exception("Fallback player failed to run %s", self.command)
                self._stop.wait(5)

    def stop(self) -> None:
        self._stop.set()
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
        self._thread = None
