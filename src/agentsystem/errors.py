"""统一错误模型（P1 详细设计 §5）。

存在的理由是 P4 的 RPA 降级判定：它必须能区分「业务拒绝」与「系统故障」。
若库存不足返回 5xx，降级逻辑会误触发 RPA 兜底 —— 拿机器人去重试一件
业务上本就不该成立的事。

``retryable`` 是降级判定的**唯一**依据。调用方不得靠 HTTP 状态码猜：
状态码是给 HTTP 语义用的，同一个 4xx 里既有该重试的也有不该重试的。
"""


class AppError(Exception):
    """全部业务异常的基类。

    Attributes:
        code: 稳定的机器可读串，不随文案变。跨版本承诺不变。
        message: 面向用户的中文说明。可以改，调用方不得依赖。
        retryable: 是否可重试；降级判定读这一个字段。
        http_status: 映射到 HTTP 层的状态码。
    """

    code: str = "SYS_UNKNOWN"
    retryable: bool = False
    http_status: int = 500

    def __init__(self, message: str, *, code: str | None = None) -> None:
        """构造异常。``code`` 留空则用类上的默认值。"""
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code

    def to_envelope(self, trace_id: str) -> dict[str, object]:
        """转成统一响应包络。字段顺序与 P1 详细设计 §5 一致。"""
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "trace_id": trace_id,
        }


class BusinessRejection(AppError):
    """业务规则拒绝，如库存不足、跨缸超容差、订单已锁定。

    不可重试 —— 重试一万次库存也不会变多。这正是 RPA 降级**不该**触发的场景。
    """

    code = "BIZ_REJECTED"
    retryable = False
    http_status = 409


class ValidationError(AppError):
    """入参不合法：槽位缺失、枚举非法。"""

    code = "VAL_INVALID"
    retryable = False
    http_status = 400


class AuthError(AppError):
    """身份或授权问题：越权、令牌无效。"""

    code = "AUTH_DENIED"
    retryable = False
    http_status = 403


class ConfirmationRequired(AppError):
    """写操作尚未二次确认（宪法第一条的负测试对象）。

    用 428 Precondition Required 而非 403：语义上不是"你不能做"，
    而是"你还缺一步前置条件"，前端据此弹确认框而非报错页。
    """

    code = "CONF_REQUIRED"
    retryable = False
    http_status = 428


class UpstreamError(AppError):
    """外部依赖故障：LLM 不可达、Dify 超时。可重试。"""

    code = "UP_UNAVAILABLE"
    retryable = True
    http_status = 502


class SystemError_(AppError):
    """本系统内部故障，如数据库连接失败。可重试。

    类名带下划线后缀以避开内建的 ``SystemError``；对外暴露的是 ``code``，
    不是类名，故不影响契约。
    """

    code = "SYS_INTERNAL"
    retryable = True
    http_status = 500
