# Playwright + Chrome (CDP) + noVNC Docker

一个开箱即用的 headed Chrome 容器：Playwright 可以通过 CDP 直连自动化，同时人可以用浏览器打开 noVNC 网页实时看/接管操作。

## 目录结构

```
.
├── Dockerfile
├── docker-compose.yml
├── supervisord.conf     # 管理桌面、Chrome 和 CDP 转发进程
├── start-chrome.sh       # Chrome 启动参数
├── start-vnc.sh          # 根据镜像内的密码文件选择 VNC 认证方式
├── test-connect.js       # Playwright 连接示例
└── test-container.js     # CDP 转发和 noVNC 认证检查
```

## 启动

Compose 已指定 `linux/amd64`，在 Mac M1 / Apple Silicon 上也会构建并运行 amd64 镜像。请先启动支持 amd64 模拟的 Docker 环境（如 Docker Desktop 或 OrbStack）。

```bash
docker compose up -d --build
```

## 在 Mac M1 上构建 linux/amd64 镜像

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

## 访问方式

| 用途 | 地址 |
|---|---|
| Playwright CDP 连接 | `http://localhost:9222` |
| 网页看画面 (noVNC) | `http://localhost:6080/vnc.html`（默认免密，设置密码后连接时输入） |
| 原生 VNC 客户端 | `localhost:5900` |

Chrome 的 CDP 实际监听容器内的回环地址。镜像内置 `socat` 转发 HTTP 和 WebSocket，由 Supervisor 启动并在异常退出后自动重启：

```text
外部客户端 → 宿主机:9222 → 容器 socat 0.0.0.0:9223 → Chrome 127.0.0.1:9222
```

外部客户端继续使用 `http://宿主机IP:9222`。直接使用 `docker run` 时应映射 `-p 9222:9223`，不要映射到容器内部的 `9222`。

## Playwright 连接示例

```bash
npm install playwright
node test-connect.js
```

代码里核心就一行：

```js
const browser = await chromium.connectOverCDP('http://localhost:9222');
```

## 常用配置

在 `docker-compose.yml` 的 `environment` 里改：

- `SCREEN_WIDTH` / `SCREEN_HEIGHT` / `SCREEN_DEPTH`：虚拟屏幕分辨率
- `EXTRA_CHROME_FLAGS`：追加 Chrome 启动参数，比如 `--lang=zh-CN`

## 持久化 Chrome 用户数据

`chrome-profile` 是具名 volume，挂在 `/data/chrome-profile`，登录状态、Cookie、扩展等都会保留在里面，容器重启不丢失。想要每次都是干净环境，把 `docker-compose.yml` 里 `volumes` 那两行删掉即可。

## 给 VNC 加密码（可选）

通过构建变量 `VNC_PASSWORD` 设置，noVNC 和原生 VNC 客户端共用这个密码；未设置或为空时免密。密码支持 1–8 个可打印 ASCII 字符，超长或含中文、换行等字符会让构建失败，避免 VNC 静默截断密码。

```bash
# 设置密码并重新构建、启动
VNC_PASSWORD='secret12' docker compose up -d --build

# 显式清空密码并重新构建，恢复免密
VNC_PASSWORD= docker compose up -d --build
```

也可以在项目的 `.env` 中设置 `VNC_PASSWORD=secret12`，之后执行 `docker compose up -d --build`。仅运行 `docker compose restart` 不会更新镜像中的密码。

直接使用 Buildx 时：

```bash
VNC_PASSWORD='secret12' docker buildx build --platform linux/amd64 --load \
  --build-arg VNC_PASSWORD -t playwright-chrome:latest .
```

密码会固化到镜像，构建参数也可能留在构建记录中；不要公开带真实密码的镜像。此密码只保护 VNC 连接，CDP 转发端口没有认证。

## 验证

启动完成后，使用 Node.js 22+ 运行，无需安装 npm 依赖：

```bash
# 免密镜像
VNC_PASSWORD= node test-container.js

# 有密码镜像：测试值必须与构建值一致
VNC_PASSWORD='secret12' node test-container.js
```

脚本检查宿主机上的 CDP HTTP / WebSocket 连通性，以及 noVNC 的免密或正确密码登录、错误密码拒绝。端口或主机不同可设置 `CDP_URL` 和 `NOVNC_URL`。

## 排查

- 顶部出现“由自动化测试软件控制”是 `--enable-automation` 的正常提示。当前容器以 root 运行并使用 `--no-sandbox`，Chrome 自身沙箱仍未启用。
- 容器起不来先看日志：`docker compose logs -f chrome`
- Chrome 崩溃/白屏：多半是 `shm_size` 不够，compose 里已经设了 `2gb`，页面重的话可以调大
- 9222 连不上：确认端口映射为 `9222:9223`，查看 `docker compose logs chrome` 中的 `cdp-proxy` 日志；可用 `curl http://localhost:9222/json/version` 检查 HTTP 接口。不要依赖 `--remote-debugging-address=0.0.0.0`，当前 Chrome 仍只监听回环地址。
- 想看中文网页乱码/缺字：Dockerfile 已装 `fonts-noto-cjk`，如果还有问题可以额外装 `fonts-wqy-zenhei`
