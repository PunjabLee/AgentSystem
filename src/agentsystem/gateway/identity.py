"""静态 Bearer 身份解析（P1 详细设计 §2.5.1）。

PoC 只需要「身份是服务端权威的」这一性质，不需要完整认证体系 ——
不做登录流程、不签发 JWT。配置里只存 SHA-256，明文令牌走 .env
（宪法第七条）。

本模块刻意不依赖任何 Web 框架：FastAPI 骨架是 P2.3.1 的事，
身份契约得先立住，否则 P1.3 的审计写入无处取 user_id。
"""

import hashlib
import hmac
import uuid
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from agentsystem.errors import AuthError
from agentsystem.gateway.context import RequestContext
from agentsystem.settings import get_settings

DEFAULT_USERS_FILE = Path("config/users.yaml")


@dataclass(frozen=True, slots=True)
class UserRecord:
    """users.yaml 中的一条用户定义。"""

    user_id: str
    name: str
    token_sha256: str
    bu_codes: frozenset[str]
    regions: frozenset[str]


def _sha256_hex(raw: str) -> str:
    """取 UTF-8 字节的 SHA-256 十六进制串。"""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class IdentityRegistry:
    """令牌到用户的只读映射。"""

    def __init__(self, users: list[UserRecord]) -> None:
        """校验并保存用户表。

        Raises:
            ValueError: user_id 或 token_sha256 重复 —— 重复的哈希意味着
                两个用户共用一枚令牌，审计里的「操作者」将不可判定。
        """
        ids = [u.user_id for u in users]
        if len(set(ids)) != len(ids):
            raise ValueError("users.yaml 中 user_id 重复")
        hashes = [u.token_sha256 for u in users]
        if len(set(hashes)) != len(hashes):
            raise ValueError("users.yaml 中 token_sha256 重复：两个用户共用令牌")
        self._users = tuple(users)

    @classmethod
    def from_yaml(cls, path: Path = DEFAULT_USERS_FILE) -> IdentityRegistry:
        """从 YAML 载入。"""
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return cls(
            [
                UserRecord(
                    user_id=u["user_id"],
                    name=u["name"],
                    token_sha256=u["token_sha256"],
                    bu_codes=frozenset(u["bu_codes"]),
                    regions=frozenset(u["regions"]),
                )
                for u in raw.get("users", [])
            ]
        )

    def resolve(self, bearer_token: str) -> UserRecord:
        """按明文令牌查用户。

        逐条 ``compare_digest`` 而非字典查表：用户数是个位数，代价可忽略，
        换来的是不必论证「以摘要为键的哈希探测是否泄漏时序」。

        Raises:
            AuthError: 令牌无法匹配任何用户。
        """
        presented = _sha256_hex(bearer_token)
        for user in self._users:
            if hmac.compare_digest(presented, user.token_sha256):
                return user
        raise AuthError("Bearer 令牌无效", code="AUTH_TOKEN_INVALID")


@lru_cache
def get_identity_registry() -> IdentityRegistry:
    """进程内单例。改配置需重启 —— PoC 阶段刻意不做热加载。"""
    return IdentityRegistry.from_yaml()


def derive_session_id(user_id: str, conversation_id: str) -> str:
    """由服务端密钥派生 session_id。

    §2.5.3 要求 session_id 由服务端生成、不接受客户端指定，理由是
    「客户端可指定即可冒充他人会话完成 write_intent 确认」。

    但确认流程横跨多个请求，session_id 必须稳定，纯随机生成就需要一张
    会话表。这里改用 HMAC 派生：客户端提供 conversation_id，服务端把
    **已认证的 user_id** 一并压进 HMAC。于是

      · 客户端算不出任何 session_id（没有密钥）；
      · 即便照抄他人的 conversation_id，派生出的 session_id 也因 user_id
        不同而不同，落不到他人的 write_intent 上。

    冒充所需的性质由此得到，且不引入会话存储。
    """
    key = get_settings().session_signing_key.encode("utf-8")
    msg = f"{user_id}:{conversation_id}".encode()
    return hmac.new(key, msg, hashlib.sha256).hexdigest()[:32]


def authenticate(
    bearer_token: str,
    *,
    conversation_id: str,
    client_trace_id: str | None = None,
) -> RequestContext:
    """把一次入站请求解析成 RequestContext。

    Args:
        bearer_token: Authorization 头里的明文令牌。
        conversation_id: 客户端提供的会话标识；仅作 HMAC 输入，
            不直接作为 session_id。
        client_trace_id: 入站 X-Trace-Id。**降级留存作参考，
            永不作为权威 trace_id** —— 客户端可指定即可让两次操作共用
            一个 id，污染 attempt/outcome 的串联。

    Returns:
        全链路只读的身份上下文。
    """
    user = get_identity_registry().resolve(bearer_token)
    return RequestContext(
        user_id=user.user_id,
        session_id=derive_session_id(user.user_id, conversation_id),
        # UUIDv7 而非 v4：自带时间前缀，审计表按 trace_id 排序即近似时序，
        # 省一个索引。Python 3.14 起 uuid7 在标准库内。
        trace_id=str(uuid.uuid7()),
        bu_codes=user.bu_codes,
        regions=user.regions,
        client_trace_id=client_trace_id,
    )
