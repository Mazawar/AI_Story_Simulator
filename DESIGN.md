# AI 剧情模拟器 · 系统设计文档

> 单机版 Python 剧情模拟游戏：内置本地小模型，离线可玩；以"剧本包"为核心内容单元；**面板与叙事全程可交互**；投喂小说按规范+质量门生成剧本包；SQLite 存储；支持游戏存档；联网时可切换第三方 API。

---

## 1. 产品概览

**一句话定位**：一个"文字冒险 + AI 主持"的单机游戏。玩家创建角色进入剧本世界，确定性引擎管规则与数值，AI 负责叙事与裁决；**叙事文本与面板本身就是游戏 UI，一切皆可交互**。

**两类内容输入**（严格区分，走不同流水线）：

| 类型 | 形态 | 来源 | 处理方式 |
|---|---|---|---|
| **剧本包（Scenario Pack）** | 已整理好的世界观/规则/角色/锚点文档，本质是"游戏主持人系统提示词"（1~2万字） | `script/` 下已有三个：《凡人修仙传：人界篇》《剑来·开放世界》《完美世界 V4.2》 | **剧本包加载器**：结构化解析入库，驱动引擎。不走小说流水线 |
| **原始小说** | 未整理的长篇正文（用户投喂） | 用户导入 | **小说拆解流水线**：切块嵌入 + LLM 分层拆解 + **规范组装 + 多级质量门**，终点产物是一个达标的剧本包 |

剧本包是全系统统一的内容单元：手动编写、剧本包导入、小说拆解生成，三者收敛到同一规范（§8）。

**核心玩法循环**：

```
选择剧本包 → 首轮创建角色(时期/身份/资质 多步选择)
      ↓
┌─→ 阅读叙事(旁白+对话, 实体名可点击) → 交互选项卡 / 自由输入 / 面板操作
│         ↓
│   引擎规则校验(数值/物品/锚点条件, 确定性) + LLM 裁决叙事
│         ↓
│   状态更新 → 交互面板刷新(境界条/灵石/任务/NPC好感, 数值变化动效)
│         ↓
│   锚点条件满足 → 触发剧情事件(名场面级给全屏仪式动效) → 自动存档
└───────┘
```

**双形态 AI**：
- **离线（默认）**：内置 GGUF 量化小模型，llama.cpp 本地推理，零第三方依赖。
- **在线（可选）**：设置里配置任意 OpenAI 兼容 API（base_url / api_key / model）。

---

## 2. 技术选型

| 领域 | 选型 | 理由 |
|---|---|---|
| 语言 | Python 3.11+ | 用户要求；生态最适合本地 LLM |
| 本地推理 | `llama-cpp-python`（GGUF） | CPU/GPU 通吃、可打包进 EXE、社区模型最多 |
| 嵌入/检索 | `fastembed`（ONNX，CPU） | 体积小、无 PyTorch 依赖、中文模型齐全 |
| 前端 | **React 18 + Vite + Ant Design**（`web/`，构建产物 `web/dist/`） | 组件生态成熟（面板/卡片/表单/动效），Vite 开发热更新；发布物是纯静态文件，用户无需 Node |
| 本地服务 | **FastAPI + uvicorn**（仅绑定 127.0.0.1 + 一次性 token） | 前端↔引擎的标准 REST+SSE 接口；OpenAPI 自文档；LLM 流式输出走 SSE 天然匹配 |
| 桌面壳 | **pywebview**（系统 WebView2），缺失时回退系统浏览器 | 比 QtWebEngine 轻 ~150MB，仍满足"打开 EXE 即成品" |
| 数据库 | `sqlite3`（标准库） | 单文件、零运维、用户要求 |
| 结构化输出 | 提示词约束 + JSON 修复重试 + llama.cpp GBNF 语法兜底 | 小模型 JSON 遵循度有限，需多层防护 |
| 打包 | PyInstaller（**onedir** 模式）+ Inno Setup 安装器 | 模型体积 GB 级，onefile 每次启动解压到临时目录，不可接受 |

