# build/ · 打包

阶段 4 落地（见 DESIGN.md §12）：

- PyInstaller **onedir** spec（含 WebEngine 冒烟验证）
- Inno Setup 安装器脚本（模型作为可选组件：精简版/完整版）
- 产物输出到 `dist/`，中间产物 `build_work/`（均不入库）
