#!/usr/bin/env python3
"""给容器里的 Chrome 送文件用的最小 HTTP 接口。

**为什么需要它**：用 CDP 远程控制这个容器里的 Chrome 时，`upload`（DOM.setFileInputFiles）
只把**路径字符串**交给浏览器，文件内容由**浏览器所在的机器**去读。调用方那台机器上的文件
在容器里并不存在，于是页面收到 0 字节，而命令还返回成功。

所以分工是：调用方先把文件 PUT 到这里（预处理），拿到容器内的绝对路径交给自动化命令，
用完再 DELETE（后处理）。容器管自己这台机器上的文件，调用方不需要知道卷怎么挂的。

接口（都要带 `Authorization: Bearer <FILE_API_TOKEN>`）：

    PUT    /files/{jobId}/{name}   上传，响应 {"path": "/data/uploads/{jobId}/{name}", ...}
    GET    /files/{jobId}          列出这个任务的文件
    DELETE /files/{jobId}          删掉这个任务的整个目录
    GET    /healthz                存活检查，不需要令牌

环境变量见 README 的「运行变量」。没有 `FILE_API_TOKEN` 时**直接退出、不反复重启**：
接口不可用比没有认证的接口好，Chrome 与 VNC 不受影响。
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import re
import shutil
import sys
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

LOG = logging.getLogger("file-api")

#: 任务目录名只收 UUID 的标准写法 —— 目录名来自调用方，不能是随便一段路径
_JOB_ID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
#: 文件名只收一段：不带目录分隔符、不是 . 或 ..（中文名照收）
_MAX_NAME = 200
_CHUNK = 1024 * 1024
#: 清理线程多久醒一次
_SWEEP_SECONDS = 600


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    try:
        value = int(raw) if raw else default
    except ValueError:
        LOG.warning("%s=%r 不是整数，按默认值 %d", name, raw, default)
        return default
    return value if value > 0 else default


class Config:
    def __init__(self) -> None:
        self.token = os.environ.get("FILE_API_TOKEN", "").strip()
        self.root = Path(os.environ.get("FILE_API_ROOT", "/data/uploads"))
        self.port = _env_int("FILE_API_PORT", 9224)
        self.max_bytes = _env_int("FILE_API_MAX_BYTES", 20 * 1024 * 1024)
        self.ttl_seconds = _env_int("FILE_API_TTL_HOURS", 6) * 3600


class Rejected(Exception):
    """请求不合规。`status` 直接回给调用方，`reason` 写明违反了哪一条。"""

    def __init__(self, status: HTTPStatus, reason: str) -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason


def _checked_job_id(value: str) -> str:
    if not _JOB_ID.match(value):
        raise Rejected(HTTPStatus.BAD_REQUEST, "任务 ID 必须是 UUID")
    return value.lower()


def _checked_name(value: str) -> str:
    if not value or len(value) > _MAX_NAME:
        raise Rejected(HTTPStatus.BAD_REQUEST, f"文件名不能为空、最长 {_MAX_NAME}")
    if "/" in value or "\\" in value or value in (".", ".."):
        raise Rejected(HTTPStatus.BAD_REQUEST, "文件名只能是一段，不能带路径")
    return value


def _job_dir(config: Config, job_id: str) -> Path:
    return config.root / job_id


class Store:
    """`/data/uploads` 下按任务分目录。只在这棵树里增删，不跟符号链接走。"""

    def __init__(self, config: Config) -> None:
        self._config = config
        config.root.mkdir(mode=0o700, parents=True, exist_ok=True)

    def save(self, job_id: str, name: str, stream, declared: int) -> dict[str, object]:
        if declared > self._config.max_bytes:
            raise Rejected(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                f"文件最大 {self._config.max_bytes} 字节",
            )
        directory = _job_dir(self._config, job_id)
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        target = directory / name
        # O_NOFOLLOW：目标被换成符号链接时直接失败，不会写到树外面去
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW
        written = 0
        try:
            handle = os.open(target, flags, 0o600)
        except OSError as exc:
            raise Rejected(HTTPStatus.CONFLICT, "这个文件名不能写（可能是链接或目录）") from exc
        try:
            with os.fdopen(handle, "wb") as sink:
                while written < declared:
                    chunk = stream.read(min(_CHUNK, declared - written))
                    if not chunk:
                        break
                    written += len(chunk)
                    sink.write(chunk)
        except OSError:
            target.unlink(missing_ok=True)
            raise
        if written != declared:
            target.unlink(missing_ok=True)
            raise Rejected(HTTPStatus.BAD_REQUEST, "请求体比 Content-Length 短，已丢弃")
        LOG.info("收下 %s/%s（%d 字节）", job_id, name, written)
        return {"path": str(target), "name": name, "sizeBytes": written}

    def listing(self, job_id: str) -> dict[str, object]:
        directory = _job_dir(self._config, job_id)
        if not directory.is_dir():
            return {"jobId": job_id, "files": []}
        files = [
            {"name": item.name, "path": str(item), "sizeBytes": item.stat().st_size}
            for item in sorted(directory.iterdir())
            if item.is_file() and not item.is_symlink()
        ]
        return {"jobId": job_id, "files": files}

    def remove(self, job_id: str) -> dict[str, object]:
        directory = _job_dir(self._config, job_id)
        if not directory.is_dir() or directory.is_symlink():
            return {"jobId": job_id, "removed": 0}
        removed = sum(1 for item in directory.iterdir() if item.is_file())
        shutil.rmtree(directory, ignore_errors=True)
        LOG.info("删掉 %s 的目录（%d 个文件）", job_id, removed)
        return {"jobId": job_id, "removed": removed}

    def sweep(self) -> int:
        """清掉过期的任务目录。

        调用方本该在任务结束时 DELETE，但它可能崩了、可能被杀 ——
        清理的兜底必须在容器这边，否则磁盘只会一直涨。
        """
        deadline = time.time() - self._config.ttl_seconds
        cleared = 0
        for entry in self._config.root.iterdir():
            if not entry.is_dir() or entry.is_symlink() or not _JOB_ID.match(entry.name):
                continue
            if entry.stat().st_mtime < deadline:
                shutil.rmtree(entry, ignore_errors=True)
                cleared += 1
        if cleared:
            LOG.info("清掉 %d 个过期任务目录", cleared)
        return cleared


class Handler(BaseHTTPRequestHandler):
    server_version = "chrome-file-api/1.0"
    config: Config
    store: Store

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 的约定
        if self.path == "/healthz":
            self._reply(HTTPStatus.OK, {"status": "ok"})
            return
        self._guarded(self._get_files)

    def do_PUT(self) -> None:  # noqa: N802
        self._guarded(self._put_file)

    def do_DELETE(self) -> None:  # noqa: N802
        self._guarded(self._delete_files)

    # ── 各接口 ───────────────────────────────────────────────────────────────

    def _put_file(self) -> None:
        parts = self._segments(expected=3)
        job_id, name = _checked_job_id(parts[1]), _checked_name(parts[2])
        raw = self.headers.get("Content-Length", "").strip()
        if not raw.isdigit():
            raise Rejected(HTTPStatus.LENGTH_REQUIRED, "需要 Content-Length")
        declared = int(raw)
        self._reply(HTTPStatus.CREATED, self.store.save(job_id, name, self.rfile, declared))

    def _get_files(self) -> None:
        parts = self._segments(expected=2)
        self._reply(HTTPStatus.OK, self.store.listing(_checked_job_id(parts[1])))

    def _delete_files(self) -> None:
        parts = self._segments(expected=2)
        self._reply(HTTPStatus.OK, self.store.remove(_checked_job_id(parts[1])))

    # ── 公共 ─────────────────────────────────────────────────────────────────

    def _segments(self, expected: int) -> list[str]:
        parts = [segment for segment in self.path.split("?")[0].split("/") if segment]
        if len(parts) != expected or parts[0] != "files":
            raise Rejected(HTTPStatus.NOT_FOUND, "只有 /files/{jobId}[/{name}] 与 /healthz")
        return parts

    def _authorize(self) -> None:
        header = self.headers.get("Authorization", "")
        prefix = "Bearer "
        given = header[len(prefix) :] if header.startswith(prefix) else ""
        if not hmac.compare_digest(given, self.config.token):
            raise Rejected(HTTPStatus.UNAUTHORIZED, "令牌不对")

    def _guarded(self, action) -> None:
        try:
            self._authorize()
            action()
        except Rejected as rejected:
            self._reply(rejected.status, {"error": rejected.reason})
        except OSError as exc:
            LOG.warning("%s %s 失败：%s", self.command, self.path, exc)
            self._reply(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "写入失败"})

    def _reply(self, status: HTTPStatus, body: dict[str, object]) -> None:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - 覆盖基类签名
        LOG.info("%s - %s", self.address_string(), format % args)


def _sweeper(store: Store) -> threading.Thread:
    def loop() -> None:
        while True:
            try:
                store.sweep()
            except OSError as exc:  # noqa: PERF203 - 清理出错不该让线程死掉
                LOG.warning("清理过期目录出错：%s", exc)
            time.sleep(_SWEEP_SECONDS)

    thread = threading.Thread(target=loop, name="sweeper", daemon=True)
    thread.start()
    return thread


def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    config = Config()
    if not config.token:
        LOG.error("没有设置 FILE_API_TOKEN：不启动文件接口（Chrome 与 VNC 不受影响）")
        return 1
    store = Store(config)
    store.sweep()
    _sweeper(store)
    Handler.config = config
    Handler.store = store
    server = ThreadingHTTPServer(("0.0.0.0", config.port), Handler)  # noqa: S104
    LOG.info(
        "文件接口监听 %d，根目录 %s，单文件上限 %d 字节，过期 %d 小时",
        config.port,
        config.root,
        config.max_bytes,
        config.ttl_seconds // 3600,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        LOG.info("收到中断，退出")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