**明确不引入**：PyTorch/transformers、向量数据库、Qt（前端已由 React 接管，桌面壳走系统 WebView2）、任何强制联网组件。前端构建链（npm/Vite）仅开发期需要，发布物是纯静态 `web/dist/`，随 EXE 分发。

---

## 3. 整体架构

```
┌────────────────────────────────────────────────────────────┐
│  前端 web/（React + Vite + Ant Design，构建产物 web/dist/）                │
│  剧本架 · 角色创建向导 · 游戏视图（叙事流/交互面板/实体链接/选项卡/动效）   │
│        ↕ HTTP（REST + SSE，仅 127.0.0.1 + 一次性 token）                  │
├────────────────────────────────────────────────────────────┤
│  本地服务层 server/（FastAPI）                                             │
│  路由（packs/play/saves/settings）· 会话事件总线 · 静态资源托管            │
├────────────────────────────────────────────────────────────┤
│  渲染契约层 render/                                                        │
│  TurnPayload（结构化回合）· 叙事解析器（LLM 文本→渲染块）· 实体链接器      │
├────────────────────────────────────────────────────────────┤
│  游戏引擎层 core/                                                          │
│  回合循环 · 数值/状态机 · 触发词拦截 · 锚点事件调度 · 规则校验 · 存档        │
├────────────────────────────────────────────────────────────┤
│  剧本包层 pack/                                                            │
│  章节切分 → 结构化解析 → 入库；剧本包规范校验器(Pack Spec Validator)        │
│  ├─ 引擎模式: 解析入库, 引擎驱动                                            │
│  └─ 直通模式: 整包作为系统提示词, 仅做对话+存档+尽力而为的面板解析           │
├────────────────────────────────────────────────────────────┤
│  AI 服务层 ai/                                                             │
│  LLMBackend 抽象 ──┬─ LocalBackend (llama.cpp)                            │
│                    └─ RemoteBackend (OpenAI兼容)                           │
│  Embedder · 模型路由/降级 · 提示词模板 · 上下文预算                          │
├────────────────────────────────────────────────────────────┤
│  小说拆解流水线 ingest/ (产物=剧本包, 过规范+质量门, 见 §8)                  │
├────────────────────────────────────────────────────────────┤
│  数据层 db/ (SQLite, 全部参数绑定)                                          │
└────────────────────────────────────────────────────────────┘
```

**关键原则**：
1. **骨架与血肉分离**：数值体系、面板、锚点触发、触发词、存档由引擎代码执行；LLM 只产出叙事与结构化裁决。剧本包"约束"章节里的规则（防数值刷子、NPC 信息边界、状态一致性、不代玩家做重大决定）全部转为代码强制——这是本地小模型可行的根本前提。
2. **面板即组件，文本即 UI**：LLM/引擎的输出走统一**渲染契约**（TurnPayload 结构化数据），渲染层把叙事、面板、数值变化全部渲染成可交互组件，杜绝"一坨文字"。
3. **生成必有门**：小说拆解的每一步产物都要过结构/一致性/质量三级门 + 人工复核，不达标的剧本包不能进入可玩状态。
4. **AI 调用全部异步**：后台线程推理，UI 流式渲染。
5. **数据层只用参数绑定**：所有 SQL 一律参数化查询，禁止拼接/format/f-string 组装 SQL。

---

## 4. 内置模型方案（"内置几个小模型"）

| 角色 | 模型 | 量化/体积 | 职责 |
|---|---|---|---|
| **主力叙事/拆解** | Qwen3-1.7B-Instruct | Q4_K_M ≈ 1.1 GB | 叙事、裁决、小说拆解、剧本包辅助解析 |
| **快速档** | Qwen3-0.6B-Instruct | Q4_K_M ≈ 0.4 GB | 摘要、实体识别、低配降级档 |
| **嵌入** | bge-small-zh-v1.5（ONNX） | ≈ 0.1 GB | 小说分块向量化、RAG、NPC 长期记忆 |

- 模型文件统一放 `models/`，随 EXE 分发；发布**精简版**（≈1.0GB）与**完整版**（≈1.8GB）两档，高配用户可手动放 **Qwen3-4B Q4（≈2.5GB）** 增强档，程序自动识别。
- 直通模式建议在线 API 或 4B 档；引擎模式上下文经组装压缩，1.7B 可承担。
- **模型路由器**：按任务选模型；本地失败/超时 → 降级更小模型；开启"在线优先" → RemoteBackend，失败回落本地。

