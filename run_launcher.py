"""PyInstaller 入口：打包后双击 EXE 即启动游戏窗口。"""

from app.launcher import launch

if __name__ == "__main__":
    raise SystemExit(launch())
