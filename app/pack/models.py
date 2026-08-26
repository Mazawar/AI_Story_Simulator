"""剧本包数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PackSection:
    """剧本包的一个章节（如「一、世界观」）。"""

    num: str            # 中文序号："一"、"二"…；序言固定为 "0"
    key: str            # 归一化键：preamble / world / characters / numeric / panels /
                        # output_format / constraints / opening / dynamics / saving
                        # 未识别的章节使用 raw_<num>
    title: str          # 原始标题文本
    body: str           # 章节正文（含子标记【…】）
    order_idx: int = 0

    def char_count(self) -> int:
        return len(self.body)


# 章节标题 → 归一化 key 的映射表（Pack Spec v1，见 DESIGN.md §6.1/§8.1）
SECTION_KEY_MAP: dict[str, str] = {
    "世界观": "world",
    "角色卡": "characters",
    "数值体系": "numeric",
    "状态播报": "panels",       # 「四、状态播报与面板」
    "播报": "panels",
    "面板": "panels",
    "输出格式": "output_format",
    "约束": "constraints",
    "首轮": "opening",          # 「七、首轮输出」
    "世界活性": "dynamics",     # 「八、世界活性机制」
    "活性": "dynamics",
    "存档": "saving",           # 「九、存档与续玩」
}


def normalize_section_key(title: str) -> str:
    for needle, key in SECTION_KEY_MAP.items():
        if needle in title:
            return key
    return "unknown"


@dataclass
class Pack:
    """一个剧本包：整包文档 + 切分出的章节。"""

    title: str
    file_path: str
    raw_text: str
    sections: list[PackSection] = field(default_factory=list)

    def section(self, key: str) -> PackSection | None:
        for s in self.sections:
            if s.key == key:
                return s
        return None

    def system_prompt(self) -> str:
        """直通模式的系统提示词：整包原文。"""
        return self.raw_text
