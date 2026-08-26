# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec：onedir 模式，产物 dist/AIStorySimulator/
# 用法：pyinstaller --noconfirm build/AIStorySimulator.spec（或 python build/make_exe.py）

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

REPO = Path(SPECPATH).resolve().parent          # 项目根
WEB_DIST = REPO / "web" / "dist"

hiddenimports = [
    # uvicorn 按需导入的子模块（PyInstaller 静态分析不可见）
    "uvicorn.logging", "uvicorn.loops", "uvicorn.loops.auto", "uvicorn.loops.asyncio",
    "uvicorn.protocols", "uvicorn.protocols.http", "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl", "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto", "uvicorn.lifespan", "uvicorn.lifespan.on",
    # 本地推理（app/ai/local.py 内延迟导入）
    "llama_cpp",
    "app", "app.ai", "app.ai.local", "app.core", "app.db", "app.pack", "app.render",
    "app.server", "app.ingest",
]

datas = []
if WEB_DIST.is_dir():
    # 前端静态产物 → _internal/web/dist（运行时 config.WEB_DIST_DIR 会找到这里）
    for p in WEB_DIST.rglob("*"):
        if p.is_file():
            datas.append((str(p), str(p.parent.relative_to(REPO))))
else:
    raise SystemExit("web/dist 不存在：请先 cd web && npm install && npm run build")

# llama-cpp-python 经 ctypes 从包内 lib/ 目录加载 DLL（导入图不可见，必须按数据收集，
# 保持 llama_cpp/lib 目录结构；冻结后 __file__ 指向 _internal/llama_cpp/）
datas += collect_data_files("llama_cpp", include_py_files=False)

# 窗口图标：launcher 经 config.ICON_PATH 加载（Windows 任务栏/标题栏用窗口 HICON，
# 与 EXE 文件图标是两回事；pywebview start(icon=) 走 winforms self.Icon）
if (REPO / "assets" / "icon.ico").is_file():
    datas.append((str(REPO / "assets" / "icon.ico"), "assets"))

a = Analysis(
    [str(REPO / "run_launcher.py")],
    pathex=[str(REPO)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "pytest", "PySide6"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    exclude_binaries=True,
    name="AIStorySimulator",
    debug=False,
    console=False,            # 双击 EXE 无黑框，日志写 data/launcher.log
    icon=str(REPO / "assets" / "icon.ico"),
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="AIStorySimulator",
)
