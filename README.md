# AI Story Simulator · AI 剧情模拟器

单机版 Python 剧情模拟游戏：**内置本地小模型，断网可玩**；以"剧本包"（游戏主持人系统提示词）为核心内容单元；叙事与面板全程可交互；支持投喂长篇小说按规范+质量门生成剧本包；SQLite 存储、多槽位存档；联网时可切换任意 OpenAI 兼容 API。

完整设计见 [DESIGN.md](DESIGN.md)。

## 当前状态

**阶段 1 完成 · 可玩成品**（见 DESIGN.md §13 路线图）：

- [x] SQLite 数据层（参数绑定 + 跨线程串行化）+ v1 迁移（18 张表）
- [x] 剧本包加载器（`script/` 三个包实测通过）+ FastAPI 本地服务（REST+SSE，token 鉴权）
- [x] React + Vite + AntD 前端（水墨暗色主题）：剧本架 / 游戏视图（叙事块渲染、选项卡、触发词 chips、流式输出、开场自动触发）
- [x] 本地推理链路：Qwen3-1.7B（GGUF Q4_K_M）+ 思考块剥离 + KV 量化缓存 + flash attention
- [x] **四五万字剧本包支持**：按剧本体量自动选上下文档位（32k 原生 → 64k/96k/128k YaRN 扩展），超长会话自动滑窗
- [x] EXE 打包（PyInstaller onedir）：`dist/AIStorySimulator/AIStorySimulator.exe` 双击即玩
- [x] 模型下载：`story-sim models fetch qwen3-1.7b`（HF + 国内镜像双源，断点续传）
- [x] 角色创建向导（剧本包首轮分步选择 → 原生 UI）/ 实体链接 Inspector 卡（角色名可点击）
- [x] 续玩入口：剧本架「未竟之局」卡片，存档快照恢复（无存档时从回合历史重建）
- [x] **引擎模式（阶段 2）✅ 全部完成**：数值守恒（白名单裁决 + 防刷子衰减 + 单笔钳制 + 时间流）、面板真数据（修士面板/身份线任务面板/章节结算）、锚点系统（条件 DSL + 剧透隔离 + 截断抢救三重兜底）、上下文组装器（首回合 14s）、滚动摘要、GBNF 约束生成
- [ ] 小说拆解 + 质量门（阶段 3）

## 成品使用（重要）

**打包产物**（gitignore，不进仓库树）：`dist/AIStorySimulator/`

```
dist/
└─ AIStorySimulator/
   ├─ AIStorySimulator.exe      ← 双击开玩（窗口即游戏）
   ├─ models/    ← 模型（qwen3-1.7b-instruct-q4_k_m.gguf 约 1.1GB）
   ├─ script/    ← 剧本包（凡人修仙传/剑来/完美世界，可自行增删 .txt）
   └─ data/      ← 存档数据库 + 运行日志（自动生成）
```

- 模型与剧本包**紧邻 EXE、用户可见可替换**：放入新 `.txt` 剧本包即出现在剧本架；放入新 `.gguf` 自动按偏好选用。
- **首次推演约 2-3 分钟**（本地模型需处理整卷剧本提示词，一次性成本），之后每回合数秒——界面上有"命运正在推演"等待动效。
- 分发：整个 `dist/AIStorySimulator/` 目录拷给对方即可（或压缩为 zip）；无需 Python/Node。

## 开发（uv 工作流）

```bash
# 环境搭建（uv 自动创建 .venv 并装齐依赖）
uv sync --all-extras

# 模型下载（HF + hf-mirror 双源，断点续传；或手动放 .gguf 到 models/）
uv run story-sim models fetch qwen3-1.7b

# 数据库迁移
uv run story-sim migrate

# 开发调试
uv run story-sim packs list                    # 剧本包切分检查
uv run story-sim demo --pack 凡人              # CLI 直通模式
uv run story-sim serve --dev --dry-run         # 本地服务（固定 token=dev）
uv run python -m unittest discover -s tests    # 测试（110 项）

# 前端开发（热更新；另开终端跑 serve --dev）
cd web && npm install && npm run dev           # Vite 5173，/api 代理到 8765

# 一键打包 EXE（前端需先 npm run build）
uv run python build/make_exe.py   # 产物在 dist/AIStorySimulator/

# 在线 API（任意 OpenAI 兼容接口；Ollama 等本机端点加 --allow-private-api）
uv run story-sim demo --pack 凡人 --api-base https://api.xx.com/v1 --api-key sk-xxx --api-model xxx
```

游戏内触发词：`存档`、`读取存档`、`修士`、`任务`、`提示`、`本章结束`（游戏视图有快捷 chips）；CLI 退出输入 `quit`。

## 目录结构

```
app/
├─ main.py        # CLI 入口：migrate / packs / demo / serve / play / models
├─ config.py      # 路径与常量（冻结/开发双形态感知）
├─ launcher.py    # 桌面启动器（EXE 入口：起服务 → 开窗口，关窗即退）
├─ core/          # 游戏引擎：回合循环、状态、触发词、滑窗裁剪、存档
├─ render/        # 渲染契约：TurnPayload + 叙事解析器（LLM 文本→渲染块）
├─ pack/          # 剧本包加载：编码检测、章节切分
├─ ai/            # LLM 后端：local（YaRN/KV量化/思考剥离）/ remote / canned
│  │              #   + 路由 + 模型下载器（models fetch）
├─ server/        # FastAPI 本地服务：token 鉴权、REST+SSE、事件总线、静态托管
├─ ingest/        # 小说拆解流水线（阶段3）
└─ db/            # SQLite：连接（跨线程串行化）、迁移、DAO（内联字面量 SQL）
web/              # React + Vite + AntD 前端（构建产物 web/dist/ 随包分发）
build/            # PyInstaller spec + make_exe.py 一键打包
models/           # GGUF 模型（gitignore，用 models fetch 下载）
data/             # story_simulator.db（运行时生成）
script/           # 剧本包素材（系统提示词文档，非小说）
tests/            # 单元测试（110 项）
dist/             # 打包产物（gitignore，双击 dist/AIStorySimulator/AIStorySimulator.exe 即玩）
```

## 设计要点

1. **骨架与血肉分离**：数值/面板/锚点/触发词/存档由引擎代码强制执行，LLM 只产出叙事与结构化裁决——本地小模型可行的前提。
2. **面板即组件**：引擎与 UI 只传 TurnPayload（结构化 JSON），叙事内实体链接、面板组件化、动效仪式感（阶段 1 落地）。
3. **生成必有门**：小说拆解走 Pack Spec + G1~G4 质量门 + 溯源强制 + 人工复核，不达标不能进入可玩列表（阶段 3 落地）。
4. **数据层只用参数绑定**：所有 SQL 参数化查询，连接封装内置防拼接守卫。
