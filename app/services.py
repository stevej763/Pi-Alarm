"""Builds and owns all long-lived components so the web app (and tests) can share them."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from .alarm_runner import AlarmRunner, State
from .audio import FallbackPlayer, ensure_fallback_wav, make_volume
from .config import Settings
from .db import Database
from .display import DisplayLoop, DisplayView, PreviewOutput, ST7789Output
from .now_playing import NowPlaying
from .scheduler import AlarmScheduler
from .spotify import SpotifyService

log = logging.getLogger(__name__)


class Services:
    def __init__(self, settings: Settings, spotify=None, volume=None, fallback=None, use_hardware: Optional[bool] = None):
        self.settings = settings
        self.db = Database(settings.db_path)
        self.spotify = spotify or SpotifyService(settings)
        self.volume = volume or make_volume(settings.audio_backend, settings.alsa_control, settings.alsa_card)
        self.fallback = fallback or FallbackPlayer(ensure_fallback_wav(settings.fallback_file))

        tz = None
        if settings.timezone:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo(settings.timezone)
        self.scheduler = AlarmScheduler(on_alarm=lambda spec: self.runner.ring(spec),
                                        is_paused=lambda: self.db.alarms_paused, timezone=tz)
        self.runner = AlarmRunner(self.spotify, self.volume, self.fallback, self.scheduler,
                                  snooze_minutes=settings.snooze_minutes, normal_volume=settings.normal_volume)

        self.now_playing = NowPlaying(
            self.spotify,
            should_poll=lambda: self.runner.state == State.RINGING and not self.runner.fallback_active,
            on_change=lambda: self.display.refresh(),
        )
        self.use_hardware = settings.use_pi_hardware if use_hardware is None else use_hardware
        output = self._make_output()
        self.display = DisplayLoop(output, self.build_view, (settings.night_start, settings.night_end),
                                   settings.night_brightness)
        self.runner.listeners += [self.display.refresh, self.now_playing.poke]
        self.buttons = None

    def _make_output(self):
        if self.use_hardware:
            try:
                return ST7789Output(self.settings.backlight_pin)
            except (Exception, SystemExit):  # gpiodevice raises SystemExit for pin errors
                log.exception("Display init failed; falling back to PNG preview")
        return PreviewOutput(self.settings.display_preview)

    def build_view(self) -> DisplayView:
        return DisplayView(
            now=datetime.now(),
            status=self.runner.status(),
            next_alarm=self.scheduler.next_alarm(),
            alarms_paused=self.db.alarms_paused,
            track=self.now_playing.track,
            art=self.now_playing.art,
        )

    def toggle_alarms_paused(self) -> None:
        self.db.alarms_paused = not self.db.alarms_paused
        self.display.refresh()

    def resync(self) -> None:
        self.scheduler.sync_jobs(self.db.alarms())
        self.display.refresh()

    def start(self) -> None:
        self.scheduler.start()
        self.resync()
        self.display.start()
        self.now_playing.start()
        if self.use_hardware:
            try:
                from .buttons import Buttons
                self.buttons = Buttons(self.settings.button_pins, self.runner, self.display.wake,
                                       self.toggle_alarms_paused)
            except Exception:
                log.exception("Button init failed")

    def shutdown(self) -> None:
        self.runner.stop()
        self.now_playing.stop()
        self.display.stop()
        if self.buttons:
            self.buttons.close()
        self.scheduler.shutdown()
