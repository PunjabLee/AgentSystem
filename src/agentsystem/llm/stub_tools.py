"""P1 冒烟用的 stub 工具定义。

P1 出口判据要求「三档均能回话且返回结构化 tool_calls」，但真实业务工具
属 P2。这里给一个形状与 P2 §3 五个只读工具一致、但不连数据库的桩，
让工具调用能力在 P1 就被验证 —— 否则问题会积到 P2 才暴露。

刻意做成**必须调工具才能回答**的形式：问库存而模型手上没有数据，
不调工具就只能编。这样才测得出 tool_calls，而不是测出模型的礼貌。
"""

from agentsystem.llm.types import ToolDef

#: 形状对齐 P2 §3.2 的 query_inventory：枚举收窄、必填项明确。
QUERY_INVENTORY_STUB = ToolDef(
    name="query_inventory",
    description="查询指定产品在指定事业部的可用库存批次。查库存必须调用本工具，不得凭记忆回答。",
    parameters={
        "type": "object",
        "properties": {
            "product_code": {
                "type": "string",
                "description": "产品编码，如 TL-800X800-YH01",
            },
            "bu_code": {
                "type": "string",
                "enum": ["BU-A", "BU-B", "BU-C"],
                "description": "事业部：BU-A 印染 / BU-B 建陶瓷砖 / BU-C 卫浴洁具",
            },
        },
        "required": ["product_code", "bu_code"],
        # additionalProperties: false —— 模型多塞字段时能被 schema 校验发现，
        # 而不是悄悄传到下游。
        "additionalProperties": False,
    },
)

#: 冒烟提问。必须提到具体产品与事业部，否则模型会先反问而非调工具。
SMOKE_PROMPT = "帮我查一下产品 TL-800X800-YH01 在建陶瓷砖事业部（BU-B）还有多少库存？"
