#!/usr/bin/env bash
# One-shot Raspberry Pi setup for Radio Alarm (automates deploy/SETUP.md steps 1-6).
# Run as your normal user (not root) on a fresh Raspberry Pi OS Lite install. Safe to re-run.
#
# Overrides (optional, as environment variables):
#   APP_DIR    where the project lives        (default: ~/radio-alarm)
#   REPO_URL   git URL to clone if APP_DIR doesn't exist yet
#   TIMEZONE   e.g. Europe/London             (default: the Pi's current timezone)
set -euo pipefail

APP_DIR="${APP_DIR:-$HOME/radio-alarm}"
REPO_URL="${REPO_URL:-}"
TIMEZONE="${TIMEZONE:-$(timedatectl show -p Timezone --value 2>/dev/null || echo Etc/UTC)}"
RUN_USER="$(id -un)"
RUN_GROUP="$(id -gn)"
NEEDS_REBOOT=0

step() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m!!  %s\033[0m\n' "$*"; }
ask()  { local prompt="$1" var; read -r -p "$prompt" var </dev/tty; printf '%s' "$var"; }

if [ "$(id -u)" -eq 0 ]; then
    echo "Run this as your normal user (it uses sudo where needed), not as root." >&2
    exit 1
fi

step "Timezone and clock"
sudo timedatectl set-timezone "$TIMEZONE"
sudo timedatectl set-ntp true
timedatectl | grep -E 'Time zone|synchronized' || true

step "System packages"
sudo apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get full-upgrade -y
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
    git curl alsa-utils mpg123 python3-venv python3-dev python3-pip \
    python3-lgpio python3-gpiozero python3-spidev python3-pil python3-numpy \
    fonts-dejavu-core

step "Project code in $APP_DIR"
if [ -f "$APP_DIR/pyproject.toml" ]; then
    echo "Found existing checkout."
    if [ -d "$APP_DIR/.git" ] && git -C "$APP_DIR" remote get-url origin >/dev/null 2>&1; then
        git -C "$APP_DIR" pull --ff-only || warn "git pull failed; continuing with the current code."
    fi
elif [ -n "$REPO_URL" ]; then
    git clone "$REPO_URL" "$APP_DIR"
else
    cat >&2 <<EOF
No project found at $APP_DIR. Either set REPO_URL to clone it, e.g.
    REPO_URL=https://github.com/you/radio-alarm.git bash $0
or copy it from your computer first, e.g.
    rsync -av --exclude .venv --exclude data --exclude .env ./ $RUN_USER@$(hostname).local:radio-alarm/
EOF
    exit 1
fi
cd "$APP_DIR"

step "Pirate Audio: /boot config (I2S DAC, amp pin, SPI)"
BOOT_CONFIG=/boot/firmware/config.txt
[ -f "$BOOT_CONFIG" ] || BOOT_CONFIG=/boot/config.txt
sudo cp -n "$BOOT_CONFIG" "$BOOT_CONFIG.radio-alarm.bak"
while IFS= read -r line; do
    case "$line" in ''|'#'*) continue ;; esac
    if [ "$line" = "dtoverlay=spi0-2cs,no_miso" ] && grep -qE '^dtparam=spi=on' "$BOOT_CONFIG"; then
        sudo sed -i 's/^dtparam=spi=on/#dtparam=spi=on/' "$BOOT_CONFIG"
        echo "changed: dtparam=spi=on -> commented out (GPIO9 is the display DC pin)"; NEEDS_REBOOT=1
    fi
    if [ "$line" = "dtparam=audio=off" ] && grep -qE '^dtparam=audio=on' "$BOOT_CONFIG"; then
        sudo sed -i 's/^dtparam=audio=on/dtparam=audio=off/' "$BOOT_CONFIG"
        echo "changed: dtparam=audio=on -> off"; NEEDS_REBOOT=1
    elif ! grep -qxF "$line" "$BOOT_CONFIG"; then
        echo "$line" | sudo tee -a "$BOOT_CONFIG" >/dev/null
        echo "added:   $line"; NEEDS_REBOOT=1
    fi
done < deploy/config.txt.snippet
sudo cp deploy/asound.conf /etc/asound.conf

