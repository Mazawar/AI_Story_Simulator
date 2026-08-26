"""一键打包：pyinstaller spec + 把 models/ 与 script/ 放到 EXE 旁边。

用法：uv run python build/make_exe.py
产物输出在**仓库外**：../AIStorySimulator-release/AIStorySimulator/
（打包产物不进仓库树，models/ script/ 紧邻 exe，用户可直接增删；data/ 运行时生成）
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DIST = REPO.parent / "AIStorySimulator-release" / "AIStorySimulator"


def link_or_copy(src: Path, dst: Path) -> None:
    """同卷优先硬链接（1GB 模型秒完成），失败回退复制。"""
    try:
        os.link(src, dst)
        print(f"  链接 {dst.name}")
    except OSError:
        shutil.copy2(src, dst)
        print(f"  复制 {dst.name}")


def main() -> int:
    web_dist = REPO / "web" / "dist"
    if not web_dist.is_dir():
        print("前端未构建：请先 cd web && npm install && npm run build")
        return 1

    print("== PyInstaller 打包 ==")
    r = subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--noconfirm",
         "--distpath", str(REPO.parent / "AIStorySimulator-release"),
         str(REPO / "build" / "AIStorySimulator.spec")],
        cwd=str(REPO),
    )
    if r.returncode != 0:
        return r.returncode

    print("== 布置 models/ 与 script/（紧邻 EXE，用户可见可替换） ==")
    for folder in ("models", "script"):
        src = REPO / folder
        dst = DIST / folder
        dst.mkdir(parents=True, exist_ok=True)
        for p in sorted(src.iterdir()):
            if p.is_file() and p.suffix.lower() in (".gguf", ".onnx", ".txt", ".md"):
                target = dst / p.name
                if target.exists():
                    target.unlink()
                link_or_copy(p, target)

    print(f"\n打包完成：{DIST / 'AIStorySimulator.exe'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