---

## 5. 交互渲染体系（render/）——"面板可交互"的落地

### 5.1 渲染契约：TurnPayload

引擎与 UI 之间只传结构化 JSON，UI 不解析自由文本：

```json
{
  "narrative": [
    {"type": "narration", "text": "药园的雾气里……"},
    {"type": "dialogue", "speaker": "韩立", "speaker_ref": "character:7", "text": "……"},
    {"type": "broadcast", "panel": "status_bar", "data": {"境界": "练气4层", "灵石": 12}}
  ],
  "entities": [{"ref": "character:7", "surface": "韩立"},
               {"ref": "item:31", "surface": "筑基丹"}],
  "deltas": [{"ref": "attr:灵石", "op": "+", "v": 2, "reason": "卖出草药"}],
  "choices": [{"id": 1, "text": "收下灵石", "tags": ["顺应"], "hint": "…"},
              {"id": 2, "text": "婉拒", "tags": ["逆反"]}, "..."],
  "effects": ["...白名单状态指令(仅引擎消费, 不下发UI)..."],
  "fx": {"level": "minor", "kind": "breakthrough"}
}
```

- 引擎模式下 LLM 按 GBNF 约束直接输出该结构；直通模式下由"尽力解析器"从纯文本提取（播报条正则 + 实体别名表匹配），解析不到就退化为纯文本渲染，交互降级但不缺失。
- `entities` 驱动叙事流内的**实体链接**：渲染时把文本中的实体名替换为可点击/可悬停的链接（`entity://character/7` 风格），点开即 Inspector 卡片。

### 5.2 交互面板组件清单

| 组件 | 交互行为 |
|---|---|
| **状态播报条**（常驻 HUD） | 境界可点击 → 境界详情卡（当前层效果、下一层需求、寿元）；灵石/物品 → 财产面板；地点 → 区域动态卡（人界简报类） |
| **修士/属性面板** | 每条属性带进度条与增长动效；数值变化以浮动 +N 动画呈现；面板可"降级/恢复"（对应剧本包触发词行为） |
| **任务面板** | 任务卡片带状态（进行/可交/失败）、奖励预览、"追踪"按钮（追踪项在 HUD 常驻）；点击任务定位到相关叙事轮 |
| **提示面板** | 三段列表（主线/角色互动/系统操作），每条可点击跳转对应入口 |
| **角色 Inspector 卡**（点实体名弹出） | 头像占位、性格/目标、好感度关系条、"他所知道的"（NPC 信息边界可视化——只显示其亲历事件）、历史对话回顾 |
| **物品 Inspector 卡** | 描述、来历（溯源到获得的那一轮）、"使用/查看/丢弃"动作按钮（动作走规则校验） |
| **锚点时间线** | 已揭晓的剧情锚点按时间排布成可滚动时间线；名场面级锚点带标记 |
| **章节结算面板** | 「本章结束」触发，统计本章数值增减/事件/关系变化，逐条可展开 |
| **选项卡** | 四向选项渲染为带标签（顺应/逆反/谨慎/冒险）的卡片，支持 1-4 数字键；悬停显示 hint |
| **输入区** | 自由输入框 + 触发词快捷 chips（「修士」「任务」「提示」「存档」…一键触发） |
| **存档槽** | 名场面卡片式（回合数/一句话进度/关键状态缩略），支持导出/导入单文件 |

### 5.3 动效与"仪式感"

- 数值浮动动画（+2 灵石）、境界突破全屏特效（丹香/天象，按剧本包 `fx` 配置）、名场面锚点触发的定格式呈现。
- 动效资源随前端（web/src/styles）实现，按剧本包可开关；低配机器可全局关闭动效。

### 5.4 服务接口（FastAPI，REST + SSE）

前端与本地服务之间走标准 HTTP（仅 127.0.0.1 + 一次性 token，`X-Auth-Token` 头或 `?token=` 查询参数，SSE 用后者）：

