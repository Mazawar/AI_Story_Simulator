"""上下文组装器（DESIGN.md §6.4）：每回合 ~9k token 以内的组装上下文。

结构（稳定前缀在前，KV 缓存友好）：
  system: ENGINE_SYSTEM + 世界观压缩 + 相关角色卡
  user:   状态摘要 + 滚动摘要 + 最近回合 + 锚点动态 + 玩家输入
"""

from __future__ import annotations

from ..pack.models import Pack
from .prompts.wrapper import ENGINE_FULL_WRAPPER, ENGINE_SYSTEM

# 包体量分界：≤ FULL_INJECT_CHARS 的剧本直接全文注入（模型拿到完整设定）
FULL_INJECT_CHARS = 6000

# 预算（字数；中文≈1.4字/token）
WORLD_BUDGET = 1600
CARDS_MAX = 5
CARD_BUDGET = 220
RECENT_TURNS = 6
TURN_BUDGET = 700
SUMMARY_BUDGET = 400


def _clip(text: str, budget: int) -> str:
    text = text.strip()
    return text if len(text) <= budget else text[: budget - 1] + "…"


def assemble_messages(pack: Pack, characters: list[dict], state, recent_turns: list[dict],
                      rolling_summary: str, anchor_block: str,
                      player_input: str, turn: int,
                      *, extra_system: str = "", world_agenda: str = "",
                      player_role: str = "") -> list[dict]:
    """组装引擎模式的一轮上下文。

    state: core.rules.NumericState；recent_turns: [{"input","text"}]（旧→新）。
    世界观文本进入前剥除揭晓点行（剧透隔离）；extra_system 追加剧本级指令
    （身份线/停滞警报）；world_agenda 为世界活性标题清单。
    """
    from ..pack.anchors import strip_reveals

    # ---- 稳定前缀（system） ----------------------------------------------------
    if len(pack.raw_text) <= FULL_INJECT_CHARS:
        # 小包：整个剧本就是系统提示词（用户"给剧本→按设定推演"的主路径）
        system = ENGINE_FULL_WRAPPER + "\n" + pack.raw_text.strip()
    else:
        world = pack.section("world")
        # 无「世界观」章节的包（系统型/未知结构）：兜底取全包前段，
        # 保证模型至少拿到剧本的核心设定
        if world is not None:
            world_text = _clip(strip_reveals(world.body), WORLD_BUDGET)
        else:
            world_text = _clip(strip_reveals(pack.raw_text), WORLD_BUDGET)

        # 相关角色卡：最近回合提及者优先
        recent_text = "".join(f"{t.get('input','')}{t.get('text','')}" for t in recent_turns)
        ranked = sorted(
            characters,
            key=lambda c: 0 if c["name"] in recent_text else 1,
        )
        cards = "\n".join(
            f"【{c['name']} · {_clip(c['desc'], CARD_BUDGET)}】" for c in ranked[:CARDS_MAX]
        )
        system = ENGINE_SYSTEM + (extra_system or "") + f"""

【世界观】
{world_text}

【主要角色】
{cards or '（无角色卡）'}
{chr(10) + chr(10) + '【世界将发生之事】' + chr(10) + world_agenda if world_agenda else ''}
"""

    # ---- 动态后缀（user） -------------------------------------------------------
    items = "、".join(f"{i['name']}×{i.get('count', 1)}" for i in state.inventory[:6]) or "无"
    state_digest = (
        f"回合{turn}｜{state.realm_name}｜灵根{state.spirit or '—'}｜"
        f"寿元余{state.lifespan_left:.0f}年｜灵石{state.stones:g}｜"
        f"修为{state.progress:.0f}/100｜地点{state.location or '—'}｜物品：{items}"
    )

    history_lines = []
    for t in recent_turns[-RECENT_TURNS:]:
        history_lines.append(f"玩家：{_clip(t.get('input', ''), 80)}")
        history_lines.append(f"剧情：{_clip(t.get('text', ''), TURN_BUDGET)}")
    history_text = "\n".join(history_lines) or "（故事刚刚开始）"

    summary_text = _clip(rolling_summary, SUMMARY_BUDGET) if rolling_summary else ""

    role_line = f"【你的角色】{player_role}\n" if player_role else ""
    user = f"""{role_line}【当前状态】{state_digest}
{f'【前情摘要】{summary_text}' if summary_text else ''}
【最近回合】
{history_text}
{f'【世界动态】{anchor_block}' if anchor_block else ''}
【玩家行动】{player_input}

【推进纪律】上述最近回合里已写过的事不得重演；本轮 narrative 必须落到具体新事件上
（who/what/where 有其一即可）。请输出叙事、effects 与 choices。"""

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def estimate_tokens(messages: list[dict]) -> int:
    return int(sum(len(m["content"]) for m in messages) / 1.4)


# ---- 滚动摘要 -----------------------------------------------------------------

SUMMARY_PROMPT = """把下面的剧情压缩成不超过300字的前情摘要：保留主角状态变化、
重要事件、人物关系与未解决的冲突；用第三人称；不要任何开场白。直接输出摘要。"""


def summarize_turns(backend, turns: list[dict], previous_summary: str) -> str:
    """用 LLM 压缩早期回合（失败时回退为截断拼接）。"""
    transcript = (f"【旧摘要】{previous_summary}\n" if previous_summary else "")
    transcript += "\n".join(
        f"玩家：{t.get('input', '')}\n剧情：{t.get('text', '')[:300]}" for t in turns
    )
    transcript = _clip(transcript, 4000)
    try:
        text = backend.generate(
            [{"role": "system", "content": SUMMARY_PROMPT},
             {"role": "user", "content": transcript}],
            max_tokens=400, temperature=0.3,
        )
        return _clip(text.strip(), SUMMARY_BUDGET + 200)
    except Exception:
        # 回退：确定性截断摘要
        merged = "；".join(t.get("text", "")[:60] for t in turns[-8:])
        return _clip((previous_summary + "；" if previous_summary else "") + merged,
                     SUMMARY_BUDGET)
