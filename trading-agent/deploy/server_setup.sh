#!/usr/bin/env bash
# One-time server setup (Ubuntu 24.04). Run as root on the server, from /opt/trading-agent:
#   bash deploy/server_setup.sh
# Safe to run again after an update: it re-installs requirements and restarts the services.
set -euo pipefail
APP=/opt/trading-agent
cd "$APP"

echo "==> System packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip unattended-upgrades ufw >/dev/null
timedatectl set-timezone UTC

echo "==> 2 GB swap file, so a small (2 GB RAM) server never runs out of memory"
if ! swapon --show | grep -q /swapfile; then
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile
  grep -q /swapfile /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

echo "==> Firewall: only SSH is open (the browser interface is reached through an SSH tunnel)"
ufw allow OpenSSH >/dev/null
ufw --force enable >/dev/null

echo "==> Dedicated user without login rights"
id trader >/dev/null 2>&1 || useradd --system --home "$APP" --shell /usr/sbin/nologin trader

echo "==> Python environment"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt
mkdir -p data
chown -R trader:trader "$APP"
chmod 600 .env

echo "==> Services"
cp deploy/trading-watcher.service deploy/trading-ui.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now trading-watcher trading-ui >/dev/null 2>&1
systemctl restart trading-watcher trading-ui

sleep 5
echo
systemctl --no-pager --lines=0 status trading-watcher | head -3
systemctl --no-pager --lines=0 status trading-ui | head -3
echo
echo "Done. A Telegram message saying the watcher is running should arrive in a few seconds."
