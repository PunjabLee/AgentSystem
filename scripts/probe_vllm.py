"""M1/M2 档的 vLLM 形状探测 —— D1 到位当天跑，几分钟给出结论。

## 为什么值得单独写一个脚本

`config/models.yaml` 里 M1/M2 关 thinking 用的是
``chat_template_kwargs: {enable_thinking: false}`` —— 这个形状是**查文档得来的，
从未实测**。而已实测的两档彼此完全不通用：

  · M3（AutoDL.Art）  顶层 ``enable_thinking: false``     → 思考 1193 → 37 tok
  · M4（DeepSeek）    ``thinking: {type: disabled}``       → 110 → 47 tok
  · M0（Ollama /v1）  **关不掉**，四种方式实测全败

三个端点三种结论，没有任何理由相信第四个会和其中之一相同。D1 到位那天
现查现试，大概率要耗掉半天；本脚本把它压缩成一次运行。

## 判据

对每个候选形状发同一个问题，比 ``completion_tokens``：
显著低于基线即为**生效**。不看返回文本 —— thinking 内容是否回显因端点而异，
token 计量才是跨端点可比的。

## 用法

    uv run python scripts/probe_vllm.py --tier M1

先起 SSH 隧道（见 docs/AUTODL-SPEC.md），确认 base_url 可达。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import pathlib
import sys
from dataclasses import replace
from typing import Any

# 与 chat.py 同样的 .env 加载：models.yaml 的 ${VAR} 占位靠它解析。
for _line in pathlib.Path(".env").read_text(encoding="utf-8").splitlines():
    _line = _line.strip()
    if _line and not _line.startswith("#") and "=" in _line:
        _k, _v = _line.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip())

from agentsystem.llm import LLMGateway, Message  # noqa: E402 —— 必须在 .env 加载之后
from agentsystem.llm.stub_tools import QUERY_INVENTORY_STUB, SMOKE_PROMPT  # noqa: E402

#: 能稳定诱发思考的问题：需要多步推理，但答案很短。
#: 若问题本身答案就长，thinking 的增量会被淹没在正文里。
THINKING_PROMPT = "一条产线每小时产 120 平米，一张订单 3000 平米，需要几小时？只回答数字。"

#: 候选形状。前三个是已在别处实测过的，后两个是常见变体。
CANDIDATES: list[tuple[str, dict[str, Any]]] = [
    ("基线（不关）", {}),
    (
        "chat_template_kwargs.enable_thinking  ← 当前 M1/M2 配置",
        {"chat_template_kwargs": {"enable_thinking": False}},
    ),
    ("顶层 enable_thinking                   ← M3 实测有效", {"enable_thinking": False}),
    ("thinking.type=disabled                 ← M4 实测有效", {"thinking": {"type": "disabled"}}),
    ("chat_template_kwargs.thinking", {"chat_template_kwargs": {"thinking": False}}),
]


async def _probe(gw: LLMGateway, tier: str, extra: dict[str, Any]) -> tuple[int | None, str | None]:
    """用指定的 extra_body 发一次请求，返回 (completion_tokens, 错误)。

    形状不被端点接受时通常是 400 —— 那是**有效信息**（说明该形状不支持），
    不是脚本故障，故捕获后继续试下一个。

    ⚠️ 配置的替换刻意放在 try **之外**。曾把它写在 try 里并直接赋值
       ``cfg.extra_body = extra`` —— TierConfig 是 frozen dataclass，抛
       FrozenInstanceError，又被下面宽泛的 except 吞成「该形状不支持」，
       于是五个候选全报 ✗。拿已实测的 M4 做对照才发现；否则 D1 当天会
       对着五个 ✗ 去查端点，而问题在脚本里。

       教训：宽泛的 except 用来表达「候选不可用」时，别让脚本自身的错误
       也落进同一个筐。
    """
    # frozen dataclass 要换值就造新实例，替换字典里的引用，而不是给字段赋值。
    gw._config.tiers[tier] = replace(gw._config.tiers[tier], extra_body=extra)
    try:
        r = await gw.chat(
            [Message(role="user", content=THINKING_PROMPT)], tier=tier, max_tokens=2048
        )
        return r.completion_tokens, None
    except Exception as exc:  # noqa: BLE001 —— 只表达「该端点不接受这个形状」
        return None, f"{type(exc).__name__}: {str(exc)[:90]}"


async def main() -> int:
    """逐个候选形状探测，并附带验证 tool_calls。"""
    parser = argparse.ArgumentParser(description="M1/M2 vLLM 形状探测")
    # 允许指定任意档：拿已实测的 M3/M4 当对照跑一遍，可在 D1 到位**之前**
    # 验证本脚本的判定逻辑本身是对的 —— 否则那天脚本失灵，等于没写。
    parser.add_argument("--tier", default="M1", choices=["M0", "M1", "M2", "M3", "M4"])
    args = parser.parse_args()

    async with LLMGateway() as gw:
        cfg = gw._config.tiers.get(args.tier)
        if cfg is None or not cfg.available:
            reason = gw._config.unavailable_tiers.get(args.tier, "未在配置中")
            print(f"❌ {args.tier} 不可用：{reason}")
            print("   D1（AutoDL 实例）到位并起好 SSH 隧道后再跑。")
            return 1

        print(f"档位 {args.tier} · {cfg.name} · {cfg.base_url}\n")
        original = cfg  # 整个 TierConfig，不是 extra_body 字段
        results: list[tuple[str, int | None, str | None]] = []
        for label, extra in CANDIDATES:
            tokens, err = await _probe(gw, args.tier, extra)
            results.append((label, tokens, err))
            shown = f"{tokens} tok" if tokens is not None else f"✗ {err}"
            print(f"  {label:52} {shown}")
        gw._config.tiers[args.tier] = original  # 还原，下面的 tool_calls 用原配置

        baseline = results[0][1]
        print()
        if baseline is None:
            print("❌ 基线就失败了，端点不可达或模型名不对 —— 先解决连通性。")
            return 1

        # 判据：显著低于基线。取 60% 是因为 M3 实测降幅达 97%、M4 达 57%，
        # 真正生效的形状降幅很大，不需要精细阈值。
        working = [(lb, tk) for lb, tk, _ in results[1:] if tk is not None and tk < baseline * 0.6]
        if working:
            print(f"✅ 生效的形状（基线 {baseline} tok）：")
            for lb, tk in working:
                drop = (1 - tk / baseline) * 100
                name = lb.split("←")[0].strip()
                print(f"     {name:40} {tk} tok  ↓{drop:.0f}%")
            print(f"\n   把 config/models.yaml 的 {args.tier}.extra_body 改成上面第一个。")
        else:
            print(f"⚠️  没有形状能显著降低 token（基线 {baseline}）。")
            print("   与 M0 相同：该端点关不掉 thinking。此时应")
            print("   ① 确认 max_tokens_floor 足够（M0 的教训：80 时思考没写完就被截断）")
            print("   ② 在评测报告中说明该档含思考开销，与其他档不可直接比 token 成本")

        print("\n── 附带验证 tool_calls ──")
        r = await gw.chat(
            [Message(role="user", content=SMOKE_PROMPT)],
            tier=args.tier,
            tools=[QUERY_INVENTORY_STUB],
        )
        ok = r.finish_reason == "tool_calls" and r.tool_calls
        mark = "✅" if ok else "❌"
        print(f"  {mark} finish_reason={r.finish_reason} calls={len(r.tool_calls)}")
        if r.tool_calls:
            print(f"     {r.tool_calls[0].name}({r.tool_calls[0].arguments[:70]})")
        return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
