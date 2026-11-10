"""On-device music: uploaded MP3s and playlists built from them."""
from __future__ import annotations

import logging
import re
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Dict, Iterable, List, Optional, Tuple

from sqlmodel import select

from .db import Database
from .models import LocalPlaylist, LocalPlaylistEntry, LocalTrack

log = logging.getLogger(__name__)

LOCAL_ALL = "local:all"
LOCAL_PLAYLIST_PREFIX = "local:playlist:"


def is_local(uri: str) -> bool:
    return uri.startswith("local:")


def playlist_uri(playlist_id: int) -> str:
    return f"{LOCAL_PLAYLIST_PREFIX}{playlist_id}"


class LibraryError(Exception):
    pass


@dataclass
class Tags:
    title: str
    artist: str
    album: str
    duration: float
    art: Optional[bytes] = None
    art_ext: str = ".jpg"


def read_tags(path: Path) -> Tags:
    """Read ID3 tags; raises if the file isn't an MP3."""
    from mutagen.mp3 import MP3

    audio = MP3(str(path))
    tags = audio.tags

    def text(key: str) -> str:
        frame = tags.get(key) if tags else None
        return str(frame.text[0]).strip() if frame and frame.text else ""

    art, ext = None, ".jpg"
    if tags:
        pictures = tags.getall("APIC")
        if pictures:
            art = pictures[0].data
            ext = ".png" if "png" in (pictures[0].mime or "") else ".jpg"
    return Tags(text("TIT2"), text("TPE1"), text("TALB"), float(audio.info.length or 0), art, ext)


def _safe_stem(filename: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(filename).stem).strip("-.")
    return stem[:60] or "track"


