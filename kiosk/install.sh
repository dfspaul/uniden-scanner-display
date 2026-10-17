#!/bin/bash
# Turns the Pi into a touchscreen kiosk: the scanner page full screen on a Raspberry Pi Touch Display 2.
# cage (a one-app Wayland compositor) runs Chromium on tty1 at boot; no desktop is installed.
# Needs Raspberry Pi OS (Bookworm or later) and the scanner-display service from ../install.sh.
#
# On the Pi, from this folder:
#   kiosk/install.sh            packages + files; safe without the display
#   kiosk/install.sh enable     start it now and at every boot
#   kiosk/install.sh disable    back to a plain console
# Or from another computer, without a copy on the Pi:  ssh <pi> 'bash -s -- enable' < kiosk/install.sh
# Safe to re-run; change a setting below and run it again, then  sudo systemctl restart kiosk
set -euo pipefail

URL=http://localhost/
ROTATE=90        # the panel is portrait; 90 turns it to landscape (right for a SmartiPi Touch case). Upside down? use 270. 0 = portrait
SCALE=1          # Chromium zoom; the page sizes itself from the screen, so 1 should do
BLANK_MIN=0      # turn the screen off after this many idle minutes (a touch wakes it); 0 = never

case "${1:-install}" in
  enable)
    sudo systemctl enable --now kiosk.service
    echo "Kiosk enabled. Logs: journalctl -u kiosk -f"
    exit 0 ;;
  disable)
    sudo systemctl disable --now kiosk.service
    echo "Kiosk disabled; tty1 is a normal console again."
    exit 0 ;;
  install) ;;
  *) echo "usage: install | enable | disable" >&2; exit 2 ;;
esac

sudo apt-get update -q
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -q --no-install-recommends \
  cage chromium wlr-randr swayidle wlopm fonts-dejavu-core curl

# The session cage runs: rotate, set up blanking, then Chromium (cage exits when Chromium does,
# and systemd starts it all again).
sudo tee /usr/local/bin/scanner-kiosk >/dev/null <<EOF
#!/bin/sh
# Started by kiosk.service inside cage. Settings live in kiosk/install.sh in uniden-scanner-display.
out=\$(wlr-randr | awk 'NR==1 { print \$1 }')
[ $ROTATE -ne 0 ] && wlr-randr --output "\$out" --transform $ROTATE
[ $BLANK_MIN -gt 0 ] && swayidle -w timeout $((BLANK_MIN * 60)) 'wlopm --off "*"' resume 'wlopm --on "*"' &
exec chromium --kiosk --ozone-platform=wayland --incognito --noerrdialogs --no-first-run \\
  --disable-infobars --disable-session-crashed-bubble --password-store=basic \\
  --disable-pinch --overscroll-history-navigation=0 --disable-features=Translate \\
  --check-for-update-interval=31536000 --force-device-scale-factor=$SCALE \\
  "$URL"
EOF
sudo chmod 755 /usr/local/bin/scanner-kiosk

# cage turns the picture but not the touch panel, so turn its coordinates to match (libinput
# calibration matrix, normalised 0..1). 90 measured on the Touch Display 2.
case $ROTATE in
  90)  M="0 -1 1 1 0 0" ;;
  180) M="-1 0 1 0 -1 1" ;;
  270) M="0 1 0 -1 0 1" ;;
  *)   M="1 0 0 0 1 0" ;;
esac
echo "ENV{ID_INPUT_TOUCHSCREEN}==\"1\", ENV{LIBINPUT_CALIBRATION_MATRIX}=\"$M\"" \
  | sudo tee /etc/udev/rules.d/99-kiosk-touch.rules >/dev/null
sudo udevadm control --reload
sudo udevadm trigger --subsystem-match=input --action=change

# cage on tty1 as $USER with a real login session (logind gives it the seat and /run/user).
sudo tee /etc/systemd/system/kiosk.service >/dev/null <<EOF
[Unit]
Description=Scanner kiosk (Chromium full screen on the touchscreen)
After=scanner-display.service systemd-user-sessions.service plymouth-quit-wait.service
Wants=scanner-display.service
Conflicts=getty@tty1.service
After=getty@tty1.service

[Service]
User=$USER
PAMName=login
TTYPath=/dev/tty1
TTYReset=yes
TTYVHangup=yes
TTYVTDisallocate=yes
StandardInput=tty-fail
StandardOutput=journal
StandardError=journal
UtmpIdentifier=tty1
UtmpMode=user
# Wait (up to a minute) for display.py, so Chromium doesn't open on a "can't connect" page.
ExecStartPre=-/usr/bin/timeout 60 /bin/sh -c 'until curl -sf -o /dev/null ${URL}state.json; do sleep 1; done'
ExecStart=/usr/bin/cage -s -- /usr/local/bin/scanner-kiosk
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload

echo
echo "Installed. With the display connected, run the 'enable' line from the top of this script."
if [ -z "$(ls /sys/class/backlight 2>/dev/null)" ]; then
  echo "Note: no backlight found yet, which is expected until the panel is connected (it's auto-detected at boot)."
fi