| 端点 | 方法 | 说明 |
|---|---|---|
| `/api/packs` | GET | 剧本架列表（标题/章节数/字数） |
| `/api/play` | POST | `{pack_title}` → 创建对局，返回 `playthrough_id` |
| `/api/play/{id}/input` | POST | `{text}` → 提交玩家输入/触发词，异步执行 |
| `/api/play/{id}/events` | GET | **SSE**：`delta`（流式补字）/ `turn`（整回合 TurnPayload）/ `note`（触发词/存档）/ `error`，15s 心跳 |
| `/api/play/{id}/history` | GET | 历史回合 TurnPayload（断线重连恢复叙事流） |
| `/api/play/{id}/saves` | GET | 存档槽列表 |
| `/api/health` | GET | 健康检查 |

- 生成在服务端后台线程执行，推理经事件总线发布到 SSE，前端只订阅渲染；对局会话串行化（同对局同时只有一个回合在跑）。
- 前端开发模式：`npm run dev`（Vite 5173）代理 `/api` 到 127.0.0.1:8765；发布模式：FastAPI 直接托管 `web/dist/` 静态产物，pywebview 窗口加载。
- 剧情渲染落地：**叙事解析器**（render/narrative_parser.py）把直通模式下 LLM 的纯文本输出解析为渲染块——`> **韩立：** …` → 对话块、`【境界 …｜灵石 …】` → 播报条块（字段化）、`【A】…` 选项行 → 选项块、其余 → 旁白块——前端按块类型映射 React 组件；引擎模式下 TurnPayload 由引擎直接产出，走同一组件集。

---

## 6. 剧本包：格式规范与双模式运行

### 6.1 剧本包格式（从三个现有包提炼的事实标准）

一个剧本包 = 一个 UTF-8 文本文件（.txt/.md），约定章节结构：

| 章节 | 内容 | 引擎中的落点 |
|---|---|---|
| 一、世界观 | 世界规则、核心循环 | 系统提示词"世界观段" |
| 二、角色卡 | 【角色名 · 一句话】+ 性格/目标/登场条件 | `characters` 表 → 角色 Inspector 卡数据源 |
| 三、数值体系 | 属性维度、货币、成长与奖励规则 | 属性定义 → 引擎数值系统 → 属性面板 |
| 四、状态播报与面板 | 播报条/各面板的格式 | `panel_specs`（组件布局与字段映射） |
| 五、输出格式 | 旁白/对话/四向选项结构、节奏与文风禁令 | 系统提示词"文风合同段" + TurnPayload 生成约束 |
| 六、约束 | 世界铁律 | 大部分转为引擎规则，残余进提示词 |
| 七、首轮输出 | 多步角色创建 | `creation_steps` → 角色创建向导 UI |
| 八、世界活性机制 | 事件时间表、锚点+触发条件+揭晓点、章节结算 | `story_anchors` + 事件调度 + 锚点时间线 |
| 九、存档与续玩 | 存档触发词与格式 | 触发词拦截 + 存档系统 |

### 6.2 加载流水线（pack/）

```
导入 .txt → 编码检测(UTF-8/GBK) → 章节切分(「^[一二三…]+、」+【】子标记)
  → 轻量解析: 章节正文、面板格式→panel_specs、触发词表
  → LLM 辅助解析: 角色卡/锚点/数值体系/首轮流程 → 结构化 JSON(GBNF约束)
  → Pack Spec 校验器(G1/G2, 见 §8.3) → 人工复核(可跳过高置信项) → 入库
```

- 解析失败章节降级为"纯文本上下文"注入，不阻塞整个包。
- 剧本包小（1.5万字），LLM 辅助解析导入时跑一次即可。

### 6.3 双模式运行

**引擎模式（默认）**：面板/数值/触发词/锚点/章节结算全由代码执行；LLM 每回合收到组装上下文，产出 TurnPayload；`effects` 只执行白名单指令且只能在引擎判定的可达/可触发集合内选择（防崩坏、防剧透——揭晓点由锚点系统按条件放行）。

