"""锚点解析：剧情锚点（时间表）与揭晓点 → 结构化锚点（确定性，无 LLM）。

识别的形态：
- 凡人：事件时间表「④虚天殿（21-30轮）：秘境开启…」——带轮次窗口，可解析成条件；
- 剑来：【剧情锚点】①外乡人涌入小镇（修士进镇挑人）②…——编号列表，无轮次；
- 揭晓点：bullets「- 【X：真相…】触发条件：玩家接近墨大夫…」或「→真相：…；由某人道出」。

条件策略：能解析出轮次窗口/境界要求的用真条件；其余按序分配启发式轮次窗口
（第 i 个锚点 ≈ 第 i*6 轮起），揭晓点必须由玩家主动探查/前置锚点触发才放行。
"""

from __future__ import annotations

import re

from .models import Pack

_CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"
_TIMELINE_RE = re.compile(rf"([{_CIRCLED}])\s*([^{_CIRCLED}\n]{{6,200}})")
_TURN_WINDOW_RE = re.compile(r"[（(](\d+)[-–~](\d+)轮[)）]")
_REALM_REQ_RE = re.compile(r"(练气|筑基|结丹|元婴|化神)期?以上")
# 揭晓点（凡人式）：- 【揭晓点：标题——真相。触发条件：条件】
_REVEAL_FULL_RE = re.compile(r"^[\s\-*>]*【(?:揭晓点|晨昏点|拂晓点)[：:]\s*(.+?)】\s*$")
_TRIGGER_INLINE_RE = re.compile(r"触发条件[：:](.+)$")
# 揭晓点（剑来式）：- 问题…→真相：…；由某人道出
_REVEAL_ARROW_RE = re.compile(r"^[\s\-*>]*(.{4,40}?)[→➜](?:真相[：:])?(.+)$")

# 启发式：无显式条件的锚点，按序落在第 i*6 轮起的窗口
_HEURISTIC_SPAN = 6


_REVEAL_LINE_MARKS = ("揭晓点", "晨昏点", "拂晓点", "→真相", "➜真相")


def strip_reveals(text: str) -> str:
    """剥除正文中的揭晓点行（剧透隔离：世界观文本进入 LLM 上下文前必须过滤）。"""
    kept = []
    for line in text.splitlines():
        if any(mark in line for mark in _REVEAL_LINE_MARKS):
            continue
        kept.append(line)
    return "\n".join(kept)


def _split_title(entry: str) -> tuple[str, str]:
    """时间表条目 → (标题, 说明)；标题取首个停顿符前的短语。"""
    entry = entry.strip().rstrip("。；;")
    m = re.match(r"(.{2,24}?)[：:（(]", entry)
    title = m.group(1).strip() if m else entry[:16]
    return title, entry


def parse_anchors(pack: Pack) -> list[dict]:
    """解析锚点列表（按触发顺序），含 timeline 与 reveal 两类。

    锚点散布在「世界观」（揭晓点/剧情锚点）与「世界活性机制」（事件时间表）
    两个章节，合并扫描。
    """
    sections = [pack.section("world"), pack.section("dynamics")]
    body = "\n".join(s.body for s in sections if s is not None)
    anchors: list[dict] = []

    # ---- 时间表锚点（含【剧情锚点】编号列表） --------------------------------
    order = 0
    for marker, entry in _TIMELINE_RE.findall(body):
        title, desc = _split_title(entry)
        cond: dict = {"type": "all", "conds": []}
        tw = _TURN_WINDOW_RE.search(entry)
        if tw:
            cond["conds"].append({"type": "turn_gte", "v": int(tw.group(1))})
            cond["conds"].append({"type": "turn_lte", "v": int(tw.group(2)) + 6})
        rr = _REALM_REQ_RE.search(entry)
        if rr:
            cond["conds"].append({"type": "realm_gte", "realm": rr.group(1)})
        if not cond["conds"]:
            cond = {"type": "turn_gte", "v": max(1, order * _HEURISTIC_SPAN)}
        anchors.append({
            "key": f"tl-{order}", "title": title, "desc": desc,
            "kind": "timeline", "trigger": cond,
            "spoiler_level": "normal", "order": order,
        })
        order += 1

    # ---- 揭晓点 ----------------------------------------------------------------
    for line in body.splitlines():
        s = line.strip()
        title, truth, trigger = None, None, ""
        fm = _REVEAL_FULL_RE.match(s)
        if fm:
            inner = fm.group(1).strip()
            tm = _TRIGGER_INLINE_RE.search(inner)
            if tm:
                trigger = tm.group(1).strip()
                inner = inner[: tm.start()].strip()
            # 标题——真相。（首个破折号分隔）
            parts = re.split(r"[—\-]{1,2}", inner, maxsplit=1)
            title = parts[0].strip("。；; ")
            truth = parts[1].strip("。；; ") if len(parts) > 1 else inner
        else:
            am = _REVEAL_ARROW_RE.match(s)
            if am and "真相" in s:
                title, truth = am.group(1).strip(), am.group(2).strip()
        if not (title and truth):
            continue
        # 触发条件是自由文本：v1 统一为"主动探查/裁决请求才放行"（防剧透的结构保证）
        cond = {"type": "any", "conds": [
            {"type": "flag", "k": f"reveal:{title}"},
            {"type": "anchor_effect", "title_contains": title[:6]},
        ]}
        anchors.append({
            "key": f"rv-{len(anchors)}", "title": title, "desc": truth,
            "kind": "reveal", "trigger": cond, "raw_trigger": trigger,
            "spoiler_level": "reveal", "order": len(anchors),
        })

    return anchors


