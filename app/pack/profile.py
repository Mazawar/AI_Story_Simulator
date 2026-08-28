"""AI 剧本配置生成器（用户核心诉求：通读剧本 → 生成该剧本的配置 JSON）。

不再写死题材结构：LLM 通读任意剧本包，产出标准化 PackProfile——
资源体系（货币/生命/经验/等级…）、境界轴、面板定义（含 LaTeX 面板的
字段化转写）、角色卡。引擎按配置驱动数值白名单与面板，前端按配置渲染。

确定性解析（parse_numeric_schema 等）降级为 profile 不可用时的兜底。
"""

from __future__ import annotations

from .models import Pack

PROFILE_PROMPT = """你是剧本配置生成器。通读下面的剧本全文，把它转换成一份引擎可执行的配置 JSON。
只输出 JSON，禁止任何 JSON 之外的文字。字段要求：

{
 "genre": "题材一句话（如 修仙/末日生存/武侠）",
 "resources": [
   {"ref": "资源名", "init": 初始数值, "max": 上限数值或省略, "kind": "vital|currency|progress"}
 ],
 "realm_axis": null 或 {
   "realms": [{"name": "境界名", "stages": 层数数字 或 ["初","中","后期"]}],
   "lifespan_caps": {"境界名": 寿元上限},
   "layer_cost_years": 每推进一小层消耗的年数或天数（无时间消耗填 0）,
   "realm_breakthrough_cost_years": {"目标境界名": 消耗}
 },
 "panels": [
   {"key": "cultivator", "title": "面板标题", "fields": [
      {"label": "字段名", "source": "数据源表达式"}
   ]}
 ],
 "characters": [{"name": "角色名", "desc": "一句话设定"}],
 "creation": ["创建步骤问题1", "创建步骤问题2"]
}

规则：
- resources：把剧本里的数值体系全部列出来（生命/体力/饥饿/货币/物资/灵石/经验……）。
  kind：vital=有上限的状态值（生命/饥饿），currency=可积累的财富（灵石/物资/金钱），
  progress=成长经验（修为/经验值）。ref 用剧本里的原名。
- realm_axis：剧本有明确等级/境界阶梯才填；没有就 null。
- panels：剧本里有面板/状态栏描述（含 LaTeX 样式面板）就逐字段转写成 fields；
  没有就给一个默认 {"key":"status","title":"状态","fields":[{"label":"生命","source":"res:生命"},{"label":"地点","source":"location"}]}。
  source 取值 ONLY：realm（境界文本）| progress（经验进度）| lifespan（剩余寿元）|
  res:资源名（对应 resources 里的 ref）| location | inventory（物品列表）|
  flags:前缀（收集该前缀的剧情标记）。
- characters：剧本明确给出的角色卡（没有就空数组）。
- creation：剧本要求开局向玩家依次询问的创建问题（没有就空数组）。

剧本全文：
"""


def _norm_resources(raw) -> list[dict]:
    out, seen = [], set()
    for r in raw or []:
        if not isinstance(r, dict):
            continue
        ref = str(r.get("ref", "")).strip()[:12]
        if not ref or ref in seen:
            continue
        seen.add(ref)
        try:
            init = float(r.get("init", 0))
        except (TypeError, ValueError):
            init = 0.0
        entry: dict = {"ref": ref, "init": init}
        if r.get("max") is not None:
            try:
                mx = float(r["max"])
                if mx > 0:
                    entry["max"] = mx
            except (TypeError, ValueError):
                pass
        kind = str(r.get("kind", "vital")).strip().lower()
        if kind not in ("vital", "currency", "progress"):
            kind = "vital"
        # progress 语义只属于境界轴的成长值；无境界轴时（如末日包的饥饿/口渴）
        # 一律归为 vital，避免误入经验通道
        if kind == "progress":
            kind = "vital"
        entry["kind"] = kind
        out.append(entry)
    if not any(r.get("kind") == "vital" for r in out):
        out.insert(0, {"ref": "生命", "init": 100.0, "max": 100.0, "kind": "vital"})
    return out