**直通模式（兼容/高级）**：整包提示词原文直灌；引擎提供对话 UI、触发词拦截、尽力而为的面板解析（§5.1）与对话级存档。适用：在线 API 还原原始体验、解析失败回退、快速试新包。

### 6.4 引擎模式上下文预算（每回合约 9k token）

系统段 ~2k（世界观压缩+文风合同+约束精选）｜状态段 ~1.5k（播报数据+参与者角色卡）｜剧情段 ~2.5k（滚动摘要+最近 N 轮）｜锚点段 ~0.5k（邻近锚点元信息，不含揭晓内容）｜生成预留 ~1.5k。llama.cpp 前缀缓存复用系统段 KV cache。

---

## 7. 数据库设计（SQLite）

单库 `data/story_simulator.db`，WAL 模式。核心表：

```sql
-- 剧本包与剧本
packs(id, title, file_path, format_version, raw_text, parse_status, created_at)
pack_sections(id, pack_id, key, kind, title, body)

storys(id, title, source_type, source_pack_id, source_novel_id,   -- 'pack'|'manual'|'novel'
       world_rules_json, metadata_json, quality_grade,            -- A/B/C, 生成包专用
       review_status, status, created_at)                         -- draft→ready
story_nodes(id, story_id, node_key, title, summary, location,
       participants_json, entry_conditions_json, hooks_text, exits_json, order_idx)
story_anchors(id, story_id, title, trigger_json, reveal_text,
       spoiler_level, is_triggered, sort_idx,
       source_refs_json, confidence)                              -- 生成包溯源+置信度
characters(id, story_id, name, aliases_json, role, personality, goal,
       affinity, is_alive, memory_json,
       source_refs_json, confidence)
creation_steps(id, story_id, step_idx, question, options_json, effect_json)
panel_specs(id, story_id, panel_key, spec_json)                   -- 面板组件布局/字段/交互
trigger_words(id, story_id, word, action)

-- 原始小说(仅 'novel' 路径)
novels(id, title, author, source_path, charset, total_chars, status, created_at)
chapters(id, novel_id, idx, title, char_count, raw_text)
chunks(id, novel_id, chapter_id, idx, text, embedding BLOB)
extract_logs(id, story_id, chapter_id, payload_json, gate_results_json, reviewed)

-- 游戏运行与存档
playthroughs(id, story_id, mode, player_json, world_flags_json,
       current_node_key, turn_count, created_at, updated_at)
turns(id, playthrough_id, idx, turn_payload_json, player_input, adjudication_json, created_at)
saves(id, playthrough_id, slot, summary, snapshot_json, created_at, updated_at)

settings(key, value)
prompt_templates(id, task, template, version)
```

要点：
- 存档 = `snapshot_json` 整体快照（玩家+世界+NPC+当前节点/消息摘要），多槽位 + autosave，事务写入；直通模式额外含完整消息历史。存档可导出/导入单文件。
- 数值体系定义存 `storys.metadata_json`，实例值存 `playthroughs.player_json`。
- `turns` 存完整 TurnPayload——回放、任务"定位到相关轮"、结算统计都靠它。
- **全部数据访问走 db/ 层 DAO，SQL 一律参数绑定。**

---

## 8. 小说拆解：生成规范与质量门（防"乱生成、低质量"）

### 8.1 剧本包规范（Pack Spec v1）

生成的剧本包必须同时满足**文档层**与**数据层**规范：

- **文档层**：九章节齐全且顺序合规；每章有最低内容要求（如 角色卡≥N 张、锚点≥M 个/万字、每个锚点的触发条件必须"可判定"——能写成引擎可求值的条件表达式，不允许"合适的时机"这类模糊描述）。
- **数据层**：各实体（角色卡/锚点/数值体系/首轮步骤/面板规格/触发词）有 JSON Schema，逐字段校验。
- **溯源强制**：生成包的每个锚点/角色/世界规则必须携带 `source_refs`（依据的章节与行号）；无法溯源的输出标记为"模型演绎"，置信度降级，复核时高亮。**没有依据的内容不允许伪装成原著设定**。

### 8.2 分层生成流程（不是一步到位）

