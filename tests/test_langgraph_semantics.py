"""钉住 LangGraph 的执行语义 —— 二次确认的正确性建立在这些语义之上。

为什么要为第三方框架写测试：宪法第一条的「二次确认」落到实现上，等价于
「`interrupt()` 挂起 → 人确认 → 恢复 → 提交」。这条链的安全性完全取决于
**恢复时哪些代码会重跑**。这不是我们能控制的行为，是框架给的；框架换个
版本就可能变，而变了之后症状是「偶发重复下单」——最难查的那类。

所以这里不测我们的代码，测的是我们**赖以成立的假设**。升级 LangGraph 时
这几条一红，就知道下单子图的拓扑约束要重新审。

实测环境：langgraph 1.2.11 + Python 3.14.6。
注意 PyPI 上 1.2.11 的 classifiers 只声明到 3.13，3.14 属实测可用而非官方背书。
"""

import operator
from typing import Annotated, TypedDict

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt


class _S(TypedDict):
    log: Annotated[list[str], operator.add]


def _thread(name: str) -> dict:
    """每个用例用独立 thread_id，避免检查点互相污染。"""
    return {"configurable": {"thread_id": name}}


def test_node_containing_interrupt_replays_from_its_start() -> None:
    """🔴 含 interrupt 的节点，恢复时**从头重放**。

    这是本文件最重要的一条。它直接决定下单子图的节点怎么切：
    写库动作若与 interrupt 放在同一节点的前半段，恢复后会执行两次 ——
    重复下单，而且是「用户确认了一次」的情况下发生，最难解释。

    因此约束是：**确认节点只做确认，写操作单独成节点放在其后。**
    """
    before: list[int] = []
    after: list[int] = []

    def confirm(state: _S) -> _S:
        before.append(1)
        answer = interrupt({"q": "确认?"})
        after.append(1)
        return {"log": [f"ans={answer}"]}

    g = StateGraph(_S)
    g.add_node("confirm", confirm)
    g.add_edge(START, "confirm")
    g.add_edge("confirm", END)
    app = g.compile(checkpointer=InMemorySaver())
    cfg = _thread("replay")

    app.invoke({"log": []}, cfg)
    assert (len(before), len(after)) == (1, 0), "中断时：前半段跑过一次，后半段未跑"

    app.invoke(Command(resume="yes"), cfg)
    assert len(before) == 2, "🔴 前半段被重放 —— 写操作绝不能放在这里"
    assert len(after) == 1, "后半段只跑一次"


def test_nodes_completed_before_interrupt_do_not_rerun() -> None:
    """中断**之前已完成的其他节点**不会重跑。

    与上一条合起来才是完整图景：重放的边界是「当前节点」，不是「整张图」。
    否则每次确认都要把前面的库存校验、槽位抽取全跑一遍，代价完全不同。
    """
    ran: list[str] = []

    def prepare(state: _S) -> _S:
        ran.append("prepare")
        return {"log": ["prepare"]}

    def confirm(state: _S) -> _S:
        interrupt({"q": "确认?"})
        return {"log": ["confirm"]}

    g = StateGraph(_S)
    g.add_node("prepare", prepare)
    g.add_node("confirm", confirm)
    g.add_edge(START, "prepare")
    g.add_edge("prepare", "confirm")
    g.add_edge("confirm", END)
    app = g.compile(checkpointer=InMemorySaver())
    cfg = _thread("no-rerun")

    app.invoke({"log": []}, cfg)
    app.invoke(Command(resume="yes"), cfg)
    assert ran == ["prepare"], "中断前已完成的节点不应重跑"


def test_interrupt_surfaces_payload_for_the_confirmation_prompt() -> None:
    """中断时能把结构化载荷交给上层 —— 二次确认的提示内容靠它。

    宪法第一条要求确认时向用户展示「将要发生什么」。载荷若取不到，
    就只能由模型复述，而模型可能已被检索内容污染（宪法第十条）。
    """

    def confirm(state: _S) -> _S:
        interrupt({"action": "create_order", "qty": 500})
        return {"log": ["ok"]}

    g = StateGraph(_S)
    g.add_node("confirm", confirm)
    g.add_edge(START, "confirm")
    g.add_edge("confirm", END)
    app = g.compile(checkpointer=InMemorySaver())

    out = app.invoke({"log": []}, _thread("payload"))
    assert "__interrupt__" in out
    payload = out["__interrupt__"][0].value
    assert payload == {"action": "create_order", "qty": 500}


def test_interrupt_suspends_even_without_checkpointer() -> None:
    """无 checkpointer 时 interrupt **仍然挂起**，不会静默越过。

    这是一条安全正面性质，值得钉住：最坏的故障模式本来会是「checkpointer
    配错了，于是 interrupt 变成空操作，写操作直接执行」—— 二次确认在无人
    察觉的情况下失效。实测确认不会发生：节点在 interrupt 处停住，
    确认之后的代码不执行。
    """
    reached_after: list[str] = []

    def confirm(state: _S) -> _S:
        interrupt({"q": "确认?"})
        reached_after.append("越过了")  # 跑到这里即为静默旁路
        return {"log": ["done"]}

    g = StateGraph(_S)
    g.add_node("confirm", confirm)
    g.add_edge(START, "confirm")
    g.add_edge("confirm", END)
    app = g.compile()  # 刻意不给 checkpointer

    out = app.invoke({"log": []}, _thread("no-ckpt"))
    assert reached_after == [], "🔴 interrupt 被静默越过 —— 二次确认形同虚设"
    assert "__interrupt__" in out


def test_resume_requires_a_checkpointer() -> None:
    """恢复必须有 checkpointer，且失败是**显式报错**而非静默降级。

    钉住这条是因为「只读子图关闭 checkpointer 以省写库」是设计里明确的
    优化项（主设计 §11）。哪天有人顺手把写子图的 checkpointer 也关了，
    这条会红 —— 而不是等到二次确认在演示现场恢复不了才发现。
    """

    def confirm(state: _S) -> _S:
        interrupt({"q": "确认?"})
        return {"log": ["ok"]}

    g = StateGraph(_S)
    g.add_node("confirm", confirm)
    g.add_edge(START, "confirm")
    g.add_edge("confirm", END)
    app = g.compile()
    cfg = _thread("resume-no-ckpt")
    app.invoke({"log": []}, cfg)

    with pytest.raises(RuntimeError, match="checkpointer"):
        app.invoke(Command(resume="yes"), cfg)
