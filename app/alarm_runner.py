"""The alarm state machine: IDLE -> RINGING -> SNOOZED -> RINGING ... -> IDLE."""
from __future__ import annotations

import enum
import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, List, Optional, Protocol

from .audio import clamp
from .models import AlarmSpec

log = logging.getLogger(__name__)

FADE_INTERVAL = 3.0  # seconds between volume steps
VOLUME_STEP = 5


class State(str, enum.Enum):
    IDLE = "idle"
    RINGING = "ringing"
    SNOOZED = "snoozed"


class Player(Protocol):
    def start(self, playlist_uri: str, shuffle: bool = True) -> None: ...
    def pause(self) -> None: ...


class Volume(Protocol):
    def set_volume(self, pct: int) -> None: ...
    def get_volume(self) -> int: ...


class Fallback(Protocol):
    def start(self) -> None: ...
    def stop(self) -> None: ...


class SnoozeTimer(Protocol):
    def schedule_snooze(self, run_at: datetime, callback: Callable[[], None]) -> None: ...
    def cancel_snooze(self) -> None: ...


def fade_steps(start: int, target: int, duration_s: float, interval: float = FADE_INTERVAL) -> List[int]:
    """Volumes to apply, one per `interval`, ending exactly at `target`."""
    n = max(1, int(duration_s // interval))
    return [round(start + (target - start) * (i + 1) / n) for i in range(n)]


@dataclass
class Status:
    state: State
    alarm: Optional[AlarmSpec]
    snooze_until: Optional[datetime]
    error: Optional[str]
    fallback_active: bool
    volume: int
    volume_changed_at: float


class AlarmRunner:
    def __init__(
        self,
        player: Player,
        volume: Volume,
        fallback: Fallback,
        timer: SnoozeTimer,
        snooze_minutes: int = 9,
        normal_volume: int = 60,
        now: Callable[[], datetime] = datetime.now,
        fade_interval: float = FADE_INTERVAL,
    ):
        self.player = player
        self.volume = volume
        self.fallback = fallback
        self.timer = timer
        self.snooze_minutes = snooze_minutes
        self.normal_volume = normal_volume
        self._now = now
        self._fade_interval = fade_interval

        self._lock = threading.RLock()
        self.state = State.IDLE
        self.alarm: Optional[AlarmSpec] = None
        self.snooze_until: Optional[datetime] = None
        self.error: Optional[str] = None
        self.fallback_active = False
        self._fade_cancel = threading.Event()
        self._fade_thread: Optional[threading.Thread] = None
        self._volume_changed_at = 0.0
        self.listeners: List[Callable[[], None]] = []

    # --- public actions -------------------------------------------------------
    def ring(self, alarm: AlarmSpec) -> None:
        with self._lock:
            if self.state == State.RINGING:
                log.info("Already ringing; ignoring alarm %s", alarm.label)
                return
            log.info("Ringing alarm %r", alarm.label)
            self.timer.cancel_snooze()
            self._cancel_fade()
            self.state = State.RINGING
            self.alarm = alarm
            self.snooze_until = None
            self.error = None
            self._set_volume(alarm.start_volume)
        self._notify()

        # Network calls happen outside the lock so Snooze/Stop stay responsive.
        try:
            self.player.start(alarm.playlist_uri, alarm.shuffle)
        except Exception as e:
            log.error("Spotify failed, using fallback sound: %s", e)
            with self._lock:
                if self.state != State.RINGING or self.alarm is not alarm:
                    return  # stopped/snoozed while we were waiting on Spotify
                self.error = str(e)
                self.fallback_active = True
                self.fallback.start()

        with self._lock:
            if self.state == State.RINGING and self.alarm is alarm:
                self._start_fade(alarm.start_volume, alarm.target_volume, alarm.fade_minutes * 60)
        self._notify()

    def snooze(self) -> None:
        with self._lock:
            if self.state != State.RINGING or self.alarm is None:
                return
            self._silence()
            self.state = State.SNOOZED
            self.snooze_until = self._now() + timedelta(minutes=self.snooze_minutes)
            alarm = self.alarm
            self.timer.schedule_snooze(self.snooze_until, lambda: self._snooze_elapsed(alarm))
            log.info("Snoozed until %s", self.snooze_until)
        self._notify()

    def stop(self) -> None:
        with self._lock:
            if self.state == State.IDLE:
                return
            self._silence()
            self.timer.cancel_snooze()
            self.state = State.IDLE
            self.alarm = None
            self.snooze_until = None
            self._set_volume(self.normal_volume)
            log.info("Alarm stopped")
        self._notify()

    def nudge_volume(self, delta: int) -> int:
        with self._lock:
            self._cancel_fade()  # the user has taken control of the volume
            new = clamp(self.volume.get_volume() + delta)
            self._set_volume(new)
        self._notify()
        return new

    def volume_up(self) -> int:
        return self.nudge_volume(VOLUME_STEP)

    def volume_down(self) -> int:
        return self.nudge_volume(-VOLUME_STEP)

    def status(self) -> Status:
        with self._lock:
            try:
                vol = self.volume.get_volume()
            except Exception:
                vol = 0
            return Status(self.state, self.alarm, self.snooze_until, self.error,
                          self.fallback_active, vol, self._volume_changed_at)

    # --- internals ------------------------------------------------------------
    def _snooze_elapsed(self, alarm: AlarmSpec) -> None:
        with self._lock:
            if self.state != State.SNOOZED:
                return
            self.state = State.IDLE  # let ring() proceed
        self.ring(alarm)

    def _silence(self) -> None:
        self._cancel_fade()
        if self.fallback_active:
            self.fallback.stop()
            self.fallback_active = False
        else:
            # Pause on a background thread: Spotify may be slow and the button should feel instant.
            threading.Thread(target=self.player.pause, name="spotify-pause", daemon=True).start()

    def _set_volume(self, pct: int) -> None:
        try:
            self.volume.set_volume(pct)
            self._volume_changed_at = time.monotonic()
        except Exception:
            log.exception("Failed to set volume")

    def _start_fade(self, start: int, target: int, duration_s: float) -> None:
        if duration_s <= 0:
            self._set_volume(target)
            return
        steps = fade_steps(start, target, duration_s, self._fade_interval)
        cancel = threading.Event()
        self._fade_cancel = cancel

        def run() -> None:
            for vol in steps:
                if cancel.wait(self._fade_interval):
                    return
                try:
                    self.volume.set_volume(vol)
                except Exception:
                    log.exception("Fade step failed")

        self._fade_thread = threading.Thread(target=run, name="volume-fade", daemon=True)
        self._fade_thread.start()

    def _cancel_fade(self) -> None:
        self._fade_cancel.set()

    def _notify(self) -> None:
        for listener in list(self.listeners):
            try:
                listener()
            except Exception:
                log.exception("Status listener failed")
