from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from sqlmodel import Field, SQLModel

DAY_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


class Alarm(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    label: str = "Wake up"
    hour: int = 7
    minute: int = 0
    days: str = "mon,tue,wed,thu,fri"  # comma-separated DAY_NAMES
    playlist_uri: str = ""
    playlist_name: str = ""
    enabled: bool = True
    shuffle: bool = True
    fade_minutes: int = 5
    start_volume: int = 5
    target_volume: int = 70

    @property
    def day_list(self) -> List[str]:
        return [d for d in self.days.split(",") if d in DAY_NAMES]

    @property
    def time_str(self) -> str:
        return f"{self.hour:02d}:{self.minute:02d}"

    def spec(self) -> "AlarmSpec":
        return AlarmSpec(
            alarm_id=self.id,
            label=self.label,
            playlist_uri=self.playlist_uri,
            shuffle=self.shuffle,
            fade_minutes=self.fade_minutes,
            start_volume=self.start_volume,
            target_volume=self.target_volume,
        )


class AppSetting(SQLModel, table=True):
    key: str = Field(primary_key=True)
    value: str = ""


class LocalTrack(SQLModel, table=True):
    """An uploaded MP3 stored in the music folder."""

    id: Optional[int] = Field(default=None, primary_key=True)
    filename: str
    title: str = ""
    artist: str = ""
    album: str = ""
    duration: float = 0.0
    art_filename: str = ""

    @property
    def duration_str(self) -> str:
        seconds = int(self.duration)
        return f"{seconds // 60}:{seconds % 60:02d}"


class LocalPlaylist(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = "New playlist"


class LocalPlaylistEntry(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    playlist_id: int = Field(index=True)
    track_id: int = Field(index=True)
    position: int = 0


@dataclass
class Track:
    """What's playing right now, from Spotify or a local file."""

    title: str
    artist: str
    album: str
    art_url: Optional[str]
    is_playing: bool


@dataclass(frozen=True)
class AlarmSpec:
    """Immutable snapshot of an alarm, handed to the runner so it doesn't hold DB objects."""

    alarm_id: Optional[int]
    label: str
    playlist_uri: str
    shuffle: bool = True
    fade_minutes: int = 5
    start_volume: int = 5
    target_volume: int = 70
