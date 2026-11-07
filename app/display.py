"""240x240 screen for the Pirate Audio: renders with Pillow, outputs to the ST7789 or a PNG preview."""
from __future__ import annotations

import io
import logging
import threading
import time as _time
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path
from typing import Callable, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

from .alarm_runner import State, Status
from .spotify import Track

log = logging.getLogger(__name__)

SIZE = 240
BG = (14, 16, 22)
TEXT = (244, 239, 230)
DIM = (128, 134, 145)
ACCENT = (255, 179, 71)
ERROR = (255, 107, 107)

FONT_CANDIDATES = {
    True: [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/Library/Fonts/Arial Bold.ttf",
    ],
    False: [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/Library/Fonts/Arial.ttf",
    ],
}
_font_cache: dict = {}


def font(size: int, bold: bool = False):
    key = (size, bold)
    if key not in _font_cache:
        for path in FONT_CANDIDATES[bold]:
            if Path(path).exists():
                _font_cache[key] = ImageFont.truetype(path, size)
                break
        else:
            _font_cache[key] = ImageFont.load_default(size=size)
    return _font_cache[key]


@dataclass
class DisplayView:
    now: datetime
    status: Status
    next_alarm: Optional[Tuple[datetime, str]] = None
    alarms_paused: bool = False
    track: Optional[Track] = None
    art: Optional[Image.Image] = None
    show_volume: bool = False


def _fit(draw: ImageDraw.ImageDraw, text: str, fnt, max_width: int) -> str:
    if draw.textlength(text, font=fnt) <= max_width:
        return text
    while text and draw.textlength(text + "…", font=fnt) > max_width:
        text = text[:-1]
    return text + "…"


def _center(draw: ImageDraw.ImageDraw, y: int, text: str, fnt, fill, max_width: int = SIZE - 16) -> None:
    text = _fit(draw, text, fnt, max_width)
    w = draw.textlength(text, font=fnt)
    draw.text(((SIZE - w) / 2, y), text, font=fnt, fill=fill)


def _corner_labels(draw: ImageDraw.ImageDraw, a: str, b: str, x: str, y: str) -> None:
    """Labels next to the physical buttons: A/B on the left edge, X/Y on the right."""
    f = font(13, bold=True)
    pad = 6
    bottom = SIZE - pad - 14
    draw.text((pad, pad), a, font=f, fill=ACCENT)
    draw.text((pad, bottom), b, font=f, fill=ACCENT)
    draw.text((SIZE - pad - draw.textlength(x, font=f), pad), x, font=f, fill=DIM)
    draw.text((SIZE - pad - draw.textlength(y, font=f), bottom), y, font=f, fill=DIM)


def _volume_bar(draw: ImageDraw.ImageDraw, volume: int) -> None:
    x0, x1, y0 = 40, SIZE - 40, SIZE - 32
    draw.rounded_rectangle((x0, y0, x1, y0 + 10), radius=5, fill=(40, 44, 54))
    filled = x0 + (x1 - x0) * volume / 100
    if filled > x0 + 4:
        draw.rounded_rectangle((x0, y0, filled, y0 + 10), radius=5, fill=ACCENT)
    _center(draw, y0 - 18, f"Volume {volume}%", font(13), DIM)


def _format_next(now: datetime, when: datetime) -> str:
    when = when.replace(tzinfo=None) if when.tzinfo else when
    if when.date() == now.date():
        day = "Today"
    elif (when.date() - now.date()).days == 1:
        day = "Tomorrow"
    else:
        day = when.strftime("%a")
    return f"{day} {when:%H:%M}"


def render(view: DisplayView) -> Image.Image:
    img = Image.new("RGB", (SIZE, SIZE), BG)
    draw = ImageDraw.Draw(img)
    st = view.status

    if st.state == State.RINGING:
        _render_ringing(img, draw, view)
    elif st.state == State.SNOOZED:
        _render_snoozed(draw, view)
    else:
        _render_idle(draw, view)

    if st.error and st.state != State.IDLE:
        msg = "Spotify unavailable - backup chime" if st.fallback_active else st.error
        _center(draw, SIZE - 52, msg, font(12), ERROR)
    return img


def _render_idle(draw: ImageDraw.ImageDraw, view: DisplayView) -> None:
    _center(draw, 48, f"{view.now:%H:%M}", font(72, bold=True), TEXT)
    _center(draw, 132, f"{view.now:%A %-d %B}", font(17), DIM)
    if view.show_volume:
        _volume_bar(draw, view.status.volume)
    elif view.alarms_paused:
        _center(draw, 178, "Alarms paused", font(18, bold=True), ERROR)
    elif view.next_alarm:
        when, label = view.next_alarm
        _center(draw, 170, "Next alarm", font(13), DIM)
        _center(draw, 188, _format_next(view.now, when), font(20, bold=True), ACCENT)
    else:
        _center(draw, 178, "No alarms set", font(16), DIM)


