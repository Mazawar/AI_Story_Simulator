"""桌面启动器：启动本地服务 → 打开游戏窗口（pywebview 原生窗口 → 系统浏览器）。

- 开发：story-sim play（main.py 调用 launch()）
- 打包：EXE 入口直接调用 launch()，关窗即退出
"""

from __future__ import annotations

import logging
import secrets
import socket
import sys
import threading
import time
import webbrowser

from . import config


class _NullStream:
    """无窗口打包后 sys.stdout/stderr 为 None，库触碰 .isatty() 即崩
    （曾致 uvicorn 日志配置失败、EXE 启动即退）。用内存空流兜底。"""

    def write(self, *_args) -> int:
        return 0

    def flush(self) -> None:
        pass

    def isatty(self) -> bool:
        return False

    def close(self) -> None:
        pass


if sys.stdout is None:
    sys.stdout = _NullStream()
if sys.stderr is None:
    sys.stderr = _NullStream()


def _log() -> logging.Logger:
    logger = logging.getLogger("launcher")
    if not logger.handlers:
        config.ensure_runtime_dirs()
        handler = logging.FileHandler(config.DATA_DIR / "launcher.log", encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger


def _pick_port(preferred: int = 8765) -> int:
    for port in range(preferred, preferred + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("找不到可用端口（8765-8784 均被占用）")


def _start_server(port: int, token: str, dry_run: bool):
    import uvicorn

    from .server import create_app

    app = create_app(token=token, dry_run=dry_run)
    # log_config=None：跳过 uvicorn 自带 logging 配置（其流处理器依赖真实 stdout；
    # 错误经 root logger 仍会进 data/play_errors.log / launcher.log）
    conf = uvicorn.Config(app, host="127.0.0.1", port=port,
                          log_level="error", log_config=None)
    server = uvicorn.Server(conf)
    threading.Thread(target=server.run, daemon=True).start()
    return server


def _wait_started(server, timeout: float = 30.0) -> bool:
    """等待 uvicorn 启动完成（server.started 由 uvicorn 在启动结束时置位）。"""
    deadline = time.time() + timeout
    while time.time() < deadline and not server.started:
        time.sleep(0.1)
    return server.started


def _open_window(url: str) -> bool:
    """优先 pywebview 原生窗口（关窗可感知）；否则系统浏览器。返回是否为原生窗口。"""
    try:
        import webview  # pywebview（pyproject [desktop] extra）

        window = webview.create_window(
            config.APP_NAME, url, width=1280, height=820, min_size=(960, 640),
        )
        # 显式传图标：任务栏/标题栏用的是窗口 Icon（与 EXE 文件图标是两回事）；
        # 不传时 winforms 从 EXE 提取图标的回退路径有 64 位句柄截断问题
        icon_arg = str(config.ICON_PATH) if config.ICON_PATH.is_file() else None
        _log().info("窗口图标：%s", icon_arg or "未找到（回退默认）")
        webview.start(icon=icon_arg)
        return True
    except TypeError:
        # 旧版 pywebview 无 icon 参数
        try:
            webview.start()
            return True
        except ImportError:
            pass
        except Exception as e:
            _log().warning("pywebview 启动失败，回退浏览器窗口：%s", e)
    except ImportError:
        pass
    except Exception as e:
        _log().warning("pywebview 启动失败，回退浏览器窗口：%s", e)

    webbrowser.open(url)
    return False


def launch(dry_run: bool = False) -> int:
    log = _log()
    log.info("启动 %s v%s（frozen=%s）", config.APP_NAME, config.APP_VERSION, config.FROZEN)

    token = secrets.token_urlsafe(24)
    port = _pick_port()
    server = _start_server(port, token, dry_run)

    if not _wait_started(server):
        log.error("本地服务启动超时")
        print("本地服务启动失败，详见 data/launcher.log")
        return 1

    url = f"http://127.0.0.1:{port}/?token={token}"
    log.info("本地服务就绪 %s", url)

    native = _open_window(url)
    if native:
        # 原生窗口关闭 → 退出整个程序
        server.should_exit = True
        log.info("窗口关闭，退出")
        return 0

    # 浏览器模式：主进程驻留，Ctrl+C 退出
    print(f"游戏已在浏览器中打开：{url}")
    print("关闭此窗口（或 Ctrl+C）退出游戏。")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        server.should_exit = True
        return 0