def _norm_panels(raw, resource_refs: list[str]) -> list[dict]:
    valid_sources = {"realm", "progress", "lifespan", "location", "inventory"}
    panels = []
    for p in raw or []:
        if not isinstance(p, dict):
            continue
        fields = []
        for f in (p.get("fields") or [])[:12]:
            if not isinstance(f, dict):
                continue
            label = str(f.get("label", "")).strip()[:12]
            source = str(f.get("source", "")).strip()
            if not label:
                continue
            if source in valid_sources or source.startswith(("res:", "flags:")):
                # res: 引用必须命中已声明资源，否则引擎取不到真数据
                if source.startswith("res:") and source[4:] not in resource_refs:
                    continue
                fields.append({"label": label, "source": source})
        if len(fields) >= 1:
            panels.append({"key": str(p.get("key", "status"))[:16],
                           "title": str(p.get("title", "状态"))[:12],
                           "fields": fields})
        if len(panels) >= 4:
            break
    return panels


def normalize_profile(raw: dict) -> dict | None:
    """LLM 原始输出 → 校验归一化为引擎 schema（+panels 附加段）。失败返回 None。"""
    if not isinstance(raw, dict) or not isinstance(raw.get("resources"), list):
        return None
    resources = _norm_resources(raw.get("resources"))
    refs = [r["ref"] for r in resources]

    realm_axis = raw.get("realm_axis") or None
    realms: list[dict] = []
    lifespan_caps: dict = {}
    breakthrough: dict = {}
    layer_cost = 0
    if isinstance(realm_axis, dict):
        for r in (realm_axis.get("realms") or [])[:8]:
            if isinstance(r, dict) and str(r.get("name", "")).strip():
                stages = r.get("stages", 3)
                if not isinstance(stages, (int, list)):
                    stages = 3
                realms.append({"name": str(r["name"]).strip()[:6], "stages": stages})
        caps = realm_axis.get("lifespan_caps") or {}
        if isinstance(caps, dict):
            lifespan_caps = {str(k)[:6]: v for k, v in list(caps.items())[:10]
                             if isinstance(v, (int, float))}
        bt = realm_axis.get("realm_breakthrough_cost_years") or {}
        if isinstance(bt, dict):
            breakthrough = {str(k)[:6]: int(v) for k, v in list(bt.items())[:10]
                            if isinstance(v, (int, float)) and v >= 0}
        try:
            layer_cost = int(realm_axis.get("layer_cost_years", 0) or 0)
        except (TypeError, ValueError):
            layer_cost = 0

    schema: dict = {
        "source": "profile",
        "genre": str(raw.get("genre", ""))[:24],
        "realms": realms,
        "resources": resources,
        "lifespan_caps": lifespan_caps,
        "realm_breakthrough_cost_years": breakthrough,
        "layer_cost_years": layer_cost,
        "panels": _norm_panels(raw.get("panels"), refs),
    }
    if isinstance(raw.get("characters"), list):
        chars = [{"name": str(c.get("name", "")).strip()[:12],
                  "desc": str(c.get("desc", "")).strip()[:80]}
                 for c in raw["characters"][:20] if isinstance(c, dict) and c.get("name")]
        schema["characters"] = chars
    return schema


def build_pack_profile(pack: Pack, backend) -> dict | None:
    """LLM 通读剧本生成配置；任何失败返回 None（调用方走确定性兜底）。

    注意必须用自由文本生成 + repair_json——不能走 generate_json 的裁决
    GBNF 语法（会把 profile 结构硬掰成 narrative/effects 形状导致失败）。
    """
    from ..ai.backend import repair_json

    text = pack.raw_text[:20000]
    messages = [
        {"role": "system", "content": PROFILE_PROMPT},
        {"role": "user", "content": text},
    ]
    for attempt, max_tokens in ((1, 2200), (2, 2800)):
        try:
            raw_text = backend.generate(messages, max_tokens=max_tokens, temperature=0.2)
            raw = repair_json(raw_text)
        except Exception:
            continue
        profile = normalize_profile(raw)
        if profile is not None:
            return profile
    return None