```
小说 → 切块嵌入 → [用户选章节范围]
  → 第1层 事实抽取: 逐章抽出 角色/事件/地点/世界规则(带原文引用)
  → 第2层 聚合归并: 跨章合并角色(别名归一)、事件时间线、规则去重
  → 第3层 规范组装: 世界观←规则聚合; 角色卡←角色合并; 数值体系←
       "已确立等级体系映射到内置模板"(修仙/武侠/西幻, 用户选配);
       锚点←事件+可判定触发条件; 首轮流程←开篇身份推导
  → 第4层 校验与评分(G1~G3) → 第5层 人工复核 → 入库(ready)
```

### 8.3 多级质量门

| 门 | 类型 | 检查内容 | 不达标处理 |
|---|---|---|---|
| **G1 结构门** | 自动，硬性 | JSON Schema 校验、必填章节齐全、字段类型 | 该章节重试（最多 2 次）→ 仍失败则剔除该章节并在复核界面标红 |
| **G2 一致性门** | 自动，硬性 | 触发条件可解析可求值；数值公式可计算；角色别名全局唯一无冲突；时间线无乱序；实体引用可解析；无重复锚点 | 自动阻断，定位到具体字段，交人工复核 |
| **G3 质量门** | 自动（LLM-as-judge + 评分规则） | 按维度打分 0-5：世界观自洽性、角色卡信息量、锚点可判定性、爽点节奏配置、二创偏离声明清晰度 | 任一维度 <3 分标黄，进必审清单 |
| **G4 人工复核** | 人工，必经 | 复核界面：左原文右产物、溯源跳转、置信度排序、低分项高亮、一键采纳高置信项 | 生成包 `review_status` 保持 draft，**不过复核不能进入可玩列表** |

- 评分规则部分是确定性的（锚点密度、角色覆盖率：出场频次 Top-N 人物必须有卡），部分用 LLM-as-judge（主力模型评分 + 与快速档交叉校验，分差过大标可疑）。
- **Prompt 版本化 + 回归基准**：拆解提示词进 `prompt_templates` 带版本；用三个手工剧本包的对应章节做黄金基准，改动提示词后跑差分对比，防质量回退。
- 生成包带 `quality_grade`（A/B/C）展示在剧本架上，用户知情。

### 8.4 防低质量的生成纪律

- **数值只映射不发明**：等级/货币/成长曲线从小说已确立体系映射到内置模板，LLM 无权编造数值公式。
- **密度下限**：锚点数/万字、角色卡覆盖主要人物、每锚点必有可判定触发条件。
- **二创边界声明**：生成的偏离性内容（如为可玩性补的支线）在包内显式标注"二创延伸"，与原著事实区分。
- **拆解是后台任务**：可暂停/续跑，失败章节跳过不阻塞；全部产物（含被剔除项）留档 `extract_logs` 供复核翻查。

---

## 9. 游戏引擎（core/）

**状态模型**：
- `PlayerState`：属性（按剧本包数值体系，如 境界/寿元/灵石）、物品栏、身份背景（首轮落点）。
- `WorldState`：剧情 flag、事件时间表进度、区域动态。
- `NPCState`：好感、存活、所在场景、短期记忆（最近 N 轮）+ 长期记忆（嵌入检索）；**信息边界由引擎维护**——NPC 只"知道"亲历事件，Inspector 卡的"他所知道的"由此驱动。

**回合流程**：
1. 玩家输入/交互动作 → **触发词拦截**（「修士」「任务」「提示」「存档」「本章结束」等，命中即渲染对应面板，不进 LLM）。
2. 组装上下文（§6.4）→ LLM 流式生成 TurnPayload（叙事+选项 或 裁决结果）。
3. 引擎执行 `effects` 白名单指令并按数值体系校验（防刷子：同类收益递减、抗药性等按包配置）。
4. 状态变更 → 推送面板刷新 + 数值动效；锚点条件求值 → 触发/放行揭晓内容 + `fx` 动效。
5. 每回合/锚点触发 → 自动快照（频率可配）。

---

## 10. 在线 API 模式（ai/remote.py）

