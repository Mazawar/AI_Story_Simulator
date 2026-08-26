# AI Story Simulator · AI 剧情模拟器

单机版 Python 剧情模拟游戏：**内置本地小模型，断网可玩**；以"剧本包"（游戏主持人系统提示词）为核心内容单元；叙事与面板全程可交互；支持投喂长篇小说按规范+质量门生成剧本包；SQLite 存储、多槽位存档；联网时可切换任意 OpenAI 兼容 API。

完整设计见 [DESIGN.md](DESIGN.md)。

## 当前状态

**阶段 1 · 直通模式界面化**（见 DESIGN.md §13 路线图）：

- [x] 工程结构 + CLI 入口
- [x] SQLite 数据层（全量参数绑定，含跨线程串行化）+ v1 迁移（18 张表）
- [x] 剧本包加载器：编码检测 + 九章节切分（对 `script/` 三个剧本包实测通过）
- [x] AI 后端抽象：本地 llama.cpp / 在线 OpenAI 兼容（含 SSRF 防护）/ 演练 Canned，统一接口 + 路由
- [x] **FastAPI 本地服务**：REST + SSE（token 鉴权、事件总线、流式推送、静态托管）
- [x] **React + Vite + Ant Design 前端**：剧本架、游戏视图（叙事块渲染：旁白/对话/播报条/选项卡）、触发词 chips、SSE 流式输出
- [x] 叙事解析器：LLM 文本 → 渲染块（`> **X：**`对话 / `【…｜…】`播报条 / `【A】`选项）
- [x] 桌面壳：`story-sim play`（pywebview WebView2，缺失回退浏览器）
- [ ] 角色创建向导 / 实体链接 Inspector 卡（阶段 1 余项）
- [ ] 引擎模式：数值/面板/锚点/章节结算（阶段 2）
- [ ] 小说拆解 + 质量门（阶段 3）
- [ ] PyInstaller 打包 + 安装器（阶段 4）

## 快速开始

```bash
# 1. 安装（骨架零依赖；按需装 extras）
pip install -e ".[server]"        # FastAPI 本地服务（界面所需）
pip install -e ".[local]"         # 本地推理（llama-cpp-python）
pip install -e ".[desktop]"       # 桌面窗口壳（pywebview）
pip install -e ".[dev]"           # 测试

# 2. 下载 GGUF 模型放入 models/（详见 models/README.md）
#    推荐 Qwen3-1.7B-Instruct Q4_K_M（主力）、Qwen3-0.6B（快速档）

# 3. 构建前端（首次）
cd web && npm install && npm run build && cd ..

# 4. 初始化数据库
story-sim migrate

# 5. 开玩（成品形态：打开即是窗口）
story-sim play                     # 无模型时加 --dry-run 演练
#    或浏览器形态：story-sim serve --dev --dry-run

# ---- 前端开发模式（热更新）----
# 终端1: story-sim serve --dev --dry-run
# 终端2: cd web && npm run dev     # Vite 5173，/api 自动代理到 8765

# ---- CLI 直通模式（无界面调试）----
story-sim packs list
story-sim demo --pack 凡人 --dry-run

# ---- 在线 API（任意 OpenAI 兼容接口）----
story-sim play --api-base https://api.example.com/v1 --api-key sk-xxx --api-model some-model
```

游戏内触发词：`存档`、`读取存档`、`修士`、`任务`、`提示`、`本章结束`（游戏视图有快捷 chips）；CLI 退出输入 `quit`。

## 目录结构

```
app/
├─ main.py        # CLI 入口：migrate / packs / demo / serve / play
├─ config.py      # 路径与常量
├─ core/          # 游戏引擎：回合循环、状态、触发词、存档
├─ render/        # 渲染契约：TurnPayload + 叙事解析器（LLM 文本→渲染块）
├─ pack/          # 剧本包加载：编码检测、章节切分
├─ ai/            # LLMBackend 抽象：local(llama.cpp) / remote(OpenAI兼容) / canned + 路由
├─ server/        # FastAPI 本地服务：token 鉴权、REST+SSE 路由、会话事件总线、静态托管
├─ ingest/        # 小说拆解流水线（阶段3）
└─ db/            # SQLite：连接（跨线程串行化）、迁移、DAO（内联字面量 SQL）
web/              # React + Vite + AntD 前端（构建产物 web/dist/ 随包分发）
models/           # GGUF/ONNX 模型文件（不入库）
data/             # story_simulator.db（运行时生成，不入库）
script/           # 剧本包素材
tests/            # 单元测试（31 项，stdlib unittest 零依赖可跑）
```

## 设计要点

1. **骨架与血肉分离**：数值/面板/锚点/触发词/存档由引擎代码强制执行，LLM 只产出叙事与结构化裁决——本地小模型可行的前提。
2. **面板即组件**：引擎与 UI 只传 TurnPayload（结构化 JSON），叙事内实体链接、面板组件化、动效仪式感（阶段 1 落地）。
3. **生成必有门**：小说拆解走 Pack Spec + G1~G4 质量门 + 溯源强制 + 人工复核，不达标不能进入可玩列表（阶段 3 落地）。
4. **数据层只用参数绑定**：所有 SQL 参数化查询，连接封装内置防拼接守卫。
