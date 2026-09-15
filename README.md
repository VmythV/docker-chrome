# Docker Chrome：CDP 自动化与 noVNC 桌面

在容器中运行带图形界面的 Google Chrome。程序可以通过 CDP 控制浏览器，人可以通过 noVNC 网页或 VNC 客户端查看、操作同一个桌面。

镜像基于 `debian:bookworm-slim`，使用 Google Chrome 官方 amd64 软件包，目标平台为 **`linux/amd64`**，支持在 Mac M1 / Apple Silicon 上通过模拟构建和运行。

## 环境要求

- Docker 服务已启动，并提供 Docker Compose v2 和 Buildx。
- Apple Silicon 上的 Docker 环境支持 amd64 模拟，例如已启用相应支持的 Docker Desktop 或 OrbStack。模拟运行通常比原生 amd64 慢。
- 构建时能够访问 Debian 软件源及 Google Chrome 软件源。
- 运行项目中的验证脚本时需要宿主机安装 Node.js 22+；只构建、启动容器不需要 Node.js。

## 目录结构

```
.
├── Dockerfile            # 镜像依赖、字体、构建时密码
├── docker-compose.yml    # 架构、端口、运行变量、数据卷
├── supervisord.conf      # 管理 D-Bus、桌面、Chrome 和 CDP 转发进程
├── start-chrome.sh       # Chrome 启动参数
├── start-vnc.sh          # 根据镜像内的密码文件选择 VNC 认证方式
├── test-connect.js       # Playwright 连接示例
└── test-container.js     # CDP 转发和 noVNC 认证检查
```

## 快速启动：Docker Compose

在当前项目目录执行：

```bash
docker compose up -d --build
```

该命令构建 `playwright-chrome:latest`，并在后台启动名为 `playwright-chrome` 的容器。Compose 已指定 `linux/amd64`，M1 上无需另加平台参数。默认 noVNC / VNC 免密；设置密码见下文“给 VNC 加密码”。

启动几秒后检查状态和 CDP 接口：

```bash
docker compose ps
curl --fail --silent --show-error http://127.0.0.1:9222/json/version
```

接口返回包含 `Browser` 和 `webSocketDebuggerUrl` 的 JSON，即可继续使用。图形界面地址为 <http://127.0.0.1:6080/vnc.html>。

## 构建 linux/amd64 镜像

只构建镜像，不启动容器：

```bash
docker compose build chrome
```

不使用 Compose 时，必须显式指定目标平台；`--load` 将镜像加载到本机 Docker：

```bash
docker buildx build --platform linux/amd64 --load -t playwright-chrome:latest .
```

检查生成的镜像架构，输出应为 `linux/amd64`：

```bash
docker image inspect playwright-chrome:latest --format '{{.Os}}/{{.Architecture}}'
```

Dockerfile 使用 Google Chrome 的 amd64 软件源，因此不要在 M1 上直接运行未指定平台的 `docker build .`。M1 构建和运行 amd64 程序需要模拟，速度可能比原生 amd64 机器慢。

构建完成后，用现有镜像启动 Compose 服务：

```bash
docker compose up -d --no-build
```

## 使用 docker run 启动

这是 Compose 的替代启动方式。构建镜像后执行以下完整命令；两种方式不要同时运行，以免容器名或宿主机端口冲突。

```bash
docker run -d \
  --name playwright-chrome \
  --hostname playwright-chrome \
  --platform linux/amd64 \
  --shm-size=2g \
  --restart unless-stopped \
  -p 9222:9223 \
  -p 6080:6080 \
  -p 5900:5900 \
  -v chrome-profile:/data/chrome-profile \
  -e SCREEN_WIDTH=1920 \
  -e SCREEN_HEIGHT=1080 \
  -e SCREEN_DEPTH=24 \
  playwright-chrome:latest
```

此示例使用名为 `chrome-profile` 的数据卷，Docker 会自动创建它。Compose 的卷名默认带项目名前缀，例如本目录下通常是 `docker-chrome_chrome-profile`；切换启动方式时，指定原来的卷名才能复用数据。

