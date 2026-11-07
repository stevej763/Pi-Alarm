"""Renders every screen; PNGs land in data/test-screens/ for a visual check."""
from datetime import datetime, timedelta
from pathlib import Path

from PIL import Image

from app.alarm_runner import State, Status
from app.display import DisplayView, is_night, render
from app.models import AlarmSpec
from app.spotify import Track

OUT = Path(__file__).resolve().parent.parent / "data" / "test-screens"
NOW = datetime(2026, 11, 9, 6, 58, 12)
SPEC = AlarmSpec(1, "Weekday wake-up", "spotify:playlist:abc")


def status(state, **kw):
    base = dict(state=state, alarm=SPEC if state != State.IDLE else None, snooze_until=None,
                error=None, fallback_active=False, volume=35, volume_changed_at=0.0)
    base.update(kw)
    return Status(**base)


def save(name, view):
    img = render(view)
    assert img.size == (240, 240)
    OUT.mkdir(parents=True, exist_ok=True)
    img.save(OUT / f"{name}.png")


def test_render_all_screens():
    art = Image.new("RGB", (300, 300), (200, 120, 60))
    save("idle", DisplayView(NOW, status(State.IDLE), next_alarm=(NOW + timedelta(minutes=2), "Wake")))
    save("idle_tomorrow", DisplayView(NOW, status(State.IDLE), next_alarm=(NOW + timedelta(days=1), "Wake")))
    save("idle_paused", DisplayView(NOW, status(State.IDLE), alarms_paused=True))
    save("idle_volume", DisplayView(NOW, status(State.IDLE), show_volume=True))
    save("ringing_track", DisplayView(NOW, status(State.RINGING), art=art,
                                      track=Track("Here Comes the Sun (Remastered 2009)", "The Beatles",
                                                  "Abbey Road", None, True)))
    save("ringing_loading", DisplayView(NOW, status(State.RINGING)))
    save("ringing_volume", DisplayView(NOW, status(State.RINGING), show_volume=True))
    save("ringing_fallback", DisplayView(NOW, status(State.RINGING, error="x", fallback_active=True)))
    save("snoozed", DisplayView(NOW, status(State.SNOOZED, snooze_until=NOW + timedelta(minutes=8, seconds=30))))


def test_is_night_wraps_midnight():
    from datetime import time
    assert is_night(time(23, 0), time(22, 0), time(6, 30))
    assert is_night(time(3, 0), time(22, 0), time(6, 30))
    assert not is_night(time(12, 0), time(22, 0), time(6, 30))
