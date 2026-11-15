"""连续批次质控偏差报警审计的请求/响应模型。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_BATCHES = 24
MAX_ENDPOINT_ABS = 20
MAX_TOLERANCE = 20
MAX_ALERT_THRESHOLD = 40


class DriftBatchIn(BaseModel):
    """一批质控偏差的整数闭区间。

    推荐对象形式 ``{"low": a, "high": b}``；同时接受二元数组 ``[a, b]``，
    以及 ``min``/``max``、``start``/``end``、``lower``/``upper`` 端点别名，
    便于不同客户端直接调用。可选批次编号字段 ``index``（别名 ``batch``、
    ``batch_index``）用于显式声明连续编号。
    """

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def _normalize_shape(cls, data: object) -> object:
        if isinstance(data, (list, tuple)):
            if len(data) != 2:
                raise ValueError("每批区间必须是 [下界, 上界] 二元数组")
            return {"low": data[0], "high": data[1]}
        if isinstance(data, dict):
            data = dict(data)
            for alias in ("batch", "batch_index"):
                if alias in data:
                    if "index" in data:
                        raise ValueError("批次编号字段重复")
                    data["index"] = data.pop(alias)
            if "low" not in data and "high" not in data:
                for lo_key, hi_key in (
                    ("min", "max"),
                    ("start", "end"),
                    ("lower", "upper"),
                ):
                    if lo_key in data and hi_key in data:
                        data["low"] = data.pop(lo_key)
                        data["high"] = data.pop(hi_key)
                        break
        return data

    index: int | None = Field(
        default=None,
        strict=True,
        ge=0,
        le=MAX_BATCHES - 1,
        description="可选批次编号；若提供，必须等于该区间在数组中的位置（从 0 开始）",
    )
    low: int = Field(
        strict=True,
        ge=-MAX_ENDPOINT_ABS,
        le=MAX_ENDPOINT_ABS,
        description="本批偏差区间下界（整数，含端点，绝对值 ≤ 20）",
        examples=[-4],
    )
    high: int = Field(
        strict=True,
        ge=-MAX_ENDPOINT_ABS,
        le=MAX_ENDPOINT_ABS,
        description="本批偏差区间上界（整数，含端点，绝对值 ≤ 20）",
        examples=[1],
    )


class DriftAuditRequest(BaseModel):
    """同一仪器从 0 开始连续编号的 0–24 批偏差区间，以及 k、h。"""

    model_config = ConfigDict(extra="forbid")

    batches: list[DriftBatchIn] = Field(
        max_length=MAX_BATCHES,
        description=(
            "按批次编号 0,1,2,… 顺序排列的整数偏差闭区间，"
            "最多 24 批；空列表返回空审计结果"
        ),
    )
    k: int = Field(
        strict=True,
        ge=0,
        le=MAX_TOLERANCE,
        description="整数允差 k（0–20），每批先从偏差中扣除",
        examples=[2],
    )
    h: int = Field(
        strict=True,
        ge=1,
        le=MAX_ALERT_THRESHOLD,
        description="警戒值 h（1–40），任一侧累计达到即报警",
        examples=[5],
    )

    @model_validator(mode="after")
    def _intervals_and_numbering_consistent(self) -> "DriftAuditRequest":
        for i, batch in enumerate(self.batches):
            if batch.low > batch.high:
                raise ValueError(f"batches[{i}] 的下界不能大于上界")
            if batch.index is not None and batch.index != i:
                raise ValueError(
                    f"batches[{i}] 声明的 index={batch.index} 与数组位置不一致；"
                    "批次必须从 0 起连续编号"
                )
        return self


class BatchAuditOut(BaseModel):
    """单批审计结论。"""

    index: int = Field(description="批次编号，从 0 开始连续编号")
    low: int
    high: int
    status: Literal["will_alarm", "may_alarm", "no_alarm"] = Field(
        description=(
            "will_alarm=必报警（区间内所有读数、所有可能历史都在本批报警）；"
            "may_alarm=可能报警（部分读数或历史在本批报警）；"
            "no_alarm=不报警（所有可能读数与历史都不在本批报警）"
        )
    )
    alarm_certain: bool = Field(description="是否所有可能情形都在本批报警")
    alarm_possible: bool = Field(description="是否存在任一情形在本批报警")
    possible_alarm_deviations: list[int] = Field(
        description=(
            "存在某个可能历史，使该整数偏差在本批触发报警的读数集合；"
            "为空的充要条件是 alarm_possible=false"
        )
    )


class DriftAuditSummaryOut(BaseModel):
    batch_count: int
    k: int
    h: int
    will_alarm_count: int
    may_alarm_count: int
    no_alarm_count: int
    first_certain_alarm_index: int | None = Field(
        description="首个必报警批次的编号；不存在则为 null"
    )


class DriftAuditResponse(BaseModel):
    summary: DriftAuditSummaryOut
    batches: list[BatchAuditOut]
