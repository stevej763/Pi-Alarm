"""Pirate Audio buttons: A=snooze, B=stop (hold 2s = pause/resume all alarms), X/Y=volume."""
from __future__ import annotations

import logging
from typing import Callable, Dict

log = logging.getLogger(__name__)


class Buttons:
    def __init__(self, pins: Dict[str, int], runner, wake_display: Callable[[], None],
                 toggle_alarms_paused: Callable[[], None]):
        from gpiozero import Button  # type: ignore

        self.runner = runner
        self.wake = wake_display
        self.toggle_paused = toggle_alarms_paused

        self.a = Button(pins["a"], pull_up=True, bounce_time=0.05)
        self.b = Button(pins["b"], pull_up=True, bounce_time=0.05, hold_time=2.0)
        self.x = Button(pins["x"], pull_up=True, bounce_time=0.05, hold_time=0.5, hold_repeat=True)
        self.y = Button(pins["y"], pull_up=True, bounce_time=0.05, hold_time=0.5, hold_repeat=True)
        self._b_held = False

        self.a.when_pressed = self._on_a
        self.b.when_held = self._on_b_held
        self.b.when_released = self._on_b_released
        self.x.when_pressed = self.x.when_held = self._on_x
        self.y.when_pressed = self.y.when_held = self._on_y

    def _on_a(self) -> None:
        self.wake()
        self.runner.snooze()

    def _on_b_held(self) -> None:
        self._b_held = True
        self.wake()
        self.toggle_paused()

    def _on_b_released(self) -> None:
        if not self._b_held:
            self.wake()
            self.runner.stop()
        self._b_held = False

    def _on_x(self) -> None:
        self.wake()
        self.runner.volume_up()

    def _on_y(self) -> None:
        self.wake()
        self.runner.volume_down()

    def close(self) -> None:
        for b in (self.a, self.b, self.x, self.y):
            b.close()
