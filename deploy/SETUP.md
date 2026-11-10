# Raspberry Pi setup

Hardware: Raspberry Pi Zero 2 W (or Pi 3/4/5) + Pimoroni Pirate Audio Speaker (PIM485).

## 1. OS
1. Flash **Raspberry Pi OS Lite (64-bit)** with Raspberry Pi Imager. In the Imager settings, set the hostname (e.g. `radio-alarm`), username, Wi-Fi, **timezone**, and enable SSH.
2. Boot, then `ssh <user>@radio-alarm.local`.
3. Check the clock. The Pi has no clock battery and relies on NTP:
   ```sh
   timedatectl          # "System clock synchronized: yes" and the correct Time zone
   sudo timedatectl set-timezone Europe/London   # if needed
   ```
4. `sudo apt update && sudo apt full-upgrade -y`

## 2. Pirate Audio audio + display
```sh
sudo nano /boot/firmware/config.txt      # append deploy/config.txt.snippet, set dtparam=audio=off
sudo cp ~/radio-alarm/deploy/asound.conf /etc/asound.conf
sudo reboot
```
After the reboot:
```sh
aplay -l                                  # should list "sndrpihifiberry"
speaker-test -c2 -t wav -l1               # speaks "front left / front right" (also creates the Master control)
amixer sset Master 40%                    # should change the loudness
```
The softvol `Master` control only appears after something has played through the `default` device once. Run `speaker-test` before `amixer`.

Steps 3, 4 and 7 are only needed for Spotify. Without it, upload MP3s on the web UI's **Music** page.

## 3. Spotify Connect (raspotify)
```sh
sudo apt install -y curl
curl -sL https://dtcooper.github.io/raspotify/install.sh | sh
sudo nano /etc/raspotify/conf             # apply the settings in deploy/raspotify.conf
sudo systemctl restart raspotify
```
Open Spotify on your phone (same Wi-Fi), tap the devices icon and pick **Radio Alarm** once. Play something to confirm audio. This logs librespot in and caches the credentials, so the device is available to the Web API after every reboot.

## 4. Spotify developer app
1. Go to <https://developer.spotify.com/dashboard> → **Create app**.
2. Redirect URI: `http://127.0.0.1:8550/callback` (Spotify only accepts HTTPS or loopback addresses; `localhost` is rejected).
3. API: **Web API**. Note the Client ID and Client Secret.
4. Development-mode apps only work for the owner and allow-listed users. That's fine for personal use.

## 5. Install the app
```sh
sudo apt install -y git mpg123 python3-venv python3-dev python3-lgpio python3-gpiozero python3-spidev python3-pil fonts-dejavu-core
git clone <your repo url> ~/radio-alarm
cd ~/radio-alarm
python3 -m venv --system-site-packages .venv   # reuse apt's GPIO libraries
.venv/bin/pip install -e '.[pi]'
cp .env.example .env && nano .env              # set HARDWARE=pi (Spotify credentials can go in the web UI)
```

## 6. Run it as a service
```sh
sudo cp deploy/radio-alarm.service /etc/systemd/system/
sudo nano /etc/systemd/system/radio-alarm.service   # set User/Group/paths if your user isn't "pi"
sudo systemctl daemon-reload
sudo systemctl enable --now radio-alarm
journalctl -u radio-alarm -f
```
The screen should show the clock.

## 7. Connect Spotify (one time)
The redirect has to land on `127.0.0.1`, so open the UI through an SSH tunnel from your laptop:
```sh
ssh -L 8550:127.0.0.1:8550 <user>@radio-alarm.local
```
Browse to <http://127.0.0.1:8550/settings>, tick **Use Spotify**, enter the Client ID/Secret if they aren't in `.env`, save, then click **Connect Spotify** and approve. The token is saved to `data/spotify-token.json` and refreshes automatically. After that you can use `http://radio-alarm.local:8550` from any device on your network.

## 8. Try it
Upload some MP3s on the **Music** page (optionally group them into a playlist), create an alarm, press **Test now**: the playlist should start quietly and fade up.
Buttons: **A** snooze · **B** stop (hold 2 s to pause/resume all alarms) · **X/Y** volume.

## Troubleshooting
- **"Spotify device 'Radio Alarm' not found"**: check `systemctl status raspotify`, and that `SPOTIFY_DEVICE_NAME` matches `LIBRESPOT_NAME`. Select it once from the Spotify app.
- **Backup chime plays instead of music**: the web UI shows the reason. For Spotify, usually the network is down or the token was revoked (reconnect via step 7). For on-device playlists: the playlist is empty, or `mpg123` isn't installed.
- **Button Y does nothing**: older Pirate Audio boards use GPIO20. Set `BUTTON_Y_PIN=20` in `.env`.
- **Blank display**: confirm `/boot/firmware/config.txt` has `dtoverlay=spi0-2cs,no_miso` (and no `dtparam=spi=on`) and that `/dev/spidev0.1` exists. Check the log for "Display init failed". "GPIO9 currently claimed" means SPI is still holding the display's DC pin as MISO.
- **`amixer: Unable to find simple control 'Master'`**: play something through `default` first (see step 2).
