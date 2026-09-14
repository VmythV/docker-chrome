#!/bin/bash
set -e

# 等 Xvfb 先起来，避免 Chrome 抢跑
sleep 2

exec google-chrome \
  --remote-debugging-port="${CDP_PORT}" \
  --enable-automation \
  --no-sandbox \
  --disable-gpu \
  --disable-dev-shm-usage \
  --no-first-run \
  --no-default-browser-check \
  --start-maximized \
  --window-size="${SCREEN_WIDTH},${SCREEN_HEIGHT}" \
  --user-data-dir="${CHROME_USER_DATA_DIR}" \
  ${EXTRA_CHROME_FLAGS:-} \
  about:blank
