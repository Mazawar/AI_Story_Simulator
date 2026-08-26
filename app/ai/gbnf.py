"""裁决协议 GBNF 语法（DESIGN.md §9 回合流程第 3 步）。

强制 LLM 输出 {"narrative": str, "effects": [...]} 的合法 JSON；
llama-cpp-python 0.3.35 的 grammar 参数需 LlamaGrammar.from_string()。
注意：GBNF 无内建 string/number 规则，必须显式定义；
语法编译失败会导致采样器空指针崩溃，from_string 即编译验证。
"""

ADJUDICATION_GRAMMAR = r'''
root ::= "{" ws "\"narrative\"" ws ":" ws string ws "," ws "\"effects\"" ws ":" ws effects ws "," ws "\"choices\"" ws ":" ws choices ws "}"
effects ::= "[" ws "]" | "[" ws effect (ws "," ws effect)* ws "]"
choices ::= "[" ws "]" | "[" ws string (ws "," ws string)* ws "]"
effect ::= delta | item | flag | anchor
delta ::= "{" ws "\"ref\"" ws ":" ws string ws "," ws "\"op\"" ws ":" ws op ws "," ws "\"v\"" ws ":" ws number ws "," ws "\"reason\"" ws ":" ws string ws "}"
item ::= "{" ws "\"item\"" ws ":" ws string ws "," ws "\"action\"" ws ":" ws ("\"add\"" | "\"remove\"") ws ("}" | ws "," ws "\"note\"" ws ":" ws string ws "}")
flag ::= "{" ws "\"flag\"" ws ":" ws string ws "," ws "\"value\"" ws ":" ws ("\"true\"" | "\"false\"") ws "}"
anchor ::= "{" ws "\"anchor\"" ws ":" ws string ws "}"
op ::= "\"+\"" | "\"-\""
string ::= "\"" ( [^"\\\x7F\x00-\x1F] | "\\" (["\\bfnrt] | "u" [0-9a-fA-F] [0-9a-fA-F] [0-9a-fA-F] [0-9a-fA-F]) )* "\""
number ::= ("-"? ([0-9] | [1-9] [0-9]*)) ("." [0-9]+)?
ws ::= [ \t\n]*
'''


def grammar_object():
    """编译并返回 LlamaGrammar（from_string 内部完成编译验证）。"""
    from llama_cpp import LlamaGrammar

    return LlamaGrammar.from_string(ADJUDICATION_GRAMMAR)


def _extract_complete_objects(text: str) -> list[dict]:
    """从文本中提取所有配平的 {...} 对象并解析（effects 截断抢救用）。"""
    import json as _json

    out: list[dict] = []
    i = 0
    while i < len(text):
        if text[i] != "{":
            i += 1
            continue
        depth, j, in_str, esc = 0, i, False, False
        while j < len(text):
            ch = text[j]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            else:
                if ch == '"':
                    in_str = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        break
            j += 1
        if j >= len(text):
            break                      # 尾部未闭合 → 后面不会有更多完整对象
        try:
            obj = _json.loads(text[i : j + 1])
            if isinstance(obj, dict):
                out.append(obj)
        except _json.JSONDecodeError:
            pass
        i = j + 1
    return out


def salvage_adjudication(text: str) -> dict | None:
    """从截断的裁决 JSON 中抢救可用字段（生成撞上 max_tokens 时的兜底）。

    narrative 字符串未闭合时补引号闭合；已配平的 effects 对象一并保留。
    """
    import json as _json

    start = text.find('"narrative"')
    if start < 0:
        return None
    colon = text.find(':', start)
    if colon < 0:
        return None
    rest = text[colon + 1:].lstrip()
    if not rest.startswith('"'):
        return None
    # 找到未被转义终结的字符串结尾；没有则补齐
    i, closed = 1, False
    while i < len(rest):
        ch = rest[i]
        if ch == '\\':
            i += 2
            continue
        if ch == '"':
            closed = True
            break
        i += 1
    # 切片必须包含闭引号本身
    value_text = rest[: i + 1] if closed else rest[:i] + '"'
    try:
        narrative = _json.loads(value_text)
    except _json.JSONDecodeError:
        return None

    effects: list[dict] = []
    eff_marker = text.find('"effects"', start)
    if eff_marker >= 0 and closed:
        # narrative 已完整闭合，截断发生在 effects 数组内 → 抢救其中的完整对象
        for obj in _extract_complete_objects(text[eff_marker:]):
            if any(k in obj for k in ("ref", "item", "flag", "anchor", "location")):
                effects.append(obj)

    return {"narrative": narrative.strip() or "（命运的笔锋在此处顿了顿。）",
            "effects": effects}
