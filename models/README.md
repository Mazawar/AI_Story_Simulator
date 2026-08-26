# models/ · 本地模型文件

本目录存放 GGUF（生成模型）与 ONNX（嵌入模型）权重，**不入 git**（见 .gitignore）。

## 推荐（见 DESIGN.md §4）

| 角色 | 模型 | 量化 | 体积约 |
|---|---|---|---|
| 主力叙事/拆解 | Qwen3-1.7B-Instruct | Q4_K_M | 1.1 GB |
| 快速档 | Qwen3-0.6B-Instruct | Q4_K_M | 0.4 GB |
| 嵌入（阶段3） | bge-small-zh-v1.5 | ONNX | 0.1 GB |

下载：在 Hugging Face 搜索模型名 + "GGUF"（如 `Qwen3-1.7B-Instruct GGUF`），下载 `.gguf` 文件直接放入本目录。程序启动时自动扫描本目录识别可用模型，按 上表顺序择优。

## 目录约定

- 文件名任意（含模型名与量化标记即可被识别），例如 `qwen3-1.7b-instruct-q4_k_m.gguf`
- 多个模型可共存，路由器按任务类型选择（见 app/ai/router.py）