class Library:
    def __init__(self, db: Database, music_dir: Path):
        self.db = db
        self.dir = music_dir
        self.art_dir = music_dir / "art"
        self.art_dir.mkdir(parents=True, exist_ok=True)

    # --- tracks ---------------------------------------------------------------
    def path(self, track: LocalTrack) -> Path:
        return self.dir / track.filename

    def art_path(self, track: LocalTrack) -> Optional[Path]:
        if not track.art_filename:
            return None
        path = self.art_dir / track.art_filename
        return path if path.exists() else None

    def tracks(self) -> List[LocalTrack]:
        with self.db.session() as s:
            tracks = list(s.exec(select(LocalTrack)))
        return sorted(tracks, key=lambda t: (t.artist.casefold(), t.title.casefold()))

    def get_track(self, track_id: int) -> Optional[LocalTrack]:
        with self.db.session() as s:
            return s.get(LocalTrack, track_id)

    def add(self, filename: str, src: BinaryIO) -> LocalTrack:
        """Store an uploaded MP3 and record its tags."""
        if not filename.lower().endswith(".mp3"):
            raise LibraryError(f"{filename}: only MP3 files are supported")
        dest = self.dir / f"{_safe_stem(filename)}-{uuid.uuid4().hex[:8]}.mp3"
        with dest.open("wb") as f:
            shutil.copyfileobj(src, f, 1024 * 1024)
        try:
            tags = read_tags(dest)
            if tags.duration <= 0:
                raise ValueError("no audio")
        except Exception as e:
            dest.unlink(missing_ok=True)
            log.info("Rejected upload %s: %s", filename, e)
            raise LibraryError(f"{filename}: not a playable MP3 file") from e

        track = LocalTrack(filename=dest.name, title=tags.title or Path(filename).stem, artist=tags.artist,
                           album=tags.album, duration=tags.duration)
        if tags.art:
            track.art_filename = dest.stem + tags.art_ext
            (self.art_dir / track.art_filename).write_bytes(tags.art)
        with self.db.session() as s:
            s.add(track)
            s.commit()
            s.refresh(track)
        return track

    def delete_track(self, track_id: int) -> None:
        with self.db.session() as s:
            track = s.get(LocalTrack, track_id)
            if not track:
                return
            for entry in s.exec(select(LocalPlaylistEntry).where(LocalPlaylistEntry.track_id == track_id)):
                s.delete(entry)
            s.delete(track)
            s.commit()
        self.path(track).unlink(missing_ok=True)
        if track.art_filename:
            (self.art_dir / track.art_filename).unlink(missing_ok=True)

    # --- playlists ------------------------------------------------------------
    def playlists(self) -> List[LocalPlaylist]:
        with self.db.session() as s:
            playlists = list(s.exec(select(LocalPlaylist)))
        return sorted(playlists, key=lambda p: p.name.casefold())

    def get_playlist(self, playlist_id: int) -> Optional[LocalPlaylist]:
        with self.db.session() as s:
            return s.get(LocalPlaylist, playlist_id)

    def create_playlist(self, name: str) -> LocalPlaylist:
        playlist = LocalPlaylist(name=name.strip() or "New playlist")
        with self.db.session() as s:
            s.add(playlist)
            s.commit()
            s.refresh(playlist)
        return playlist

    def rename_playlist(self, playlist_id: int, name: str) -> None:
        with self.db.session() as s:
            playlist = s.get(LocalPlaylist, playlist_id)
            if playlist and name.strip():
                playlist.name = name.strip()
                s.add(playlist)
                s.commit()

    def delete_playlist(self, playlist_id: int) -> None:
        with self.db.session() as s:
            for entry in s.exec(select(LocalPlaylistEntry).where(LocalPlaylistEntry.playlist_id == playlist_id)):
                s.delete(entry)
            playlist = s.get(LocalPlaylist, playlist_id)
            if playlist:
                s.delete(playlist)
            s.commit()

    def entries(self, playlist_id: int) -> List[Tuple[LocalPlaylistEntry, LocalTrack]]:
        with self.db.session() as s:
            rows = s.exec(
                select(LocalPlaylistEntry, LocalTrack)
                .where(LocalPlaylistEntry.playlist_id == playlist_id)
                .where(LocalPlaylistEntry.track_id == LocalTrack.id)
                .order_by(LocalPlaylistEntry.position, LocalPlaylistEntry.id)
            )
            return list(rows)

    def track_counts(self) -> Dict[int, int]:
        counts: Dict[int, int] = {}
        with self.db.session() as s:
            for entry in s.exec(select(LocalPlaylistEntry)):
                counts[entry.playlist_id] = counts.get(entry.playlist_id, 0) + 1
        return counts

    def add_to_playlist(self, playlist_id: int, track_ids: Iterable[int]) -> None:
        with self.db.session() as s:
            existing = list(s.exec(select(LocalPlaylistEntry).where(LocalPlaylistEntry.playlist_id == playlist_id)))
            position = max((e.position for e in existing), default=-1) + 1
            present = {e.track_id for e in existing}
            for track_id in track_ids:
                if track_id in present or not s.get(LocalTrack, track_id):
                    continue
                s.add(LocalPlaylistEntry(playlist_id=playlist_id, track_id=track_id, position=position))
                present.add(track_id)
                position += 1
            s.commit()

    def remove_entry(self, entry_id: int) -> None:
        with self.db.session() as s:
            entry = s.get(LocalPlaylistEntry, entry_id)
            if entry:
                s.delete(entry)
                s.commit()

    def move_entry(self, entry_id: int, delta: int) -> None:
        """Swap an entry with its neighbour (delta -1 = up, +1 = down)."""
        with self.db.session() as s:
            entry = s.get(LocalPlaylistEntry, entry_id)
            if not entry:
                return
            ordered = list(s.exec(select(LocalPlaylistEntry)
                                  .where(LocalPlaylistEntry.playlist_id == entry.playlist_id)
                                  .order_by(LocalPlaylistEntry.position, LocalPlaylistEntry.id)))
            i = next(i for i, e in enumerate(ordered) if e.id == entry_id)
            j = i + delta
            if not 0 <= j < len(ordered):
                return
            ordered[i], ordered[j] = ordered[j], ordered[i]
            for position, e in enumerate(ordered):  # renumber so positions stay unique
                e.position = position
                s.add(e)
            s.commit()

    # --- alarm playback ---------------------------------------------------------
    def choices(self) -> List[dict]:
        """Options for the alarm form's playlist picker."""
        options = []
        if self.tracks():
            options.append({"name": "All music on the device", "uri": LOCAL_ALL})
        options += [{"name": p.name, "uri": playlist_uri(p.id)} for p in self.playlists()]
        return options

    def tracks_for(self, uri: str) -> List[LocalTrack]:
        if uri == LOCAL_ALL:
            return self.tracks()
        if uri.startswith(LOCAL_PLAYLIST_PREFIX):
            try:
                playlist_id = int(uri[len(LOCAL_PLAYLIST_PREFIX):])
            except ValueError:
                return []
            return [track for _, track in self.entries(playlist_id)]
        return []
