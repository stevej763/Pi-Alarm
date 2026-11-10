from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List, Optional
from urllib.parse import urlencode

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .config import Settings
from .library import LibraryError, is_local
from .library import playlist_uri as local_playlist_uri
from .models import DAY_NAMES, Alarm
from .services import Services

log = logging.getLogger(__name__)
HERE = Path(__file__).parent
templates = Jinja2Templates(directory=str(HERE / "templates"))


def _back(path: str = "/", **params: str) -> RedirectResponse:
    query = urlencode({k: v for k, v in params.items() if v})
    return RedirectResponse(f"{path}?{query}" if query else path, status_code=303)


def create_app(services: Optional[Services] = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        svc = services or Services(Settings.from_env())
        app.state.svc = svc
        svc.start()
        yield
        svc.shutdown()

    app = FastAPI(title="Radio Alarm", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")

    def svc(request: Request) -> Services:
        return request.app.state.svc

    def spotify_playlists(s: Services) -> List[dict]:
        if not (s.spotify_enabled and s.spotify.authorized):
            return []
        try:
            return s.spotify.list_playlists()
        except Exception as e:
            log.warning("Could not list playlists: %s", e)
            return []

    def status_context(s: Services) -> dict:
        st = s.runner.status()
        return {"status": st, "next_alarm": s.scheduler.next_alarm(), "paused": s.db.alarms_paused}

    # --- pages ----------------------------------------------------------------
    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        s = svc(request)
        enabled = s.spotify_enabled
        return templates.TemplateResponse(request, "index.html", {
            "alarms": s.db.alarms(),
            "spotify_enabled": enabled,
            "spotify_configured": enabled and s.spotify.configured,
            "spotify_authorized": enabled and s.spotify.authorized,
            "has_music": bool(s.library.tracks()),
            "device_name": s.settings.spotify_device_name,
            **status_context(s),
        })

    @app.get("/status", response_class=HTMLResponse)
    def status_partial(request: Request):
        return templates.TemplateResponse(request, "_status.html", status_context(svc(request)))

    def alarm_form(request: Request, alarm: Alarm) -> HTMLResponse:
        s = svc(request)
        local, spotify = s.library.choices(), spotify_playlists(s)
        known = {p["uri"] for p in local + spotify}
        return templates.TemplateResponse(request, "alarm_form.html", {
            "alarm": alarm, "days": DAY_NAMES, "local_playlists": local, "spotify_playlists": spotify,
            "spotify_enabled": s.spotify_enabled,
            # A playlist that isn't listed (deleted, or Spotify is off/unreachable) stays selected so saving keeps it.
            "unlisted": bool(alarm.playlist_uri) and alarm.playlist_uri not in known,
            "custom_uri": "" if alarm.playlist_uri in known or is_local(alarm.playlist_uri) else alarm.playlist_uri,
        })

    @app.get("/alarms/new", response_class=HTMLResponse)
    def new_alarm(request: Request):
        return alarm_form(request, Alarm())

    @app.get("/alarms/{alarm_id}/edit", response_class=HTMLResponse)
    def edit_alarm(request: Request, alarm_id: int):
        alarm = svc(request).db.get_alarm(alarm_id)
        if not alarm:
            raise HTTPException(404)
        return alarm_form(request, alarm)

    @app.post("/alarms")
    @app.post("/alarms/{alarm_id}")
    def save_alarm(
        request: Request,
        alarm_id: Optional[int] = None,
        label: str = Form("Wake up"),
        time: str = Form(...),
        days: List[str] = Form([]),
        playlist_uri: str = Form(""),
        playlist_custom: str = Form(""),
        shuffle: bool = Form(False),
        fade_minutes: int = Form(5),
        start_volume: int = Form(5),
        target_volume: int = Form(70),
    ):
        s = svc(request)
        alarm = s.db.get_alarm(alarm_id) if alarm_id else Alarm()
        if alarm is None:
            raise HTTPException(404)
        hour, minute = (int(x) for x in time.split(":")[:2])
        uri = playlist_custom.strip() or playlist_uri
        uri = _normalise_spotify_uri(uri)
        names = {p["uri"]: p["name"] for p in s.library.choices() + spotify_playlists(s)}
        if uri in names:
            alarm.playlist_name = names[uri]
        elif uri != alarm.playlist_uri:
            alarm.playlist_name = uri
        alarm.label = label.strip() or "Wake up"
        alarm.hour, alarm.minute = hour, minute
        alarm.days = ",".join(d for d in DAY_NAMES if d in days)
        alarm.playlist_uri = uri
        alarm.shuffle = shuffle
        alarm.fade_minutes = max(0, min(60, fade_minutes))
        alarm.start_volume = max(0, min(100, start_volume))
        alarm.target_volume = max(0, min(100, target_volume))
        s.db.save_alarm(alarm)
        s.resync()
        return _back()

    @app.post("/alarms/{alarm_id}/delete")
    def delete_alarm(request: Request, alarm_id: int):
        s = svc(request)
        s.db.delete_alarm(alarm_id)
        s.resync()
        return _back()

    @app.post("/alarms/{alarm_id}/toggle")
    def toggle_alarm(request: Request, alarm_id: int):
        s = svc(request)
        alarm = s.db.get_alarm(alarm_id)
        if alarm:
            alarm.enabled = not alarm.enabled
            s.db.save_alarm(alarm)
            s.resync()
        return _back()

    @app.post("/alarms/{alarm_id}/test")
    def test_alarm(request: Request, alarm_id: int):
        s = svc(request)
        alarm = s.db.get_alarm(alarm_id)
        if not alarm:
            raise HTTPException(404)
        import threading
        threading.Thread(target=s.runner.ring, args=(alarm.spec(),), daemon=True).start()
        return _back()

    # --- controls -------------------------------------------------------------
    @app.post("/snooze")
    def snooze(request: Request):
        svc(request).runner.snooze()
        return _back()

    @app.post("/stop")
    def stop(request: Request):
        svc(request).runner.stop()
        return _back()

    @app.post("/volume/{direction}")
    def volume(request: Request, direction: str):
        r = svc(request).runner
        r.volume_up() if direction == "up" else r.volume_down()
        return _back()

    @app.post("/pause-all")
    def pause_all(request: Request):
        svc(request).toggle_alarms_paused()
        return _back()

    @app.get("/display.png")
    def display_png(request: Request):
        png = svc(request).display.last_frame_png
        if not png:
            raise HTTPException(404)
        return Response(png, media_type="image/png", headers={"Cache-Control": "no-store"})

    # --- on-device music -------------------------------------------------------
    def get_playlist_or_404(s: Services, playlist_id: int):
        playlist = s.library.get_playlist(playlist_id)
        if not playlist:
            raise HTTPException(404)
        return playlist

    @app.get("/music", response_class=HTMLResponse)
    def music(request: Request, msg: str = "", error: str = ""):
        s = svc(request)
        return templates.TemplateResponse(request, "music.html", {
            "tracks": s.library.tracks(), "playlists": s.library.playlists(),
            "counts": s.library.track_counts(), "msg": msg, "error": error,
        })

    @app.post("/music/upload")
    def upload_music(request: Request, files: List[UploadFile] = File(...), playlist_id: Optional[int] = Form(None)):
        s = svc(request)
        added, errors = [], []
        for upload in files:
            if not upload.filename:
                continue
            try:
                added.append(s.library.add(upload.filename, upload.file).id)
            except LibraryError as e:
                errors.append(str(e))
        target = "/music"
        if playlist_id and s.library.get_playlist(playlist_id):
            s.library.add_to_playlist(playlist_id, added)
            target = f"/music/playlists/{playlist_id}"
        msg = f"Added {len(added)} track{'s' if len(added) != 1 else ''}" if added else ""
        return _back(target, msg=msg, error="; ".join(errors))

    @app.get("/music/tracks/{track_id}/audio")
    def track_audio(request: Request, track_id: int):
        s = svc(request)
        track = s.library.get_track(track_id)
        if not track or not s.library.path(track).exists():
            raise HTTPException(404)
        return FileResponse(s.library.path(track), media_type="audio/mpeg")

    @app.get("/music/tracks/{track_id}/art")
    def track_art(request: Request, track_id: int):
        s = svc(request)
        track = s.library.get_track(track_id)
        art = s.library.art_path(track) if track else None
        if not art:
            raise HTTPException(404)
        return FileResponse(art)

    @app.post("/music/tracks/{track_id}/delete")
    def delete_track(request: Request, track_id: int):
        svc(request).library.delete_track(track_id)
        return _back("/music")

    @app.post("/music/playlists")
    def create_playlist(request: Request, name: str = Form("")):
        playlist = svc(request).library.create_playlist(name)
        return _back(f"/music/playlists/{playlist.id}")

    @app.get("/music/playlists/{playlist_id}", response_class=HTMLResponse)
    def playlist_page(request: Request, playlist_id: int, msg: str = "", error: str = ""):
        s = svc(request)
        playlist = get_playlist_or_404(s, playlist_id)
        entries = s.library.entries(playlist_id)
        in_playlist = {t.id for _, t in entries}
        return templates.TemplateResponse(request, "playlist.html", {
            "playlist": playlist, "entries": entries,
            "available": [t for t in s.library.tracks() if t.id not in in_playlist],
            "used_by": [a for a in s.db.alarms() if a.playlist_uri == local_playlist_uri(playlist_id)],
            "msg": msg, "error": error,
        })

    @app.post("/music/playlists/{playlist_id}/rename")
    def rename_playlist(request: Request, playlist_id: int, name: str = Form("")):
        s = svc(request)
        get_playlist_or_404(s, playlist_id)
        s.library.rename_playlist(playlist_id, name)
        for alarm in s.db.alarms():  # keep the name shown on the alarm list in step
            if alarm.playlist_uri == local_playlist_uri(playlist_id):
                alarm.playlist_name = s.library.get_playlist(playlist_id).name
                s.db.save_alarm(alarm)
        return _back(f"/music/playlists/{playlist_id}")

    @app.post("/music/playlists/{playlist_id}/delete")
    def delete_playlist(request: Request, playlist_id: int):
        svc(request).library.delete_playlist(playlist_id)
        return _back("/music")

    @app.post("/music/playlists/{playlist_id}/add")
    def add_to_playlist(request: Request, playlist_id: int, track_ids: List[int] = Form([])):
        s = svc(request)
        get_playlist_or_404(s, playlist_id)
        s.library.add_to_playlist(playlist_id, track_ids)
        return _back(f"/music/playlists/{playlist_id}")

    @app.post("/music/playlists/{playlist_id}/entries/{entry_id}/remove")
    def remove_entry(request: Request, playlist_id: int, entry_id: int):
        svc(request).library.remove_entry(entry_id)
        return _back(f"/music/playlists/{playlist_id}")

    @app.post("/music/playlists/{playlist_id}/entries/{entry_id}/{direction}")
    def move_entry(request: Request, playlist_id: int, entry_id: int, direction: str):
        if direction not in ("up", "down"):
            raise HTTPException(404)
        svc(request).library.move_entry(entry_id, -1 if direction == "up" else 1)
        return _back(f"/music/playlists/{playlist_id}")

    # --- settings ---------------------------------------------------------------
    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request, msg: str = ""):
        s = svc(request)
        return templates.TemplateResponse(request, "settings.html", {
            "settings": s.settings, "spotify_enabled": s.spotify_enabled,
            "spotify_configured": s.spotify.configured,
            "spotify_authorized": s.spotify_enabled and s.spotify.authorized, "msg": msg,
        })

    @app.post("/settings/spotify")
    def save_spotify(
        request: Request,
        enabled: bool = Form(False),
        client_id: str = Form(""),
        client_secret: str = Form(""),
        redirect_uri: str = Form(""),
        device_name: str = Form(""),
    ):
        svc(request).save_spotify_settings(
            enabled, spotify_client_id=client_id, spotify_client_secret=client_secret,
            spotify_redirect_uri=redirect_uri, spotify_device_name=device_name)
        return _back("/settings", msg="Saved")

    @app.post("/settings/spotify/disconnect")
    def disconnect_spotify(request: Request):
        svc(request).spotify.disconnect()
        return _back("/settings", msg="Spotify disconnected")

    # --- spotify auth ---------------------------------------------------------
    @app.get("/login")
    def login(request: Request):
        return RedirectResponse(svc(request).spotify.authorize_url())

    @app.get("/callback")
    def callback(request: Request, code: Optional[str] = None, error: Optional[str] = None):
        s = svc(request)
        if code and not error:
            try:
                s.spotify.handle_callback(code)
                return _back("/settings", msg="Spotify connected")
            except Exception as e:
                log.warning("Spotify token exchange failed: %s", e)
                error = str(e)
        log.warning("Spotify authorisation failed: %s", error)
        return templates.TemplateResponse(request, "auth_error.html", {
            "error": error or "no code returned", "redirect_uri": s.settings.spotify_redirect_uri,
        }, status_code=400)

    return app


def _normalise_spotify_uri(value: str) -> str:
    """Accept open.spotify.com links as well as spotify: URIs."""
    value = value.strip()
    if value.startswith("https://open.spotify.com/"):
        parts = value.split("?")[0].rstrip("/").split("/")
        # handles /playlist/ID and /intl-xx/playlist/ID
        if len(parts) >= 2:
            return f"spotify:{parts[-2]}:{parts[-1]}"
    return value


app = create_app()
