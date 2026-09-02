"""models.yaml 的加载与校验（P1 详细设计 §2.1）。

模型端点 URL **只允许**出现在 ``config/models.yaml`` 与本模块
（宪法第六条，CI 机械检查 §6）。业务代码里出现任何 base_url 即违规。
"""

import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml

DEFAULT_MODELS_FILE = Path("config/models.yaml")

#: ``${VAR}`` 占位形状。api_key 必须长这样，或显式为 null。
_ENV_PLACEHOLDER = re.compile(r"^\$\{([A-Z0-9_]+)\}$")


class ModelConfigError(Exception):
    """配置非法。

    刻意不继承 AppError —— 它发生在启动期，没有请求上下文，也不该被
    包装成给用户看的响应。启动失败要吵，不要静悄悄降级。
    """


@dataclass(frozen=True, slots=True)
class TierConfig:
    """一个模型档位。"""

    tier: str
    name: str
    base_url: str
    model: str
    role: Literal["dev", "benchmark"]
    max_tokens_floor: int
    extra_body: dict[str, Any]
    #: 已解析出的明文密钥。仅存在于内存，来源必须是环境变量。
    api_key: str | None = None
    #: 非 None 表示本档当前不可用（如密钥未配置），值即原因。
    #: 档位不可用**不阻止进程启动** —— 否则一个尚未开通的档位会让全部
    #: 可用档位一起用不了。真去调用它时才失败，且报的仍是根因而非 401。
    unavailable_reason: str | None = None

    @property
    def is_benchmark(self) -> bool:
        """是否参与对比结论。dev 档（M0）不参与。"""
        return self.role == "benchmark"

    @property
    def available(self) -> bool:
        """本档是否可用。"""
        return self.unavailable_reason is None


@dataclass(frozen=True, slots=True)
class ModelsConfig:
    """整份模型配置。"""

    default_tier: str
    tiers: dict[str, TierConfig]
    fallback_chain: tuple[str, ...]

    def get(self, tier: str) -> TierConfig:
        """按档位名取配置。

        Raises:
            ModelConfigError: 档位不存在，或该档当前不可用。
        """
        if tier not in self.tiers:
            raise ModelConfigError(f"未知档位 {tier}，可用：{sorted(self.tiers)}")
        cfg = self.tiers[tier]
        if not cfg.available:
            raise ModelConfigError(f"档位 {tier}（{cfg.name}）当前不可用：{cfg.unavailable_reason}")
        return cfg

    @property
    def available_tiers(self) -> list[str]:
        """当前可用的档位，按名称排序。"""
        return sorted(t for t, c in self.tiers.items() if c.available)

    @property
    def unavailable_tiers(self) -> dict[str, str]:
        """不可用档位到原因的映射。启动日志应打印它，否则"档位悄悄少了"无人察觉。"""
        return {t: c.unavailable_reason for t, c in self.tiers.items() if not c.available}


def _resolve_api_key(tier: str, raw: Any) -> tuple[str | None, str | None]:
    """把 ``${VAR}`` 占位解析成环境变量值。

    🔴 这是安全评审的阻塞项：models.yaml 被 git 跟踪，宪法第七条针对
    .env 的检查拦不住写进被跟踪文件的密钥。故只接受两种取值 —— null，
    或严格的 ``${VAR}`` 占位。任何其他字符串一律视为明文密钥并拒绝启动。

    Returns:
        ``(明文密钥, 不可用原因)``。两者恰有一个为 None。

    Raises:
        ModelConfigError: 值是明文密钥 —— 这一条永远拒绝启动，与档位
            是否在用无关：密钥一旦写进被跟踪文件就已经泄漏了。
    """
    if raw is None:
        return None, None
    if not isinstance(raw, str):
        raise ModelConfigError(f"{tier}.api_key 必须是字符串占位或 null")
    match = _ENV_PLACEHOLDER.match(raw)
    if match is None:
        raise ModelConfigError(
            f"{tier}.api_key 疑似明文密钥。本文件被 git 跟踪，"
            f"密钥必须写成 ${{ENV_VAR}} 占位（宪法第七条）"
        )
    var = match.group(1)
    value = os.environ.get(var)
    if not value:
        # 不报错、只标记不可用。若在此处 raise，一个尚未开通的档位
        # （如 M1/M2 等 AutoDL 实例）会让 M3/M4 也一起起不来。
        # 代价由 ModelsConfig.get() 兜住：真去用它时立刻失败，且报的是
        # 「环境变量未设置」这个根因，而不是调用时才出现的 401。
        return None, f"环境变量 {var} 未设置"
    return value, None


def load_models_config(path: Path = DEFAULT_MODELS_FILE) -> ModelsConfig:
    """加载并校验 models.yaml。

    Raises:
        ModelConfigError: 任一档位配置非法，或降级链引用了不存在的档。
    """
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    tiers: dict[str, TierConfig] = {}
    for tier, spec in (raw.get("tiers") or {}).items():
        api_key, unavailable = _resolve_api_key(tier, spec.get("api_key"))
        tiers[tier] = TierConfig(
            tier=tier,
            name=spec["name"],
            base_url=spec["base_url"],
            model=spec["model"],
            role=spec.get("role", "benchmark"),
            max_tokens_floor=int(spec.get("max_tokens_floor", 512)),
            extra_body=spec.get("extra_body") or {},
            api_key=api_key,
            unavailable_reason=unavailable,
        )

    chain = tuple(raw.get("fallback_chain") or ())
    unknown = [t for t in chain if t not in tiers]
    if unknown:
        raise ModelConfigError(f"fallback_chain 引用了不存在的档位：{unknown}")
    # dev 档进降级链会污染对比结论：一次静默回落就让报告里的数字不再可比。
    dev_in_chain = [t for t in chain if tiers[t].role == "dev"]
    if dev_in_chain:
        raise ModelConfigError(f"dev 档不得进入 fallback_chain：{dev_in_chain}")

    default_tier = raw.get("default_tier")
    if default_tier not in tiers:
        raise ModelConfigError(f"default_tier {default_tier!r} 不在档位表中")

    return ModelsConfig(default_tier=default_tier, tiers=tiers, fallback_chain=chain)


@lru_cache
def get_models_config() -> ModelsConfig:
    """进程内单例。改配置需重启。"""
    return load_models_config()