def parse_random_events(pack: Pack) -> list[dict]:
    """随机事件池：- 坊市淘宝（地摊上的一枚玉简——摊主不知道它是什么，你知道）"""
    section = pack.section("dynamics")
    if section is None:
        return []
    body = section.body
    # 定位「随机事件池（…）：」标题行（区别于"随机事件池陆续登场"的正文引用）
    idx = None
    for m in re.finditer(r"随机事件池[（(]", body):
        idx = m.start()
        break
    if idx is None:
        return []
    events: list[dict] = []
    for line in body[idx:].splitlines()[1:]:
        s = line.strip()
        if not s:
            continue
        m2 = re.match(r"^[-*·]\s*([^（（：:]{2,12})\s*[（(]?(.*)$", s)
        if m2:
            title = m2.group(1).strip()
            desc = m2.group(2).strip("（）()。")
            events.append({"title": title, "desc": desc[:80]})
        elif events:
            break                          # 列表结束
    return events


def parse_world_materials(pack: Pack, *, limit: int = 40) -> list[dict]:
    """全包通用素材提取：任何章节的 bullet 列表 → 世界事件素材。

    不依赖章节命名——修仙包的随机事件池、末日包的随机事件系统、
    探索/NPC 条目都会被收进来，供停滞注入与"世界将发生之事"使用。
    排除：揭晓点（剧透隔离）、选项行、创建步骤、引号对话示例。
    """
    materials: list[dict] = []
    seen_titles: set[str] = set()
    for section in pack.sections:
        if section.key in ("preamble", "opening"):
            continue
        current_group = section.title
        for line in section.body.splitlines():
            s = line.strip()
            if not s:
                continue
            if re.match(r"^[\-*·]\s", s):
                title = ""
                desc = ""
                m = re.match(r"^[-*·]\s*([^（（：:]{2,16})\s*[（:：]?(.*)$", s)
                if m:
                    title, desc = m.group(1).strip(), m.group(2).strip("（）()。")
                else:
                    title = s.lstrip("-*· ").strip()[:16]
                if (not title or len(title) < 2
                        or title.startswith(("我", "你", "玩家", "例如", "\"", "“"))
                        or "揭晓" in title or "真相" in s
                        or re.match(r"^【?\s*[A-Da-d\d]", title)):
                    continue
                if title in seen_titles:
                    continue
                seen_titles.add(title)
                materials.append({"group": current_group, "title": title,
                                  "desc": desc[:70]})
                if len(materials) >= limit:
                    return materials
    return materials


def parse_identity_lines(pack: Pack) -> list[dict]:
    """身份线（支线节点链）：凡人包「你的身份线」四选一的 ①→⑤ 节点。

    返回 [{"identity": "凡人", "nodes": ["灵根觉醒", …]}]；无则空表。
    """
    section = pack.section("dynamics")
    if section is None:
        return []
    body = section.body
    if "身份线" not in body:
        return []
    result: list[dict] = []
    current: dict | None = None
    for line in body.splitlines():
        s = line.strip()
        m = re.match(r"^(\d+)[.、]\s*\*{0,2}([\u4e00-\u9fa5]{2,6})\*{0,2}[：:]", s)
        if m:
            current = {"identity": m.group(2), "nodes": []}
            result.append(current)
            s = s.split("：", 1)[-1]
        if current is not None:
            for marker, seg in re.findall(rf"([{_CIRCLED}])([^{_CIRCLED}]{{2,20}})", s):
                node = re.split(r"[（(（]", seg)[0].strip()
                if node:
                    current["nodes"].append(node)
    return result