直接用 `docker run` 启动时，可按需执行以下管理命令：

```bash
docker logs -f playwright-chrome
docker stop playwright-chrome
docker start playwright-chrome
```

## 访问方式

| 宿主机端口 → 容器端口 | 用途 | 本机访问地址 |
|---|---|---|
| `9222 → 9223` | CDP 自动化接口，供 Playwright 等程序控制 Chrome | `http://127.0.0.1:9222` |
| `6080 → 6080` | noVNC 网页，浏览器中查看和操作桌面 | <http://127.0.0.1:6080/vnc.html> |
| `5900 → 5900` | 原生 VNC，使用 VNC 客户端连接 | 在客户端输入 `127.0.0.1:5900` |

`6080` 和 `5900` 访问同一个桌面，并共用 VNC 密码。原生 VNC 端口不能直接用 HTTP 浏览器打开。其他机器访问时，将 `127.0.0.1` 替换为容器宿主机的 IP。

Chrome 的 CDP 实际监听容器内的回环地址。镜像内置 `socat` 转发 HTTP 和 WebSocket，由 Supervisor 启动并在异常退出后自动重启：

```text
外部客户端 → 宿主机:9222 → 容器 socat 0.0.0.0:9223 → Chrome 127.0.0.1:9222
```

外部客户端继续使用 `http://宿主机IP:9222`。直接使用 `docker run` 时应映射 `-p 9222:9223`，不要映射到容器内部的 `9222`。

只需要网页查看桌面时，可以删除宿主机的 `5900:5900` 映射；容器内的 5900 服务仍供 noVNC 使用。

当前 Compose 将端口发布到宿主机所有网卡。只需本机访问时，可将映射分别改为 `127.0.0.1:9222:9223`、`127.0.0.1:6080:6080`、`127.0.0.1:5900:5900`。CDP 没有密码认证，应通过受控网络访问。

## Playwright 连接示例

```bash
npm install playwright
node test-connect.js
```

代码里核心就一行：

```js
const browser = await chromium.connectOverCDP('http://localhost:9222');
```

