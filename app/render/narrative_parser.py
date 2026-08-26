"""叙事解析器：LLM 纯文本输出 → 渲染块（DESIGN.md §5.4）。

直通模式下模型按剧本包「输出格式」章节产出带结构的文本，本解析器将其
切成前端可渲染的块；引擎模式下 TurnPayload 由引擎直接产出，不走这里。

识别的块类型：
- {"type": "narration", "text": ...}                          旁白
- {"type": "dialogue", "speaker": ..., "text": ...}           对话（> **X：** …）
- {"type": "broadcast", "fields": [{"label","value"}, ...]}   播报条（【…｜…】整行）
- {"type": "choices", "options": [{"id","text"}, ...]}        选项（【A】… / A. …，≥2 行连续才成块）
"""

from __future__ import annotations

import re

_DIALOGUE_BOLD_RE = re.compile(r"^>\s*\*\*(.+?)[:：]\*\*\s*(.*)$")
_DIALOGUE_PLAIN_RE = re.compile(r"^>\s*([^*>：:]{1,20})[:：]\s*(.*)$")
_BROADCAST_RE = re.compile(r"^【([^【】]{2,200})】$")
# 选项行：【A】文本 / A.文本 / A、文本 / A：文本（"A 文本"不算——避免误伤普通句子）
_CHOICE_RE = re.compile(r"^\s*(?:【\s*([A-Da-d])\s*】\s*|([A-Da-d])[\.、:：]\s+)(.+)$")


def _parse_broadcast_fields(content: str) -> list[dict]:
    fields = []
    for part in re.split(r"[｜|]", content.strip()):
        part = part.strip()
        if not part:
            continue
        m = re.match(r"^(.+?)[:：\s]+(.+)$", part)
        if m:
            fields.append({"label": m.group(1).strip(), "value": m.group(2).strip()})
        else:
            fields.append({"label": part, "value": ""})
    return fields


def parse_narrative(text: str) -> list[dict]:
    blocks: list[dict] = []
    narration: list[str] = []
    pending_choices: list[tuple[str, str, str]] = []   # (id, text, 原始行)

    def flush_narration() -> None:
        while narration and not narration[-1].strip():
            narration.pop()
        if narration:
            blocks.append({"type": "narration", "text": "\n".join(narration)})
        narration.clear()

    def flush_choices() -> None:
        if not pending_choices:
            return
        if len(pending_choices) >= 2:
            blocks.append({
                "type": "choices",
                "options": [{"id": cid.upper(), "text": ctext.strip()}
                            for cid, ctext, _raw in pending_choices],
            })
        else:
            # 单独一行大概率是普通句子，原样退回旁白
            for _cid, _ctext, raw in pending_choices:
                narration.append(raw)
        pending_choices.clear()

    for raw_line in text.splitlines():
        line = raw_line.rstrip()

        if not line.strip():
            if pending_choices:
                flush_choices()
            narration.append("")
            continue

        choice_m = _CHOICE_RE.match(line)
        if choice_m:
            flush_narration()  # 选项块前的旁白（引导句）必须排在选项之前
            cid = choice_m.group(1) or choice_m.group(2)
            pending_choices.append((cid, choice_m.group(3), line))
            continue
        if pending_choices:
            flush_choices()

        broadcast_m = _BROADCAST_RE.match(line.strip())
        if broadcast_m and ("｜" in broadcast_m.group(1) or "|" in broadcast_m.group(1)):
            flush_narration()
            blocks.append({"type": "broadcast", "fields": _parse_broadcast_fields(broadcast_m.group(1))})
            continue

        bold_m = _DIALOGUE_BOLD_RE.match(line)
        if bold_m:
            flush_narration()
            blocks.append({"type": "dialogue", "speaker": bold_m.group(1).strip(),
                           "text": bold_m.group(2).strip()})
            continue
        plain_m = _DIALOGUE_PLAIN_RE.match(line)
        if plain_m:
            flush_narration()
            blocks.append({"type": "dialogue", "speaker": plain_m.group(1).strip(),
                           "text": plain_m.group(2).strip()})
            continue

        narration.append(line)

    flush_choices()
    flush_narration()
    return blocks
