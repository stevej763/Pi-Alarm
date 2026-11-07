from __future__ import annotations

from pathlib import Path
from typing import List

from sqlmodel import Session, SQLModel, create_engine, select

from .models import Alarm, AppSetting

ALARMS_PAUSED = "alarms_paused"


class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
        SQLModel.metadata.create_all(self.engine)

    def session(self) -> Session:
        return Session(self.engine, expire_on_commit=False)

    def alarms(self) -> List[Alarm]:
        with self.session() as s:
            return list(s.exec(select(Alarm).order_by(Alarm.hour, Alarm.minute)))

    def get_alarm(self, alarm_id: int) -> Alarm | None:
        with self.session() as s:
            return s.get(Alarm, alarm_id)

    def save_alarm(self, alarm: Alarm) -> Alarm:
        with self.session() as s:
            s.add(alarm)
            s.commit()
            s.refresh(alarm)
            return alarm

    def delete_alarm(self, alarm_id: int) -> None:
        with self.session() as s:
            alarm = s.get(Alarm, alarm_id)
            if alarm:
                s.delete(alarm)
                s.commit()

    def get_setting(self, key: str, default: str = "") -> str:
        with self.session() as s:
            row = s.get(AppSetting, key)
            return row.value if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self.session() as s:
            row = s.get(AppSetting, key) or AppSetting(key=key)
            row.value = value
            s.add(row)
            s.commit()

    @property
    def alarms_paused(self) -> bool:
        return self.get_setting(ALARMS_PAUSED) == "1"

    @alarms_paused.setter
    def alarms_paused(self, paused: bool) -> None:
        self.set_setting(ALARMS_PAUSED, "1" if paused else "0")
