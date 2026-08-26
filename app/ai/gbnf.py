"""裁决协议 GBNF 语法（DESIGN.md §9 回合流程第 3 步）。

强制 LLM 输出 {"narrative": str, "effects": [...]} 的合法 JSON；
llama-cpp-python 0.3.35 的 grammar 参数需 LlamaGrammar.from_string()。
注意：GBNF 无内建 string/number 规则，必须显式定义；
语法编译失败会导致采样器空指针崩溃，from_string 即编译验证。
"""

ADJUDICATION_GRAMMAR = r'''
root ::= "{" ws "\"narrative\"" ws ":" ws string ws "," ws "\"effects\"" ws ":" ws effects ws "}"
effects ::= "[" ws "]" | "[" ws effect (ws "," ws effect)* ws "]"
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
