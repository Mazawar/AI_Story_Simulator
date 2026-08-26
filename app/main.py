"""CLI 入口：migrate / packs / demo / serve / play。

- serve：启动本地服务（开发用，--dev 固定 token 方便 Vite 代理）
- play：启动服务并打开桌面窗口（pywebview，缺失时回退系统浏览器）
"""

from __future__ import annotations

import argparse
import secrets
import sys
import threading
import webbrowser

from . import config
from .ai import resolve_backend
from .core.engine import DirectEngine
from .db import Database, migrate
from .db import dao
from .pack import load_pack, load_packs


def _stdout_utf8() -> None:
    # Windows 控制台默认 GBK，输出中文叙事前统一切 UTF-8
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass


def _open_db() -> Database:
    config.ensure_runtime_dirs()
    db = Database(config.DB_PATH)
    migrate(db)
    return db


# ---- 子命令 -----------------------------------------------------------------


def cmd_migrate(_args) -> int:
    db = _open_db()
    version = db.schema_version()
    print(f"数据库就绪：{config.DB_PATH}（schema v{version}）")
    db.close()
    return 0


def cmd_packs(args) -> int:
    packs = load_packs(config.SCRIPT_DIR)
    if not packs:
        print(f"未找到剧本包（{config.SCRIPT_DIR} 下无 .txt/.md）")
        return 1

    if args.action == "list":
        print(f"剧本包目录：{config.SCRIPT_DIR}\n")
        for p in packs:
            print(f"  {p.title:<16} {len(p.sections)} 个章节  {len(p.raw_text)} 字  ({p.file_path})")
        return 0

    # show：按名称子串匹配
    matches = [p for p in packs if args.name in p.title]
    if not matches:
        print(f"未找到匹配「{args.name}」的剧本包")
        return 1
    for p in matches:
        print(f"=== {p.title} ===")
        for s in p.sections:
            preview = s.body.strip().replace("\n", " ")[:60]
            print(f"  [{s.num}] {s.title:<10} key={s.key:<14} {s.char_count()}字")
            if preview:
                print(f"        {preview}…")
        print()
    return 0


