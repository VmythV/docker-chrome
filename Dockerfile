FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive \
    DISPLAY=:99 \
    SCREEN_WIDTH=1920 \
    SCREEN_HEIGHT=1080 \
    SCREEN_DEPTH=24 \
    CHROME_USER_DATA_DIR=/data/chrome-profile \
    NOVNC_PORT=6080 \
    VNC_PORT=5900 \
    CDP_PORT=9222 \
    CDP_PROXY_PORT=9223

# 基础依赖 + Google Chrome 官方源
RUN apt-get update && apt-get install -y --no-install-recommends \
        wget gnupg ca-certificates curl \
    && wget -q -O - https://dl.google.com/linux/linux_signing_key.pub \
        | gpg --dearmor -o /usr/share/keyrings/google-chrome.gpg \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome.gpg] http://dl.google.com/linux/chrome/deb/ stable main" \
        > /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update && apt-get install -y --no-install-recommends \
        google-chrome-stable \
        xvfb \
        x11vnc \
        fluxbox \
        supervisor \
        socat \
        novnc \
        websockify \
        fonts-liberation \
        fonts-noto-cjk \
        dumb-init \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# 让 http://host:6080/ 直接打开 noVNC 页面
RUN ln -s /usr/share/novnc/vnc.html /usr/share/novnc/index.html

RUN mkdir -p ${CHROME_USER_DATA_DIR}

COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf
COPY start-chrome.sh start-vnc.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/start-chrome.sh /usr/local/bin/start-vnc.sh

# 按需将 VNC 密码固化到镜像；不设置时保持免密。
ARG VNC_PASSWORD=""
RUN set -eu; export LC_ALL=C; \
    if [ "${#VNC_PASSWORD}" -gt 8 ]; then \
        echo 'VNC_PASSWORD must be at most 8 ASCII characters' >&2; exit 1; \
    fi; \
    case "$VNC_PASSWORD" in \
        *[![:print:]]*) echo 'VNC_PASSWORD must contain printable ASCII characters only' >&2; exit 1 ;; \
    esac; \
    if [ -n "$VNC_PASSWORD" ]; then \
        x11vnc -storepasswd "$VNC_PASSWORD" /etc/x11vnc.passwd; \
        chmod 600 /etc/x11vnc.passwd; \
    fi

EXPOSE 9223 5900 6080

VOLUME ["/data/chrome-profile"]

ENTRYPOINT ["dumb-init", "--"]
CMD ["/usr/bin/supervisord", "-n", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
