#!/bin/bash
# Optional: keeps a Pi on Wi-Fi. A Pi 3B+ on Wi-Fi now and then fails to rejoin after the router
# or modem restarts and stays off the network until it's power-cycled. This adds a timer that pings
# the router every minute: after 3 misses in a row it restarts Wi-Fi, after 10 it reboots the Pi.
# It also turns Wi-Fi power saving off (it makes reconnecting less reliable on the Pi's Wi-Fi chip)
# and keeps the system log across reboots, so after an outage you can see what happened.
# Needs Raspberry Pi OS (Bookworm or later, which uses NetworkManager).
#
# On the Pi, from this folder:  netwatch/install.sh
# Or from another computer:     ssh <pi> 'bash -s' < netwatch/install.sh
# The router and Wi-Fi connection are found when you run it; to pick them yourself:
#   GATEWAY=192.168.1.1 CON="my wifi" netwatch/install.sh
# Run it again if the Pi moves to another network. Safe to re-run.
# Logs: journalctl -t netwatch    The log from before the last reboot: journalctl -b -1
# Remove: sudo systemctl disable --now netwatch.timer
set -euo pipefail

GATEWAY=${GATEWAY:-$(ip -4 route show default | awk '{ print $3; exit }')}
CON=${CON:-$(nmcli -t -f NAME,TYPE con show --active | awk -F: '$2 == "802-11-wireless" { print $1; exit }')}
BOUNCE_AT=3     # minutes without the router before restarting Wi-Fi
REBOOT_AT=10    # minutes without the router before rebooting

if [ -z "$GATEWAY" ] || [ -z "$CON" ]; then
  echo "Couldn't find the router (\"$GATEWAY\") or an active Wi-Fi connection (\"$CON\")." >&2
  echo "Run this while the Pi is on Wi-Fi, or set GATEWAY= and CON= yourself. On Ethernet you don't need it." >&2
  exit 1
fi

sudo tee /usr/local/bin/netwatch >/dev/null <<EOF
#!/bin/sh
# Run every minute by netwatch.timer. Written by netwatch/install.sh in uniden-scanner-display.
f=/run/netwatch.misses
if ping -c 3 -W 2 -q $GATEWAY >/dev/null 2>&1; then
  [ -s \$f ] && logger -t netwatch "router back after \$(cat \$f) missed checks"
  rm -f \$f; exit 0
fi
n=\$(( \$(cat \$f 2>/dev/null || echo 0) + 1 )); echo \$n > \$f
logger -t netwatch "no reply from $GATEWAY (\$n in a row)"
if [ \$n -ge $REBOOT_AT ]; then
  logger -t netwatch "rebooting"; systemctl reboot
elif [ \$n -ge $BOUNCE_AT ]; then
  logger -t netwatch "restarting Wi-Fi"
  nmcli radio wifi off; sleep 3; nmcli radio wifi on; sleep 5
  nmcli con up '$CON' >/dev/null 2>&1 || true
fi
EOF
sudo chmod 755 /usr/local/bin/netwatch

sudo tee /etc/systemd/system/netwatch.service >/dev/null <<EOF
[Unit]
Description=Check the Pi can reach the router; restart Wi-Fi or reboot if not
[Service]
Type=oneshot
ExecStart=/usr/local/bin/netwatch
EOF
sudo tee /etc/systemd/system/netwatch.timer >/dev/null <<EOF
[Unit]
Description=Run netwatch every minute
[Timer]
OnBootSec=3min
OnUnitActiveSec=1min
[Install]
WantedBy=timers.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now netwatch.timer

# 2 = power saving off. "0 (default)" leaves the Wi-Fi driver's own default, which is on.
sudo nmcli con mod "$CON" 802-11-wireless.powersave 2

# Raspberry Pi OS keeps the log in memory only, so a reboot loses it. Keep it on the card, capped.
sudo mkdir -p /etc/systemd/journald.conf.d
printf '[Journal]\nStorage=persistent\nSystemMaxUse=50M\n' \
  | sudo tee /etc/systemd/journald.conf.d/persistent.conf >/dev/null
sudo systemctl restart systemd-journald && sudo journalctl --flush

echo "netwatch $(systemctl is-active netwatch.timer): pinging $GATEWAY, Wi-Fi connection \"$CON\"."
echo "Power saving off takes effect on the next reconnect or reboot."
