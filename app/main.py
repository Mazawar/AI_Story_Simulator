"""CLI 入口：migrate / packs / demo（阶段 0 验收闭环）。"""

from __future__ import annotations

import argparse
import sys

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
    version = migrate(db)
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
        if args.allow_private_api:
            dao.settings.set_setting(db, "api_allow_private", "1")

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


# ---- 入口 -------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    _stdout_utf8()
    parser = argparse.ArgumentParser(
        prog="story-sim", description="AI 剧情模拟器（阶段 0：直通模式 CLI）"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("migrate", help="初始化/升级数据库")

    p_packs = sub.add_parser("packs", help="剧本包管理")
    p_packs_sub = p_packs.add_subparsers(dest="action", required=True)
    p_packs_sub.add_parser("list", help="列出 script/ 下剧本包")
    p_show = p_packs_sub.add_parser("show", help="查看章节切分")
    p_show.add_argument("name", help="剧本包名称（子串匹配）")

    p_demo = sub.add_parser("demo", help="直通模式开局")
    p_demo.add_argument("--pack", default="", help="剧本包名称（子串匹配，默认第一个）")
    p_demo.add_argument("--dry-run", action="store_true", help="演练后端（无需模型）")
    p_demo.add_argument("--api-base", help="OpenAI 兼容 API 地址（如 https://api.xx.com/v1）")
    p_demo.add_argument("--api-key", help="API 密钥")
    p_demo.add_argument("--api-model", help="模型名")
    p_demo.add_argument("--allow-private-api", action="store_true",
                        help="允许内网/本机 API 端点（Ollama/LM Studio）")

    args = parser.parse_args(argv)

    if args.command == "migrate":
        return cmd_migrate(args)
    if args.command == "packs":
        args.action = getattr(args, "action", "list")
        return cmd_packs(args)
    if args.command == "demo":
        return cmd_demo(args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
