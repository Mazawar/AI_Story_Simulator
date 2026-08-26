"""剧本包加载器：编码检测 + 章节切分。

剧本包是"游戏主持人系统提示词"文档（见 DESIGN.md §6），不是小说，
不走 ingest/ 流水线。
"""

from __future__ import annotations

import re
from pathlib import Path

from .models import Pack, PackSection, normalize_section_key

# 顶层章节标题：「一、世界观」/「【一、世界观】」两种形态
_HEADER_RE = re.compile(r"^【?\s*([一二三四五六七八九十]{1,3})\s*、\s*(.+?)\s*】?\s*$")
# 标题候选行：【凡人修仙传：人界篇 】模拟人生 / 《剑来 · 开放世界》人生模拟器
_TITLE_RE = re.compile(r"^【\s*(.+?)\s*】|《\s*(.+?)\s*》")

_ENCODINGS = ("utf-8", "utf-8-sig", "gb18030")


def read_text(path: Path) -> str:
    """按 UTF-8 → GB18030 顺序尝试解码，避免引入 chardet 依赖。"""
    raw = path.read_bytes()
    for enc in _ENCODINGS:
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError(
        "pack", raw, 0, 1, f"无法识别编码：{path.name}（支持 {'/'.join(_ENCODINGS)}）"
    )


def split_sections(text: str) -> list[PackSection]:
    """按顶层章节标题切分；标题行之前的内容归入 preamble。"""
    sections: list[PackSection] = []
    preamble_lines: list[str] = []
    current: PackSection | None = None
    order = 0

    for line in text.splitlines():
        header = _HEADER_RE.match(line.strip())
        if header:
            if current is not None:
                current.body = current.body.rstrip()
                sections.append(current)
            num, title = header.group(1), header.group(2).strip()
            order += 1
            current = PackSection(
                num=num,
                key=normalize_section_key(title),
                title=title,
                body="",
                order_idx=order,
            )
        elif current is not None:
            current.body += line + "\n"
        else:
            preamble_lines.append(line)

    if current is not None:
        sections.append(current)

    if any(l.strip() for l in preamble_lines):
        sections.insert(
            0,
            PackSection(
                num="0",
                key="preamble",
                title="序言",
                body="\n".join(preamble_lines).strip(),
                order_idx=0,
            ),
        )

    # 标题行并入序言正文，避免内容丢失
    return sections


def extract_title(text: str, fallback: str) -> str:
    for line in text.splitlines():
        m = _TITLE_RE.search(line.strip())
        if m:
            return (m.group(1) or m.group(2) or "").strip() or fallback
    return fallback


def load_pack(path: Path | str) -> Pack:
    path = Path(path)
    text = read_text(path).strip()
    return Pack(
        title=extract_title(text, fallback=path.stem),
        file_path=str(path),
        raw_text=text,
        sections=split_sections(text),
    )


def load_packs(folder: Path | str, suffixes=(".txt", ".md")) -> list[Pack]:
    """加载目录下全部剧本包，按文件名排序。"""
    folder = Path(folder)
    if not folder.is_dir():
        return []
    return [load_pack(p) for p in sorted(folder.iterdir()) if p.suffix.lower() in suffixes]
