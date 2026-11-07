import time
from datetime import datetime, timedelta

from app.alarm_runner import AlarmRunner, State, fade_steps
from app.models import AlarmSpec

from .fakes import FakeFallback, FakeSpotify, FakeTimer, FakeVolume

NOW = datetime(2026, 11, 9, 7, 0)
SPEC = AlarmSpec(alarm_id=1, label="Wake", playlist_uri="spotify:playlist:abc",
                 fade_minutes=0, start_volume=5, target_volume=70)


def make(fail=False, fade_interval=0.01):
    sp, vol, fb, timer = FakeSpotify(fail), FakeVolume(), FakeFallback(), FakeTimer()
    runner = AlarmRunner(sp, vol, fb, timer, snooze_minutes=9, normal_volume=60,
                         now=lambda: NOW, fade_interval=fade_interval)
    return runner, sp, vol, fb, timer


def test_fade_steps_end_at_target():
    steps = fade_steps(5, 70, 60, 3)
    assert len(steps) == 20
    assert steps[-1] == 70
    assert steps == sorted(steps)


def test_fade_steps_short_duration():
    assert fade_steps(10, 50, 1, 3) == [50]


def test_ring_starts_playlist_at_start_volume_then_target():
    runner, sp, vol, fb, _ = make()
    runner.ring(SPEC)
    assert runner.state == State.RINGING
    assert sp.started == [("spotify:playlist:abc", True)]
    assert vol.history[0] == 5
    assert vol.value == 70  # fade_minutes=0 jumps straight to target
    assert not fb.playing


def test_fade_ramps_volume_over_time():
    runner, _, vol, _, _ = make(fade_interval=0.02)
    vol.value = 0
    runner._start_fade(0, 60, duration_s=0.4)  # 20 steps of 3%
    time.sleep(0.15)
    assert 0 < vol.value < 60
    time.sleep(0.5)
    assert vol.value == 60


def test_fade_is_cancelled_by_stop():
    runner, _, vol, _, _ = make(fade_interval=0.02)
    spec = AlarmSpec(1, "Wake", "spotify:playlist:abc", fade_minutes=1, start_volume=5, target_volume=90)
    runner.ring(spec)
    runner.stop()
    time.sleep(0.1)
    assert vol.value == 60


def test_spotify_failure_uses_fallback():
    runner, _, _, fb, _ = make(fail=True)
    runner.ring(SPEC)
    assert runner.state == State.RINGING
    assert fb.playing
    assert "network down" in runner.status().error
    runner.stop()
    assert not fb.playing


def test_snooze_then_reraise_then_stop():
    runner, sp, vol, _, timer = make()
    runner.ring(SPEC)
    runner.snooze()
    assert runner.state == State.SNOOZED
    assert runner.snooze_until == NOW + timedelta(minutes=9)
    assert timer.run_at == NOW + timedelta(minutes=9)
    assert sp.paused.wait(1)

    timer.fire()
    assert runner.state == State.RINGING
    assert len(sp.started) == 2

    runner.stop()
    assert runner.state == State.IDLE
    assert timer.callback is None
    assert vol.value == 60  # back to normal listening volume


def test_stop_while_snoozed_cancels_snooze():
    runner, _, _, _, timer = make()
    runner.ring(SPEC)
    runner.snooze()
    runner.stop()
    assert runner.state == State.IDLE
    assert timer.callback is None


def test_second_ring_while_ringing_is_ignored():
    runner, sp, _, _, _ = make()
    runner.ring(SPEC)
    runner.ring(SPEC)
    assert len(sp.started) == 1


def test_snooze_when_idle_is_noop():
    runner, _, _, _, timer = make()
    runner.snooze()
    assert runner.state == State.IDLE
    assert timer.callback is None


def test_volume_nudge_cancels_fade_and_clamps():
    runner, _, vol, _, _ = make()
    vol.value = 98
    assert runner.volume_up() == 100
    vol.value = 3
    assert runner.volume_down() == 0
