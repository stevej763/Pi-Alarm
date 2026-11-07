from datetime import datetime

from app.models import Alarm
from app.scheduler import AlarmScheduler, build_trigger


def test_trigger_fires_on_selected_days_only():
    alarm = Alarm(id=1, hour=7, minute=30, days="mon,wed")
    trigger = build_trigger(alarm)
    # Friday 6 Nov 2026 -> next is Monday 9 Nov 07:30
    start = datetime(2026, 11, 6, 12, 0).astimezone()
    nxt = trigger.get_next_fire_time(None, start)
    assert (nxt.year, nxt.month, nxt.day, nxt.hour, nxt.minute) == (2026, 11, 9, 7, 30)
    nxt2 = trigger.get_next_fire_time(nxt, nxt.replace(minute=31))
    assert nxt2.day == 11  # Wednesday


def test_no_days_means_no_trigger():
    assert build_trigger(Alarm(id=1, days="")) is None


def test_sync_jobs_skips_disabled_and_reports_next():
    fired = []
    sched = AlarmScheduler(on_alarm=fired.append)
    sched.sync_jobs([
        Alarm(id=1, label="On", hour=7, minute=0, days="mon,tue,wed,thu,fri,sat,sun"),
        Alarm(id=2, label="Off", hour=6, minute=0, days="mon,tue,wed,thu,fri,sat,sun", enabled=False),
    ])
    assert [j.id for j in sched.alarm_jobs()] == ["alarm-1"]
    when, label = sched.next_alarm()
    assert label == "On" and when.hour == 7


def test_paused_alarms_do_not_ring():
    fired = []
    sched = AlarmScheduler(on_alarm=fired.append, is_paused=lambda: True)
    sched._fire(Alarm(id=1).spec())
    assert fired == []