def cmd_demo(args) -> int:
    db = _open_db()
    try:
        packs = load_packs(config.SCRIPT_DIR)
        if not packs:
            print(f"未找到剧本包（{config.SCRIPT_DIR}）")
            return 1
        pack = next((p for p in packs if args.pack in p.title), packs[0])

        # 入库并建立对局
        pack_id = dao.packs.upsert_pack(db, pack)
        story_id = dao.packs.get_story_for_pack(db, pack_id, pack.title)
        playthrough_id = dao.plays.create_playthrough(db, story_id, mode="direct")

        backend = resolve_backend(
            db, dry_run=args.dry_run,
            api_base=args.api_base, api_key=args.api_key, api_model=args.api_model,
        )
        engine = DirectEngine(db, backend, pack, playthrough_id)

        print("=" * 56)
        print(f"  剧本：{pack.title}")
        print(f"  后端：{backend.name}   对局 #{playthrough_id}")
        print(f"  触发词：{' / '.join(('存档', '读取存档', '修士', '任务', '提示'))} …")
        print(f"  退出：quit")
        print("=" * 56)

        while True:
            try:
                user_input = input("\n你> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not user_input:
                continue
            if user_input.lower() in ("quit", "exit", "q"):
                break
            try:
                payload = engine.handle(user_input)
            except RuntimeError as e:
                print(f"[错误] {e}")
                continue

            if payload.system_note:
                print(f"\n[系统] {payload.system_note}")
            for block in payload.narrative:
                print(f"\n{block.get('text', '')}")

        print("再见。")
        return 0
    finally:
        db.close()


# ---- 本地服务 / 桌面窗口 ------------------------------------------------------


def _make_server(host: str, port: int, token: str, dry_run: bool):
    import uvicorn
    from .server import create_app

    app = create_app(token=token, dry_run=dry_run)
    config_ = uvicorn.Config(app, host=host, port=port, log_level="warning")
    return uvicorn.Server(config_)


def cmd_serve(args) -> int:
    _stdout_utf8()
    token = "dev" if args.dev else secrets.token_urlsafe(24)
    server = _make_server(args.host, args.port, token, args.dry_run)
    url = f"http://{args.host}:{args.port}/?token={token}"
    print(f"本地服务：{url}")
    print("  前端：发布模式自动托管 web/dist/；开发模式另开终端执行 cd web && npm run dev")
    if not args.no_browser:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    server.run()
    return 0


def cmd_play(args) -> int:
    from .launcher import launch

    return launch(dry_run=args.dry_run)


def cmd_models(args) -> int:
    from .ai import downloader

    if args.action == "list":
        entries = downloader.scan(config.MODELS_DIR)
        if not entries:
            print(f"models/ 目录为空（{config.MODELS_DIR}）")
            print("下载主力模型：story-sim models fetch qwen3-1.7b")
            return 0
        for e in entries:
            size_mb = e["size"] / 1024 / 1024
            role = "★ 主力档" if e["rank"] < 3 else ("快速档" if e["rank"] < 6 else "增强/其他")
            print(f"  {e['file']:<44} {size_mb:7.1f} MB  {role}")
        return 0

    if args.action == "presets":
        for key, p in downloader.PRESETS.items():
            print(f"  {key:<12} {p['desc']}")
        return 0

    # fetch
    try:
        downloader.fetch(args.source, config.MODELS_DIR, name=args.name)
        return 0
    except (ValueError, RuntimeError) as e:
        print(f"下载失败：{e}")
        return 1


# ---- 入口 -------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    _stdout_utf8()
    parser = argparse.ArgumentParser(
        prog="story-sim", description="AI 剧情模拟器"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("migrate", help="初始化/升级数据库")

    p_packs = sub.add_parser("packs", help="剧本包管理")
    p_packs_sub = p_packs.add_subparsers(dest="action", required=True)
    p_packs_sub.add_parser("list", help="列出 script/ 下剧本包")
    p_show = p_packs_sub.add_parser("show", help="查看章节切分")
    p_show.add_argument("name", help="剧本包名称（子串匹配）")

    p_demo = sub.add_parser("demo", help="直通模式开局（CLI）")
    p_demo.add_argument("--pack", default="", help="剧本包名称（子串匹配，默认第一个）")
    p_demo.add_argument("--dry-run", action="store_true", help="演练后端（无需模型）")
    p_demo.add_argument("--api-base", help="OpenAI 兼容 API 地址（如 https://api.xx.com/v1）")
    p_demo.add_argument("--api-key", help="API 密钥")
    p_demo.add_argument("--api-model", help="模型名")
    p_demo.add_argument("--allow-private-api", action="store_true",
                        help="允许内网/本机 API 端点（Ollama/LM Studio）")

    p_play = sub.add_parser("play", help="启动桌面窗口（成品形态）")
    p_play.add_argument("--dry-run", action="store_true", help="演练后端（无需模型）")

    p_models = sub.add_parser("models", help="本地模型管理")
    p_models_sub = p_models.add_subparsers(dest="action", required=True)
    p_models_sub.add_parser("list", help="扫描 models/ 显示可用模型")
    p_models_sub.add_parser("presets", help="列出预置模型源")
    p_fetch = p_models_sub.add_parser("fetch", help="下载模型")
    p_fetch.add_argument("source", help="预置名（如 qwen3-1.7b）或 GGUF 直链 URL")
    p_fetch.add_argument("--name", help="自定义 URL 时指定保存文件名（须 .gguf 结尾）")

    for name in ("serve",):
        p = sub.add_parser(name, help="启动本地服务（开发）")
        p.add_argument("--port", type=int, default=8765)
        p.add_argument("--dry-run", action="store_true", help="演练后端（无需模型）")
        p.add_argument("--host", default="127.0.0.1")
        p.add_argument("--dev", action="store_true",
                       help="开发模式：固定 token=dev，供 Vite 代理使用")
        p.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")

    args = parser.parse_args(argv)

    if args.command == "migrate":
        return cmd_migrate(args)
    if args.command == "packs":
        args.action = getattr(args, "action", "list")
        return cmd_packs(args)
    if args.command == "demo":
        return cmd_demo(args)
    if args.command == "serve":
        return cmd_serve(args)
    if args.command == "play":
        return cmd_play(args)
    if args.command == "models":
        return cmd_models(args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