- 设置页配置 `base_url` / `api_key` / `model` / 温度等，兼容 OpenAI 风格接口（官方、DeepSeek、智谱、本地 Ollama 等）。
- 开关：**离线优先（默认）** / **在线优先（失败回落本地）**；直通模式建议在线。
- RemoteBackend 与 LocalBackend 同一 `LLMBackend` 接口（`generate` / `generate_json` / `stream`），上层无感知。api_key 存 settings，后续可选 Windows 凭据管理器加密。

---

## 11. 目录结构

```
AI_Story_Simulator/
├─ app/
│  ├─ main.py                 # 入口
│  ├─ core/                   # engine.py state.py triggers.py anchors.py rules.py save.py
│  ├─ render/                 # contract.py(TurnPayload) linker.py(实体链接) bridge.py(QWebChannel)
│  ├─ pack/                   # loader.py parser.py validator.py(Pack Spec) review.py
│  ├─ ai/                     # backend.py local.py remote.py router.py prompts/
│  ├─ ingest/                 # cleaner.py splitter.py chunker.py extractor.py gates.py
│  ├─ db/                     # database.py(参数绑定) dao/ migrations.py
│  └─ server/                 # app.py(FastAPI工厂+token鉴权) routers.py sessions.py(事件总线)
├─ web/                       # React 前端：src/(pages/components) · 构建产物 web/dist/
├─ models/                    # GGUF + ONNX 模型文件
├─ data/                      # story_simulator.db(运行时)
├─ script/                    # 剧本包素材(已有三个)
├─ tests/                     # 含黄金基准回归测试
├─ build/                     # pyinstaller spec + Inno Setup
└─ DESIGN.md
```

---

## 12. 打包与分发

- PyInstaller **onedir**：程序 ≈ 400MB（Python + FastAPI/uvicorn/pywebview，远轻于 Qt），`web/dist/` 作为数据文件随包并由本地服务托管；加模型后精简版 ≈ 1.4GB / 完整版 ≈ 2.2GB；Inno Setup 安装器，模型为可选组件。
- llama-cpp-python 预编译 CPU wheel（通用优先），GPU 版作可选变体运行时探测。
- 首启自检：模型完整性、AVX2、DB 初始化、WebEngine 可用性。

---

## 13. 开发路线图

| 阶段 | 内容 | 产出验收 |
|---|---|---|
| **0. 骨架** | 工程、DB 迁移、AI 后端抽象、CLI 直通闭环 | ✅ 已完成：剧本包解析/DAO/本地+在线+演练后端/触发词/存档，测试全绿 |
| **1. 直通模式** | FastAPI + React/AntD + pywebview 桌面壳 + EXE 打包；**续玩入口（未竟之局/存档快照恢复）、角色创建向导（首轮分步选择）、实体链接 Inspector 卡** | ✅ 完成：52 测试全绿，EXE 双击即玩 |
| **2. 引擎模式** | 结构化解析、TurnPayload 全量渲染（选项卡/Inspector/动效）、数值/锚点/结算、角色创建向导 | 数值/面板/存档/防刷子全由代码保证 |
| **3. 小说拆解** | 分层拆解 + Pack Spec 校验器 + G1~G3 质量门 + 复核界面 + 黄金回归 | 从一部小说前 20 章生成 A/B 级剧本包并通关一局 |
| **4. 在线与分发** | RemoteBackend、设置页、安装器 | 干净 Windows 机器安装即玩，断网可用 |

风险与对策：
- **WebView2 缺失（老旧 Win10）** → 安装器捆绑 WebView2 引导程序；运行时回退打开系统浏览器（功能不缺失，仅少窗口壳）。
- **小模型叙事力不足** → 引擎模式压缩上下文 + 4B 档 + 在线优先；阶段 1 直通模式尽早暴露上限。
- **小模型 JSON 不稳** → GBNF + 修复重试 + 章节级降级。
- **剧本包格式漂移** → 解析器宽容 + 未知章节降级纯文本 + 直通模式兜底。
- **生成质量失控** → §8 全套质量门 + 溯源强制 + 人工复核必经 + prompt 回归基准。
- **低配机器** → 模型档位 + 动效可关 + 流式输出。
