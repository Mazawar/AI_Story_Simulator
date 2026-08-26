# AI Story Simulator · AI 剧情模拟器

单机版 Python 剧情模拟游戏：**内置本地小模型，断网可玩**；以"剧本包"（游戏主持人系统提示词）为核心内容单元；叙事与面板全程可交互；支持投喂长篇小说按规范+质量门生成剧本包；SQLite 存储、多槽位存档；联网时可切换任意 OpenAI 兼容 API。

完整设计见 [DESIGN.md](DESIGN.md)。

## 当前状态

**阶段 0 · 骨架**（见 DESIGN.md §13 路线图）：

- [x] 工程结构 + CLI 入口
- [x] SQLite 数据层（全量参数绑定，含防拼接守卫）+ v1 迁移（18 张表）
- [x] 剧本包加载器：编码检测 + 九章节切分（对 `script/` 三个剧本包实测通过）
- [x] AI 后端抽象：本地 llama.cpp / 在线 OpenAI 兼容 / 演练用 Canned，统一接口 + 路由
- [x] 直通模式闭环：加载剧本包 → 对话回合 → 触发词拦截（「存档」等）→ 回合与存档落库
- [ ] WebEngine 交互游戏视图（阶段 1）
- [ ] 引擎模式：数值/面板/锚点/角色创建向导（阶段 2）
- [ ] 小说拆解 + 质量门（阶段 3）
- [ ] PyInstaller 打包 + 安装器（阶段 4）

## 快速开始

```bash
# 1. 安装（骨架零依赖；要跑本地模型装 local 扩展）
pip install -e .
pip install -e ".[local]"        # 本地推理（llama-cpp-python）

# 2. 下载 GGUF 模型放入 models/（详见 models/README.md）
#    推荐 Qwen3-1.7B-Instruct Q4_K_M（主力）、Qwen3-0.6B（快速档）

# 3. 初始化数据库
story-sim migrate

# 4. 查看剧本包
story-sim packs list
story-sim packs show 凡人

# 5. 开玩（直通模式，本地模型）
story-sim demo --pack 凡人

# 无模型时的流水线演练（Canned 后端，验证管线不通模型也能跑通）
story-sim demo --pack 剑来 --dry-run

# 在线 API（任意 OpenAI 兼容接口）
story-sim demo --pack 完美 --api-base https://api.example.com/v1 --api-key sk-xxx --model some-model
```

游戏内触发词（直通模式已拦截）：`存档`、`读取存档`、`修士`、`任务`、`提示`、`降级面板`、`恢复面板`、`本章结束`；退出输入 `quit`。

## 目录结构

```
app/
├─ main.py        # CLI 入口：migrate / packs / demo
├─ config.py      # 路径与常量
├─ core/          # 游戏引擎：回合循环、状态、触发词、存档
├─ render/        # 渲染契约（TurnPayload）：结构化回合数据，阶段1起供 WebEngine 消费
├─ pack/          # 剧本包加载：编码检测、章节切分
├─ ai/            # LLMBackend 抽象：local(llama.cpp) / remote(OpenAI兼容) / canned + 路由
├─ ingest/        # 小说拆解流水线（阶段3）
├─ db/            # SQLite：连接封装（强制参数绑定）、迁移、DAO
└─ ui/            # 桌面界面（阶段1：Widgets 外壳 + WebEngine 游戏视图）
assets/web/       # 游戏视图前端资源（阶段1）
models/           # GGUF/ONNX 模型文件（不入库）
data/             # story_simulator.db（运行时生成，不入库）
script/           # 剧本包素材
tests/            # 单元测试（stdlib unittest，零依赖可跑）
```

## 设计要点

1. **骨架与血肉分离**：数值/面板/锚点/触发词/存档由引擎代码强制执行，LLM 只产出叙事与结构化裁决——本地小模型可行的前提。
2. **面板即组件**：引擎与 UI 只传 TurnPayload（结构化 JSON），叙事内实体链接、面板组件化、动效仪式感（阶段 1 落地）。
3. **生成必有门**：小说拆解走 Pack Spec + G1~G4 质量门 + 溯源强制 + 人工复核，不达标不能进入可玩列表（阶段 3 落地）。
4. **数据层只用参数绑定**：所有 SQL 参数化查询，连接封装内置防拼接守卫。
