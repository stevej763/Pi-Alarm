# Radio Alarm

A Spotify wake-up alarm for a Raspberry Pi with a [Pimoroni Pirate Audio Speaker](https://shop.pimoroni.com/products/pirate-audio-mini-speaker).
When an alarm goes off it starts a playlist quietly on shuffle and fades it up, like a radio coming on.

- Web UI to manage alarms (days, playlist, fade time, volumes), test them, snooze/stop, and pause all alarms
- 240×240 screen: clock and next alarm, album art and track while ringing, snooze countdown; dims at night
- Buttons: A snooze · B stop (hold = pause all alarms) · X/Y volume
- Falls back to a local chime if Spotify can't be reached, so the alarm always sounds

## How it works
`raspotify` (librespot) makes the Pi a Spotify Connect speaker. At alarm time this app (FastAPI + APScheduler) tells the
Spotify Web API to play the playlist on that device, then fades the volume locally through an ALSA softvol control.
Requires Spotify Premium.

## Development (macOS/Linux, no Pi needed)
```sh
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
cp .env.example .env            # add Spotify credentials to test real playback
.venv/bin/python -m app         # http://127.0.0.1:8550
.venv/bin/python -m pytest
```
Without Pi hardware the screen is rendered to `data/display.png` and shown live in the web UI. To test playback before
you have the Pi, set `SPOTIFY_DEVICE_NAME` to another Spotify Connect device (e.g. your Mac's name in the Spotify app).
The volume fade only affects real hardware (`AUDIO_BACKEND=alsa`). On the Mac it is simulated.

## Pi installation
On a fresh Raspberry Pi OS Lite install, run this over SSH as your normal user:
```sh
curl -fsSL https://raw.githubusercontent.com/stevej763/Pi-Alarm/main/deploy/setup-pi.sh -o /tmp/setup-pi.sh && REPO_URL=https://github.com/stevej763/Pi-Alarm.git bash /tmp/setup-pi.sh
```
It installs everything, sets up the Pirate Audio, raspotify and the systemd service, asks for your Spotify app
credentials, and then reboots. It's safe to re-run. Afterwards, finish with steps 7–8 of [deploy/SETUP.md](deploy/SETUP.md),
which also covers each step manually.
