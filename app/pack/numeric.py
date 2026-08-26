"""数值体系解析：剧本包「数值体系」章节 → NumericSchema（确定性，无 LLM）。

Schema 存 storys.metadata_json，由 core/rules.py 的 NumericState 消费。
解析不出的包回退 DEFAULT_SCHEMA（通用修仙模板），引擎模式不因格式缺失而不可用。
"""

from __future__ import annotations

import re

from .models import Pack

# ---- 中文数字 ---------------------------------------------------------------

_CN_DIGITS = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def cn_num(s: str) -> int | None:
    """中文数字 → 整数（支持到万：'十三'→13，'二百'→200，'两千'→2000）。"""
    s = s.strip()
    if s.isdigit():
        return int(s)
    total, num = 0, 0
    for ch in s:
        if ch in _CN_DIGITS:
            num = _CN_DIGITS[ch]
        elif ch == "十":
            total += (num or 1) * 10
            num = 0
        elif ch == "百":
            total += (num or 1) * 100
            num = 0
        elif ch == "千":
            total += (num or 1) * 1000
            num = 0
        elif ch == "万":
            total += (num or 1) * 10000
            num = 0
        else:
            return None
    return total + num


# ---- 默认模板（无数值章节的包，如剑来） ----------------------------------------

DEFAULT_SCHEMA = {
    "realms": [
        {"name": "练气", "stages": 9, "stage_label": "层"},
        {"name": "筑基", "stages": ["初", "中", "后"]},
        {"name": "结丹", "stages": ["初", "中", "后"]},
        {"name": "元婴", "stages": ["初", "中", "后"]},
    ],
    "lifespan_caps": {"练气": 100, "筑基": 200, "结丹": 500, "元婴": 1000},
    "realm_breakthrough_cost_years": {"筑基": 10, "结丹": 30, "元婴": 80},
    "layer_cost_years": 1,
    "currency": {"name": "灵石", "denoms": ["下品", "中品", "上品"], "rate": 100},
    "source": "default",
}

# 境界轴（行式）：境界…：练气（十三层）→ 筑基（初/中/后期）→ …
_REALM_LINE_RE = re.compile(r"境界[^：:\n]*[：:]\s*(.+)")
_REALM_ITEM_RE = re.compile(r"(\D{1,4}?)（([^（]+?)）")
# 境界轴（列表式）：- 搬血境：淬炼气血……（完美世界等包）
_REALM_BULLET_RE = re.compile(r"^[\s\-*>]*([\u4e00-\u9fa5]{1,6}境)\s*[（:：]")
# 列表式阶梯数：一洞天到十洞天 → 10
_BULLET_STAGES_RE = re.compile(r"一([\u4e00-\u9fa5]{1,2})到十\1")
# 寿元：练气≈百岁 / 筑基≈二百（后续项常省略"岁"）
_LIFESPAN_RE = re.compile(r"([\u4e00-\u9fa5]{1,3})[≈=]\s*([\d一两二三四五六七八九十百千]+)\s*(?:岁|。|，|,|；|;|$|\n)")
# 突破扣年：练气→筑基扣10年（名称可能带破折号/顿号前缀污染）
_BREAKTHROUGH_RE = re.compile(r"(\D{1,4}?)→(\D{1,4}?)扣(\d+)年")


# 每突破一层=扣1年
_LAYER_COST_RE = re.compile(r"每突破一层[＝=]扣(\d+)年")
# 灵石：下品/中品/上品/极品，1:100
_CURRENCY_RE = re.compile(r"灵石[^\n]{0,30}?([\u4e00-\u9fa5品]+(?:/[^\d，。；\n]{1,6})+)[^\n]*?1[:：](\d+)")
# 灵根选项：四灵根/伪灵根、三灵根、天灵根
_SPIRIT_RE = re.compile(r"[（(]?(四灵根|三灵根|双灵根|天灵根|伪灵根)")


def _clean_name(s: str) -> str:
    """剥掉名称两侧的非中文字符（破折号/顿号/括号等解析污染）。"""
    return re.sub(r"^[^\u4e00-\u9fa5]+|[^\u4e00-\u9fa5]+$", "", s)


def _parse_stages(spec: str) -> int | list[str]:
    spec = spec.strip()
    if "/" in spec:                      # 初/中/后期 → ["初","中","后"]
        parts = [p.strip().rstrip("期") for p in spec.split("/")]
        return [p for p in parts if p]
    n = cn_num(spec.replace("层", "").replace("重", ""))
    return n if isinstance(n, int) and n > 0 else 3


def parse_numeric_schema(pack: Pack) -> dict:
    """解析数值体系；任何关键字段缺失即整体回退默认模板。"""
    section = pack.section("numeric")
    if section is None:
        return dict(DEFAULT_SCHEMA)

    body = section.body
    schema: dict = {"source": "parsed"}

    # 境界阶梯
    realms = []
    realm_line = _REALM_LINE_RE.search(body)
    if realm_line:
        for name, spec in _REALM_ITEM_RE.findall(realm_line.group(1)):
            name = _clean_name(name)
            if not name or name in ("升级轴",):
                continue
            stages = _parse_stages(spec)
            entry = {"name": name, "stages": stages}
            if isinstance(stages, int):
                entry["stage_label"] = "层"
            realms.append(entry)
    if not realms:
        # 列表式境界：- 搬血境：……（每条一境，阶梯数从描述推断）
        current: dict | None = None
        for line in body.splitlines():
            m = _REALM_BULLET_RE.match(line)
            if m:
                if current:
                    realms.append(current)
                current = {"name": m.group(1), "stages": ["初", "中", "后"]}
                continue
            if current is not None:
                sm = _BULLET_STAGES_RE.search(line)
                if sm:
                    current["stages"] = 10
                    current["stage_label"] = sm.group(1)
                elif "极境" in line and isinstance(current["stages"], list):
                    current["stages"] = ["初期", "极境"]
        if current:
            realms.append(current)
    if not realms:
        return dict(DEFAULT_SCHEMA)
    schema["realms"] = realms

    # 寿元上限
    caps = {}
    for name, raw in _LIFESPAN_RE.findall(body):
        v = cn_num(raw)
        name = _clean_name(name)
        if v and name:
            caps[name] = v
    schema["lifespan_caps"] = caps or dict(DEFAULT_SCHEMA["lifespan_caps"])

    # 突破扣年
    costs = {}
    for _frm, to, years in _BREAKTHROUGH_RE.findall(body):
        to = _clean_name(to)
        if to:
            costs[to] = int(years)
    schema["realm_breakthrough_cost_years"] = costs

    # 每层扣年
    m = _LAYER_COST_RE.search(body)
    schema["layer_cost_years"] = int(m.group(1)) if m else 1

    # 币制
    currency = dict(DEFAULT_SCHEMA["currency"])
    cm = _CURRENCY_RE.search(body)
    if cm:
        currency["denoms"] = [d for d in cm.group(1).split("/") if d.strip()]
        currency["rate"] = int(cm.group(2))
    schema["currency"] = currency

    # 灵根（数值节或创建步骤里出现的选项）
    spirits = list(dict.fromkeys(_SPIRIT_RE.findall(body)))
    if not spirits:
        opening = pack.section("opening")
        if opening:
            spirits = list(dict.fromkeys(_SPIRIT_RE.findall(opening.body)))
    schema["spirits"] = spirits

    return schema