step "Spotify Connect (raspotify)"
if ! dpkg -s raspotify >/dev/null 2>&1; then
    curl -sL https://dtcooper.github.io/raspotify/install.sh | sh
fi
RASPOTIFY_CONF=/etc/raspotify/conf
sudo cp -n "$RASPOTIFY_CONF" "$RASPOTIFY_CONF.radio-alarm.bak"
grep -E '^[A-Z_]+=' deploy/raspotify.conf | while IFS= read -r line; do
    key="${line%%=*}"
    sudo sed -i -E "/^#?[[:space:]]*${key}=/d" "$RASPOTIFY_CONF"
    echo "$line" | sudo tee -a "$RASPOTIFY_CONF" >/dev/null
done
sudo systemctl enable raspotify
sudo systemctl restart raspotify || warn "raspotify didn't start yet (normal before the first reboot)."

step "Python environment"
python3 -m venv --system-site-packages .venv   # reuse apt's GPIO libraries
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e '.[pi]'

step "App settings (.env)"
[ -f .env ] || cp .env.example .env
set_env() {  # set_env KEY VALUE: replace (or uncomment) the line, else append
    if grep -qE "^#?$1=" .env; then
        sed -i -E "s|^#?$1=.*|$1=$2|" .env
    else
        echo "$1=$2" >> .env
    fi
}
current() { grep -E "^$1=" .env | head -1 | cut -d= -f2-; }
set_env HARDWARE pi
set_env AUDIO_BACKEND alsa
set_env TIMEZONE "$TIMEZONE"
if [ -z "$(current SPOTIFY_CLIENT_ID)" ]; then
    echo "Spotify is optional: alarms can play MP3s uploaded through the web UI."
    echo "Spotify app credentials (https://developer.spotify.com/dashboard; redirect URI http://127.0.0.1:8550/callback)."
    echo "Leave blank to skip, or add them later on the web UI's Settings page."
    id_in="$(ask 'SPOTIFY_CLIENT_ID: ')"
    [ -n "$id_in" ] && set_env SPOTIFY_CLIENT_ID "$id_in"
fi
if [ -z "$(current SPOTIFY_CLIENT_SECRET)" ]; then
    secret_in="$(ask 'SPOTIFY_CLIENT_SECRET: ')"
    [ -n "$secret_in" ] && set_env SPOTIFY_CLIENT_SECRET "$secret_in"
fi
chmod 600 .env

step "systemd service"
sudo usermod -aG audio,gpio,spi "$RUN_USER"
# The softvol "Master" control only exists once something has played through it, so play
# one second of silence before the app starts if it's missing.
sed -e "s|/home/pi/radio-alarm|$APP_DIR|g" \
    -e "s|^User=pi|User=$RUN_USER|" \
    -e "s|^Group=pi|Group=$RUN_GROUP|" \
    -e "/^WorkingDirectory=/a ExecStartPre=-/bin/sh -c 'amixer sget Master >/dev/null 2>&1 || aplay -q -t raw -f S16_LE -r 44100 -c 2 -d 1 /dev/zero'" \
    deploy/radio-alarm.service | sudo tee /etc/systemd/system/radio-alarm.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable radio-alarm
[ "$NEEDS_REBOOT" -eq 0 ] && sudo systemctl restart radio-alarm

step "Done"
cat <<EOF
Next (see deploy/SETUP.md for detail):
  1. Browse to http://$(hostname).local:8550, upload MP3s on the Music page and create an alarm.
  2. Optional, Spotify: open Spotify on your phone (same Wi-Fi) and pick the "Radio Alarm" device once. Then from
     your laptop run  ssh -L 8550:127.0.0.1:8550 $RUN_USER@$(hostname).local, browse to http://127.0.0.1:8550,
     turn on Spotify in Settings and click "Connect Spotify".
  3. Logs:  journalctl -u radio-alarm -f
EOF
if [ "$NEEDS_REBOOT" -eq 1 ]; then
    reply="$(ask 'A reboot is needed for the audio/display config. Reboot now? [Y/n] ')"
    case "$reply" in [nN]*) echo "Reboot later with: sudo reboot" ;; *) sudo reboot ;; esac
fi
