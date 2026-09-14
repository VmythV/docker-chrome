#!/bin/bash
set -e

sleep 2

auth=(-nopw)
if [ -e /etc/x11vnc.passwd ]; then
  auth=(-rfbauth /etc/x11vnc.passwd)
fi

exec x11vnc \
  -display "${DISPLAY}" \
  -forever -shared \
  -rfbport "${VNC_PORT}" \
  "${auth[@]}" \
  -xkb -noxrecord -noxfixes -noxdamage