`test-connect.js` 会新建页面、打开 `https://example.com` 并打印标题，可以同时从 noVNC 观察操作。它会保留连接；需要结束示例脚本时按 `Ctrl+C`。这里只连接容器里已有的 Chrome，无需另外运行 `playwright install`。接口说明见 [Playwright connectOverCDP](https://playwright.dev/docs/api/class-browsertype#browser-type-connect-over-cdp)。

## 运行时配置

在 `docker-compose.yml` 的 `services.chrome.environment` 中设置，或在 `docker run` 的镜像名之前通过 `-e 变量=值` 传入：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `SCREEN_WIDTH` | `1920` | 虚拟屏幕和 Chrome 窗口宽度 |
| `SCREEN_HEIGHT` | `1080` | 虚拟屏幕和 Chrome 窗口高度 |
| `SCREEN_DEPTH` | `24` | 虚拟屏幕色深 |
| `EXTRA_CHROME_FLAGS` | 空 | 追加 Chrome 参数，如 `--lang=zh-CN`；多个参数用空格分隔 |
| `CHROME_USER_DATA_DIR` | `/data/chrome-profile` | Chrome 用户数据目录；修改时同时调整卷挂载的容器路径 |
| `CDP_PORT` | `9222` | Chrome 内部 CDP 监听端口，通常无需修改 |
| `CDP_PROXY_PORT` | `9223` | 容器内转发器监听端口；修改时同步调整端口映射右侧 |
| `NOVNC_PORT` | `6080` | 容器内 noVNC 网页端口；修改时同步调整端口映射右侧 |
| `VNC_PORT` | `5900` | 容器内原生 VNC 端口；noVNC 自动使用此值，原生客户端的端口映射也需同步调整 |
| `DISPLAY` | `:99` | 虚拟显示编号，桌面和 Chrome 共用，通常无需修改 |

例如，修改现有 `environment` 配置为：

```yaml
environment:
  SCREEN_WIDTH: "1280"
  SCREEN_HEIGHT: "720"
  SCREEN_DEPTH: "24"
  EXTRA_CHROME_FLAGS: "--lang=zh-CN"
```

修改运行变量或端口映射后，执行 `docker compose up -d` 让 Compose 重建容器。`docker compose restart` 不会应用修改后的环境变量或端口配置。

端口映射左侧是宿主机端口，右侧是容器端口。例如宿主机 `9222` 已被占用，可改成 `19222:9223`，客户端连接 `http://127.0.0.1:19222`；这种修改不需要改内部的 `CDP_PORT` 或 `CDP_PROXY_PORT`。

Chrome 和转发器是两个监听进程，`CDP_PORT` 与 `CDP_PROXY_PORT` 必须使用不同端口。

Compose 已设置 `shm_size: "2gb"`，对应 `docker run --shm-size=2g`。`.env` 中的变量只有被 Compose 配置引用才会生效；本项目目前只引用其中的 `VNC_PASSWORD`，屏幕配置请按上面的方式设置。

## 持久化 Chrome 用户数据

`chrome-profile` 是具名 volume，挂在 `/data/chrome-profile`，保存登录状态、Cookie、扩展及其他浏览器配置。正常重启或重建容器时会复用该数据卷；`docker compose down` 默认也保留它。

**`docker compose down -v` 会删除本项目的数据卷及其中的浏览器数据。** 保留登录状态时不要使用 `-v`。Dockerfile 自身声明了 `VOLUME`，仅删除 Compose 中的卷配置仍可能产生匿名卷，不能把它当作可靠的数据清理方式。

Compose 固定了 `hostname: playwright-chrome`，使 Chrome 在重建容器后能识别并处理本实例遗留的进程锁。直接使用 `docker run` 并复用数据卷时，也应保持 `--hostname playwright-chrome` 不变。一个用户数据目录同一时间只能供一个 Chrome 实例使用。

## 给 VNC 加密码：构建时配置

通过构建变量 `VNC_PASSWORD` 设置，noVNC 和原生 VNC 客户端共用这个密码；未设置或为空时免密。密码支持 1–8 个可打印 ASCII 字符，超长或含中文、换行等字符会让构建失败，避免 VNC 静默截断密码。

构建并启动带密码的镜像：

```bash
VNC_PASSWORD='secret12' docker compose up -d --build
```

需要恢复免密时，显式清空密码并重新构建：

```bash
VNC_PASSWORD= docker compose up -d --build
```

也可以在项目的 `.env` 中设置 `VNC_PASSWORD=secret12`，之后执行 `docker compose up -d --build`。仅运行 `docker compose restart` 不会更新镜像中的密码。

例如创建 `.env`，后续构建会沿用此设置：

```dotenv
VNC_PASSWORD='secret12'
```

示例密码请替换为自己的值。项目的 `.gitignore` 已排除 `.env`。**`VNC_PASSWORD` 是构建参数；启动时使用 `docker run -e VNC_PASSWORD=...` 不会修改镜像内的密码。**

直接使用 Buildx 时：

```bash
VNC_PASSWORD='secret12' docker buildx build --platform linux/amd64 --load \
  --build-arg VNC_PASSWORD -t playwright-chrome:latest .
```

密码会固化到镜像，构建参数也可能留在构建记录中；不要公开带真实密码的镜像。此密码只保护 VNC 连接，CDP 转发端口没有认证。

## 日常管理

以下命令适用于 Compose 启动的服务，按需执行：

```bash
# 查看状态和最近日志
docker compose ps
docker compose logs --tail 100 chrome

# 持续查看日志，按 Ctrl+C 结束查看
docker compose logs -f chrome

# 停止服务，保留容器及数据
docker compose stop chrome

# 启动服务，并应用 Compose 配置变化
docker compose up -d

# 重新构建镜像并更新容器
docker compose up -d --build

# 删除容器和项目网络，保留具名数据卷
docker compose down
```

修改 Dockerfile 或启动脚本后需要重新构建。设置过构建密码时，在后续构建中保持相同的 `VNC_PASSWORD`，可通过 `.env` 保存它。

## 验证

启动完成后，使用 Node.js 22+ 运行，无需安装 npm 依赖：

```bash
# 免密镜像
VNC_PASSWORD= node test-container.js

# 有密码镜像：测试值必须与构建值一致
VNC_PASSWORD='secret12' node test-container.js
```

脚本检查宿主机上的 CDP HTTP / WebSocket 连通性，以及 noVNC 的免密或正确密码登录、错误密码拒绝。端口或主机不同可设置 `CDP_URL` 和 `NOVNC_URL`。

例如将宿主机 CDP 端口改成 `19222` 后：

```bash
CDP_URL=http://127.0.0.1:19222 \
NOVNC_URL=http://127.0.0.1:6080/ \
VNC_PASSWORD= node test-container.js
```

这里的 `VNC_PASSWORD` 是测试客户端尝试登录使用的密码，也不会改变镜像配置。

## 网页字体

镜像已安装 `fonts-liberation` 和 [`fonts-noto-cjk`](https://packages.debian.org/bookworm/fonts-noto-cjk)，覆盖常用拉丁字母以及中日韩文字；中文、英语、西班牙语网页通常可以正常显示。具体页面仍取决于文字、字体和网页自身的编码。

当前 Dockerfile 未安装完整的多文字字体集。如果目标网页使用印地语、孟加拉语、泰米尔语等文字，可在 Dockerfile 的 `apt-get install` 列表中增加 [`fonts-noto-core`](https://packages.debian.org/bookworm/fonts-noto-core)；需要彩色表情时可增加 [`fonts-noto-color-emoji`](https://packages.debian.org/bookworm/fonts-noto-color-emoji)，然后重新构建镜像。

`--lang=zh-CN` 设置浏览器语言偏好，不会安装字体，也不会自动翻译网页。

## 排查

- 顶部出现“由自动化测试软件控制”是 `--enable-automation` 的正常提示。当前容器以 root 运行并使用 `--no-sandbox`，Chrome 自身沙箱仍未启用。
- 容器起不来先看日志：`docker compose logs -f chrome`
- D-Bus 的 `Unknown address type` / `system_bus_socket: No such file or directory`：镜像已由 Supervisor 管理会话总线和系统总线，并为 Chrome 设置会话总线地址；旧容器需运行 `docker compose up -d --build` 更新。Fluxbox 的标题栏默认配置也已补齐，其余未配置项的 `Setting default value` 表示使用默认值。
- `UPower` 的 `ServiceUnknown`：容器未安装电源管理服务，通常不影响网页显示或 CDP。
- `Created TensorFlow Lite XNNPACK delegate for CPU`：Chrome 内部推理组件的正常初始化信息，无需修改。
- GCM 的 `DEPRECATED_ENDPOINT`：Google 推送服务返回的注册错误；普通网页显示和 CDP 通常不受影响。若依赖网站或扩展的推送通知，需要单独排查该功能。
- Chrome 崩溃或白屏：先检查 Chrome 的退出日志、容器内存和可用磁盘空间；不要仅凭界面现象判断原因。
- 9222 连不上：确认端口映射为 `9222:9223`，查看 `docker compose logs chrome` 中的 `cdp-proxy` 日志；可用 `curl http://localhost:9222/json/version` 检查 HTTP 接口。不要依赖 `--remote-debugging-address=0.0.0.0`，当前 Chrome 仍只监听回环地址。
- 9222 返回 `ERR_EMPTY_RESPONSE` / `Empty reply from server`：查看 Chrome 是否启动失败。如果日志提示 `profile appears to be in use`，通常是旧容器遗留的 `SingletonLock` 记录了不同主机名。停止服务并确认没有其他实例使用该数据卷后，才能移除用户数据目录下的 `SingletonLock`、`SingletonCookie`、`SingletonSocket` 三个锁链接，再启动容器；保留整个用户数据目录。
- 构建或运行时报架构相关错误：确认 Compose 保留 `platform: linux/amd64`，直接使用 Buildx / `docker run` 时带 `--platform linux/amd64`，并确认 M1 上的 Docker 环境支持 amd64 模拟。
