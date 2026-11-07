"""APScheduler wrapper: one cron job per enabled alarm, plus a one-off snooze job."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Callable, Iterable, Optional, Tuple

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from .models import Alarm, AlarmSpec

log = logging.getLogger(__name__)

ALARM_PREFIX = "alarm-"
SNOOZE_JOB = "snooze"


def build_trigger(alarm: Alarm, timezone=None) -> Optional[CronTrigger]:
    days = alarm.day_list
    if not days:
        return None
    return CronTrigger(day_of_week=",".join(days), hour=alarm.hour, minute=alarm.minute, timezone=timezone)


class AlarmScheduler:
    def __init__(self, on_alarm: Callable[[AlarmSpec], None], is_paused: Callable[[], bool] = lambda: False,
                 timezone=None):
        self.on_alarm = on_alarm
        self.is_paused = is_paused
        self.timezone = timezone
        kwargs = {"timezone": timezone} if timezone else {}
        self.scheduler = BackgroundScheduler(
            job_defaults={"misfire_grace_time": 120, "coalesce": True, "max_instances": 1}, **kwargs
        )

    def start(self) -> None:
        self.scheduler.start()

    def shutdown(self) -> None:
        self.scheduler.shutdown(wait=False)

    def sync_jobs(self, alarms: Iterable[Alarm]) -> None:
        for job in self.scheduler.get_jobs():
            if job.id.startswith(ALARM_PREFIX):
                job.remove()
        for alarm in alarms:
            trigger = build_trigger(alarm, self.timezone or self.scheduler.timezone)
            if alarm.enabled and trigger:
                self.scheduler.add_job(self._fire, trigger, args=[alarm.spec()],
                                       id=f"{ALARM_PREFIX}{alarm.id}", name=alarm.label)
        log.info("Scheduled %d alarm(s)", len(self.alarm_jobs()))

    def alarm_jobs(self):
        return [j for j in self.scheduler.get_jobs() if j.id.startswith(ALARM_PREFIX)]

    def next_alarm(self) -> Optional[Tuple[datetime, str]]:
        """(when, label) of the soonest scheduled alarm, ignoring snooze."""
        upcoming = []
        for job in self.alarm_jobs():
            when = getattr(job, "next_run_time", None)
            if when is None:  # scheduler not started yet: compute it from the trigger
                when = job.trigger.get_next_fire_time(None, datetime.now(job.trigger.timezone))
            if when:
                upcoming.append((when, job.name))
        return min(upcoming, key=lambda x: x[0]) if upcoming else None

    def _fire(self, spec: AlarmSpec) -> None:
        if self.is_paused():
            log.info("Alarms paused; skipping %r", spec.label)
            return
        self.on_alarm(spec)

    # SnoozeTimer protocol for AlarmRunner
    def schedule_snooze(self, run_at: datetime, callback: Callable[[], None]) -> None:
        self.scheduler.add_job(callback, DateTrigger(run_date=run_at), id=SNOOZE_JOB, replace_existing=True)

    def cancel_snooze(self) -> None:
        if self.scheduler.get_job(SNOOZE_JOB):
            self.scheduler.remove_job(SNOOZE_JOB)
