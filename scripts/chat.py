"""P1 模型冒烟：逐档验证「能回话」与「能返回结构化 tool_calls」。

出口判据（P1.6）要求三档均通过。用法：

    make chat              # 跑全部可用档位
    make chat TIER=M4      # 只跑一档
"""

import argparse
import asyncio
import os
import pathlib
import sys


def _load_dotenv() -> None:
    """把 .env 灌进环境变量。models.yaml 的 ${VAR} 占位靠它解析。"""
    env = pathlib.Path(".env")
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()

from agentsystem.llm import LLMGateway, Message, get_models_config  # noqa: E402
from agentsystem.llm.stub_tools import QUERY_INVENTORY_STUB, SMOKE_PROMPT  # noqa: E402


async def probe(gateway: LLMGateway, tier: str) -> bool:
    """跑一档，返回是否两项判据都过。"""
    print(f"\n{'─' * 62}\n档位 {tier}")
    try:
        result = await gateway.chat(
            [Message(role="user", content=SMOKE_PROMPT)],
            tier=tier,
            tools=[QUERY_INVENTORY_STUB],
        )
    except Exception as exc:  # noqa: BLE001 —— 冒烟脚本要报出全部失败原因
        print(f"  ❌ 调用失败: {type(exc).__name__}: {exc}")
        return False

    can_reply = bool(result.content) or bool(result.tool_calls)
    has_tool_calls = bool(result.tool_calls)

    print(
        f"  实际档位  : {result.tier_used}"
        + (f"（降级自 {result.degraded_from}）" if result.degraded_from else "")
    )
    print(f"  finish    : {result.finish_reason}")
    print(f"  延迟      : {result.latency_ms} ms")
    print(f"  token     : prompt={result.prompt_tokens} completion={result.completion_tokens}")
    print(f"  {'✅' if can_reply else '❌'} 能回话")
    if has_tool_calls:
        for tc in result.tool_calls:
            print(f"  ✅ tool_calls: {tc.name}({tc.arguments})")
    else:
        print(f"  ❌ 无 tool_calls；content={(result.content or '')[:80]!r}")
    return can_reply and has_tool_calls


async def main() -> int:
    """按参数跑一档或全部可用档。"""
    parser = argparse.ArgumentParser(description="P1 模型冒烟")
    parser.add_argument("--tier", default=os.environ.get("TIER") or None)
    args = parser.parse_args()

    config = get_models_config()
    if config.unavailable_tiers:
        print("⏸ 不可用档位（不计入判据）:")
        for tier, reason in config.unavailable_tiers.items():
            print(f"   {tier}: {reason}")

    tiers = [args.tier] if args.tier else config.available_tiers
    gateway = LLMGateway(config)
    results = {t: await probe(gateway, t) for t in tiers}

    print(f"\n{'═' * 62}\n汇总")
    for tier, ok in results.items():
        print(f"  {'✅' if ok else '❌'} {tier}  {config.tiers[tier].name}")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
