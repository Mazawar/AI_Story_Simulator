"""剧本包结构化解析（确定性，无 LLM）：角色卡与首轮创建步骤。

阶段 1 收尾项的数据源：
- 角色卡 → 实体链接（叙事中人名可点击弹 Inspector 卡）
- 首轮输出 → 角色创建向导（分步选择，替代模型输出的问卷文本）
"""

from __future__ import annotations

import re

from .models import Pack

# 角色卡标题：【韩立 · 七玄门杂役出身，四灵根修士】（时间点B登场）
_CARD_RE = re.compile(r"^【\s*([^【】·]+?)\s*·\s*([^【】]+?)\s*】")
# 粗体变体1：**陈平安 · 泥瓶巷孤儿（你的发小）**
_CARD_BOLD_RE = re.compile(r"^\*\*\s*([^*·【】]+?)\s*·\s*([^*]+?)\s*\*\*\s*$")
# 粗体变体2（爱情公寓式）：**胡一菲**（大学老师·楼长·武力担当）
_CARD_BOLD_PAREN_RE = re.compile(r"^\*\*\s*([^*【】]{1,12})\s*\*\*\s*[（(](.+?)[)）]\s*$")
# 步骤标题：【第一步】你醒来在什么时候？
_STEP_RE = re.compile(r"^【\s*第([一二三四五六七八九十\d]+)步\s*】\s*(.*)$")
# 选项行：【A】七玄门时期——……（也兼容【A·xxx】）
_OPT_RE = re.compile(r"^【\s*([A-D])\s*[·]?\s*】\s*(.+)$")

# 不作为实体卡片的分类标签
_CARD_SKIP = {"词条角色", "一句话卡", "角色卡", "次要角色", "主要角色"}


def parse_character_cards(pack: Pack, limit: int = 40) -> list[dict]:
    """从「角色卡」章节提取 [{name, desc}]（按出现顺序）。"""
    section = pack.section("characters")
    if section is None:
        return []
    cards: list[dict] = []
    for line in section.body.splitlines():
        s = line.strip()
        m = _CARD_RE.match(s) or _CARD_BOLD_RE.match(s)
        if m:
            name, desc = m.group(1).strip(), m.group(2).strip()
        else:
            m2 = _CARD_BOLD_PAREN_RE.match(s)
            if not m2:
                continue
            name, desc = m2.group(1).strip(), m2.group(2).strip()
        if name in _CARD_SKIP or len(name) > 12:
            continue
        if any(c["name"] == name for c in cards):
            continue
        cards.append({"name": name, "desc": desc})
        if len(cards) >= limit:
            break
    return cards


def parse_creation_steps(pack: Pack, limit: int = 6) -> list[dict]:
    """从「首轮输出」章节提取分步创建流程 [{question, options:[{id,text}]}]。

    至少 1 步且每步 ≥2 个选项才算有效，否则返回空（调用方回退自动开场）。
    """
    section = pack.section("opening")
    if section is None:
        return []
    steps: list[dict] = []
    current: dict | None = None
    question_lines: list[str] = []

    def close_step() -> None:
        nonlocal current, question_lines
        if current is not None:
            current["question"] = " ".join(q for q in question_lines if q).strip()
            question_lines = []
            if len(current["options"]) >= 2 and current["question"]:
                steps.append(current)
        current = None

    for line in section.body.splitlines():
        s = line.strip()
        step_m = _STEP_RE.match(s)
        if step_m:
            close_step()
            current = {"question": "", "options": []}
            if step_m.group(2).strip():
                question_lines.append(step_m.group(2).strip())
            continue
        if current is None:
            continue
        opt_m = _OPT_RE.match(s)
        if opt_m:
            current["options"].append({"id": opt_m.group(1), "text": opt_m.group(2).strip()})
        elif s:
            # 选项出现前的说明行归入问题；出现后的补充行（如记忆说明）忽略
            if not current["options"]:
                question_lines.append(s)
        if len(steps) >= limit:
            break

    close_step()
    return steps


def pack_meta(pack: Pack) -> dict:
    """/api/packs 附加元数据：角色卡 + 创建步骤。"""
    return {
        "characters": parse_character_cards(pack),
        "creation_steps": parse_creation_steps(pack),
    }
