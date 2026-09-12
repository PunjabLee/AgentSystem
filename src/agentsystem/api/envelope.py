"""统一响应包络与分页语义（P2.3.2 · P2.3.3）。

## 为什么分页不是可选项

**无分页会让 LLM 把截断结果当成完整结果 —— 表现为幻觉，成因却是接口设计。**
这是评审原话。而仅返回 ``has_more: true`` 不够：模型未必注意到一个布尔字段。
故 :func:`paginated` 在截断时额外附一条**自然语言** ``notice`` —— 它进入模型
上下文，比结构化布尔值有效得多。

## 空结果与业务拒绝不能混

| 情形 | 返回 | 为什么不能混 |
|---|---|---|
| 查询无匹配 | 200 + ``data: []`` + notice | 正常结果，不是错误 |
| 库存不足 | 409 + ``retryable: false`` | 业务拒绝，**不得触发 P4 的 RPA 降级** |
| 上游超时 | 504 + ``retryable: true`` | 这才该触发降级 |

混在一起的代价是具体的：P4 的降级判定读 ``retryable``，把「库存不足」当成
故障重试，会对着一个永远不会成功的请求反复重试。
"""

from typing import Any

from pydantic import BaseModel, Field

#: 默认与最大页长。上限存在的理由是防止「limit=99999 绕过分页」——
#: 那等于把截断问题原样搬回来。
DEFAULT_LIMIT = 20
MAX_LIMIT = 100


class Pagination(BaseModel):
    """分页元信息。"""

    limit: int
    offset: int
    total_count: int
    has_more: bool


class Envelope[T](BaseModel):
    """成功响应的统一外壳。

    ``notice`` 是给**模型**看的自然语言提示，不是给人看的 UI 文案 ——
    截断、空结果这两种情况下模型最容易误判，靠它显式说明。
    """

    data: T
    pagination: Pagination | None = None
    notice: str | None = None
    trace_id: str = Field(description="与 audit_log 同 trace_id，可串联全链路")


def paginated(
    rows: list[Any],
    *,
    total: int,
    limit: int,
    offset: int,
    trace_id: str,
    narrow_hint: str = "缩小查询范围",
) -> Envelope[list[Any]]:
    """把一页结果装进包络，并在必要时附上模型可读的提示。

    Args:
        rows: 本页数据。
        total: 匹配的总条数（不是本页条数）。
        limit: 本次页长。
        offset: 本次偏移。
        trace_id: 全链路标识。
        narrow_hint: 截断时给模型的收窄建议，各端点按自己的过滤维度填。

    Returns:
        含 ``notice`` 的包络 —— 截断与空结果两种情况下 ``notice`` 非空。
    """
    has_more = offset + len(rows) < total
    notice: str | None = None
    if has_more:
        # 🔴 措辞要让模型知道「这不是全部」，并给出可执行的收窄方向。
        #    只说「还有更多」而不说怎么办，模型往往就直接把这一页当全部用了。
        notice = (
            f"共 {total} 条，此处仅返回第 {offset + 1}–{offset + len(rows)} 条。"
            f"如需完整结果请{narrow_hint}，或说明需要查看更多。"
        )
    elif not rows:
        # 空结果单独给提示：它是正常结果而非错误，但模型容易把「查不到」
        # 说成「系统出错了」，反过来也会把「无权限」说成「没有数据」。
        notice = "未找到符合条件的记录。"
    return Envelope(
        data=rows,
        pagination=Pagination(limit=limit, offset=offset, total_count=total, has_more=has_more),
        notice=notice,
        trace_id=trace_id,
    )


def clamp_limit(limit: int | None) -> int:
    """把页长收进合法区间。

    超上限时**静默收窄而非报错** —— 模型传了 500 是能力问题不是错误，
    报错只会让它重试一遍同样的请求。收窄后由 ``notice`` 告诉它结果被截断了。
    """
    if limit is None:
        return DEFAULT_LIMIT
    return max(1, min(limit, MAX_LIMIT))
