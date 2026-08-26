"""叙事解析器：LLM 纯文本输出 → 渲染块（DESIGN.md §5.4）。

直通模式下模型按剧本包「输出格式」章节产出带结构的文本，本解析器将其
切成前端可渲染的块；引擎模式下 TurnPayload 由引擎直接产出，不走这里。

针对本地小模型的加固（实测漂移形态）：
- 行首引用前缀（>）与粗体记号（**）剥离后再分类；
- LaTeX 装饰行（$$、\\fcolorbox、\\textcolor…）直接丢弃；
- 播报条变体：整行含 ≥2 个「【字段】值」对时聚合为 broadcast 块；
- 面板标题行（【播报条】【修士面板】等无值标签）丢弃；
- 分隔线（---）丢弃。

块类型：
- {"type": "narration", "text": ...}                          旁白
- {"type": "dialogue", "speaker": ..., "text": ...}           对话（> **X：** …）
- {"type": "broadcast", "fields": [{"label","value"}, ...]}   播报条
- {"type": "choices", "options": [{"id","text"}, ...]}        选项（≥2 行连续才成块）
"""

from __future__ import annotations

import re

_DIALOGUE_BOLD_RE = re.compile(r"^>\s*\*\*(.+?)[:：]\*\*\s*(.*)$")
_DIALOGUE_PLAIN_RE = re.compile(r"^>\s*([^*>：:]{1,20})[:：]\s*(.*)$")
_BROADCAST_RE = re.compile(r"^【([^【】]{2,200})】$")
# 选项行：【A】文本 / A.文本 / A、文本 / A：文本（"A 文本"不算——避免误伤普通句子）
_CHOICE_RE = re.compile(r"^\s*(?:【\s*([A-Da-d])\s*】\s*|([A-Da-d])[\.、:：]\s+)(.+)$")
# 「【字段】值」对（播报条变体）
_FIELD_PAIR_RE = re.compile(r"【\s*([^【】]{1,12})\s*】\s*([^【】]*?)(?=\s*【|\s*$)")

_LATEX_MARKERS = (
    "$$", "\\fcolorbox", "\\colorbox", "\\textcolor", "\\begin{", "\\end{",
    "\\qquad", "\\quad", "\\overline", "\\underline", "\\Large", "\\large",
    "\\rule", "\\hline", "\\makebox", "\\framebox", "\\parbox", "\\hspace",
    "\\vspace", "\\tabular", "\\fbox",
)
_PANEL_LABELS = {"播报条", "修士面板", "任务面板", "提示面板", "旁白", "对话", "正文"}


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


def _clean_line(raw: str) -> str:
    """剥引用前缀与粗体记号（对话行已在调用前单独识别）。"""
    s = raw.strip()
    while s.startswith(">"):
        s = s[1:].lstrip()
    s = s.strip()
    if not s:
        return ""
    if s.startswith("**") and s.endswith("**") and len(s) > 4:
        s = s[2:-2].strip()
    s = s.replace("**", "")
    return s.strip()


def _is_latex(line: str) -> bool:
    return any(marker in line for marker in _LATEX_MARKERS)


def _broadcast_variant(cleaned: str) -> list[dict] | None:
    """「【字段】值 … 【字段】值」聚合变体：≥2 对且至少一个有值才算播报条。"""
    if "【" not in cleaned:
        return None
    pairs = _FIELD_PAIR_RE.findall(cleaned)
    fields = [
        {"label": label.strip(), "value": value.strip(" |｜\t").strip()}
        for label, value in pairs
    ]
    fields = [f for f in fields if f["label"]]
    if len(fields) >= 2 and any(f["value"] for f in fields):
        return fields
    return None


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
                narration.append(_clean_line(raw) or raw)
        pending_choices.clear()

    for raw_line in text.splitlines():
        line = raw_line.rstrip()

        if not line.strip() or line.strip() in ("---", "***", "___"):
            if pending_choices:
                flush_choices()
            narration.append("")
            continue

        # 1) 选项行（原始或剥净后均尝试）
        choice_m = _CHOICE_RE.match(line.strip()) or _CHOICE_RE.match(_clean_line(line))
        if choice_m:
            flush_narration()  # 选项块前的旁白（引导句）必须排在选项之前
            cid = choice_m.group(1) or choice_m.group(2)
            pending_choices.append((cid, choice_m.group(3), line))
            continue
        if pending_choices:
            flush_choices()

        # 2) 对话（带 > 引用的两种形态，在剥净前识别）
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

        # 3) 剥净后处理：LaTeX 丢弃 / 播报条 / 标签行丢弃 / 旁白
        cleaned = _clean_line(line)
        if not cleaned:
            continue
        if _is_latex(cleaned):
            continue

        broadcast_m = _BROADCAST_RE.match(cleaned)
        if broadcast_m and ("｜" in broadcast_m.group(1) or "|" in broadcast_m.group(1)):
            flush_narration()
            blocks.append({"type": "broadcast",
                           "fields": _parse_broadcast_fields(broadcast_m.group(1))})
            continue

        variant = _broadcast_variant(cleaned)
        if variant is not None:
            flush_narration()
            blocks.append({"type": "broadcast", "fields": variant})
            continue

        # 无值面板标签行（【播报条】等）→ 丢弃
        if (cleaned.startswith("【") and cleaned.endswith("】")
                and len(cleaned) <= 12 and cleaned[1:-1].strip() in _PANEL_LABELS):
            continue

        narration.append(cleaned)

    flush_choices()
    flush_narration()
    return blocks
