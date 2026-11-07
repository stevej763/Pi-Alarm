from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .config import Settings
from .models import DAY_NAMES, Alarm
from .services import Services

log = logging.getLogger(__name__)
HERE = Path(__file__).parent
templates = Jinja2Templates(directory=str(HERE / "templates"))


def _back() -> RedirectResponse:
    return RedirectResponse("/", status_code=303)


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

    def playlists(s: Services) -> List[dict]:
        if not s.spotify.authorized:
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
        return templates.TemplateResponse(request, "index.html", {
            "alarms": s.db.alarms(),
            "spotify_configured": s.spotify.configured,
            "spotify_authorized": s.spotify.authorized,
            "device_name": s.settings.spotify_device_name,
            **status_context(s),
        })

    @app.get("/status", response_class=HTMLResponse)
    def status_partial(request: Request):
        return templates.TemplateResponse(request, "_status.html", status_context(svc(request)))

    @app.get("/alarms/new", response_class=HTMLResponse)
    def new_alarm(request: Request):
        s = svc(request)
        return templates.TemplateResponse(request, "alarm_form.html", {
            "alarm": Alarm(), "days": DAY_NAMES, "playlists": playlists(s)})

    @app.get("/alarms/{alarm_id}/edit", response_class=HTMLResponse)
    def edit_alarm(request: Request, alarm_id: int):
        s = svc(request)
        alarm = s.db.get_alarm(alarm_id)
        if not alarm:
            raise HTTPException(404)
        return templates.TemplateResponse(request, "alarm_form.html", {
            "alarm": alarm, "days": DAY_NAMES, "playlists": playlists(s)})

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
        names = {p["uri"]: p["name"] for p in playlists(s)}
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
                return _back()
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