def _render_ringing(img: Image.Image, draw: ImageDraw.ImageDraw, view: DisplayView) -> None:
    _corner_labels(draw, "SNOOZE", "STOP", "VOL+", "VOL-")
    art_size = 112
    if view.art is not None:
        art = view.art.convert("RGB").resize((art_size, art_size))
        img.paste(art, ((SIZE - art_size) // 2, 26))
    else:
        x0 = (SIZE - art_size) // 2
        draw.rounded_rectangle((x0, 26, x0 + art_size, 26 + art_size), radius=10, fill=(32, 36, 46))
        _center(draw, 26 + art_size // 2 - 22, f"{view.now:%H:%M}", font(34, bold=True), ACCENT)
    if view.track:
        _center(draw, 148, view.track.title, font(17, bold=True), TEXT, SIZE - 24)
        _center(draw, 170, view.track.artist, font(14), DIM, SIZE - 24)
    else:
        label = view.status.alarm.label if view.status.alarm else "Alarm"
        _center(draw, 152, label, font(18, bold=True), TEXT)
    if view.show_volume:
        _volume_bar(draw, view.status.volume)
    else:
        _center(draw, SIZE - 22, f"{view.now:%H:%M}", font(14, bold=True), DIM, 120)


def _render_snoozed(draw: ImageDraw.ImageDraw, view: DisplayView) -> None:
    _corner_labels(draw, "", "STOP", "", "")
    until = view.status.snooze_until
    _center(draw, 30, f"{view.now:%H:%M}", font(22, bold=True), DIM)
    _center(draw, 70, "Snoozing", font(26, bold=True), TEXT)
    if until:
        remaining = max(0, int((until - view.now).total_seconds()))
        _center(draw, 108, f"{remaining // 60}:{remaining % 60:02d}", font(52, bold=True), ACCENT)
        _center(draw, 176, f"rings again at {until:%H:%M}", font(15), DIM)


def is_night(now: time, start: time, end: time) -> bool:
    if start <= end:
        return start <= now < end
    return now >= start or now < end


# --- outputs -----------------------------------------------------------------

class PreviewOutput:
    """Writes frames to a PNG (development / no hardware)."""

    def __init__(self, path: Path):
        self.path = path
        self.brightness = 100

    def show(self, img: Image.Image) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp.png")
        img.save(tmp)
        tmp.replace(self.path)

    def set_brightness(self, pct: int) -> None:
        self.brightness = pct

    def close(self) -> None:
        pass


class ST7789Output:
    """Pirate Audio LCD: ST7789 on SPI0 CE1, DC on GPIO9, PWM backlight on GPIO13."""

    def __init__(self, backlight_pin: int = 13):
        import st7789  # type: ignore
        from gpiozero import PWMLED  # type: ignore

        self.disp = st7789.ST7789(port=0, cs=1, dc=9, backlight=None, rotation=90,
                                  width=SIZE, height=SIZE, spi_speed_hz=80_000_000)
        if hasattr(self.disp, "begin"):
            self.disp.begin()
        self.backlight = PWMLED(backlight_pin)
        self.backlight.value = 1.0

    def show(self, img: Image.Image) -> None:
        self.disp.display(img)

    def set_brightness(self, pct: int) -> None:
        self.backlight.value = max(0.0, min(1.0, pct / 100))

    def close(self) -> None:
        self.backlight.value = 0
        self.backlight.close()


class DisplayLoop:
    """Redraws the screen about once a second (faster right after a volume change)."""

    WAKE_SECONDS = 10
    VOLUME_OVERLAY_SECONDS = 2.5

    def __init__(self, output, build_view: Callable[[], DisplayView], night: Tuple[time, time],
                 night_brightness: int):
        self.output = output
        self.build_view = build_view
        self.night_start, self.night_end = night
        self.night_brightness = night_brightness
        self._wake_until = 0.0
        self._kick = threading.Event()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_png: bytes = b""
        self._last_brightness: Optional[int] = None
        self._tick_lock = threading.Lock()

    @property
    def last_frame_png(self) -> bytes:
        return self._last_png

    def wake(self) -> None:
        self._wake_until = _time.monotonic() + self.WAKE_SECONDS
        self.refresh()

    def refresh(self) -> None:
        self._kick.set()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="display", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._kick.set()
        if self._thread:
            self._thread.join(timeout=2)
        self.output.close()

    def tick(self) -> None:
        with self._tick_lock:
            self._tick()

    def _tick(self) -> None:
        view = self.build_view()
        view.show_volume = _time.monotonic() - view.status.volume_changed_at < self.VOLUME_OVERLAY_SECONDS
        img = render(view)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        png = buf.getvalue()
        if png != self._last_png:
            self.output.show(img)
            self._last_png = png
        self._apply_brightness(view)

    def _apply_brightness(self, view: DisplayView) -> None:
        awake = view.status.state != State.IDLE or _time.monotonic() < self._wake_until
        dim = is_night(view.now.time(), self.night_start, self.night_end) and not awake
        level = self.night_brightness if dim else 100
        if level != self._last_brightness:
            self.output.set_brightness(level)
            self._last_brightness = level

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                log.exception("Display update failed")
            self._kick.wait(timeout=1.0 - (_time.time() % 1.0) + 0.02)  # tick on the second
            self._kick.clear()
