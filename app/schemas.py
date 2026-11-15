"""API 请求/响应模型。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_RECORDS_PER_SIDE = 12


class RecordIn(BaseModel):
    """单条采样登记或仪器检测记录。"""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(
        min_length=1,
        max_length=64,
        description="记录唯一 ID（同一组内唯一）",
        examples=["S-01"],
    )
    barcode: str = Field(
        min_length=1,
        max_length=128,
        description="容器条码，组内允许重复",
        examples=["BC-100"],
    )
    timestamp: datetime = Field(
        description="UTC 时间，ISO 8601 格式，必须携带时区（推荐 Z 后缀）",
        examples=["2026-11-14T08:00:00Z"],
    )

    @field_validator("timestamp")
    @classmethod
    def _require_timezone(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.utcoffset() is None:
            raise ValueError("timestamp 必须携带时区信息（UTC 时间，建议以 Z 结尾）")
        return v.astimezone(timezone.utc)


class MatchRequest(BaseModel):
    """配对请求：两组记录，每组最多 12 条。"""

    model_config = ConfigDict(extra="forbid")

    sampling_records: list[RecordIn] = Field(
        default_factory=list,
        max_length=MAX_RECORDS_PER_SIDE,
        description="人工采样登记，最多 12 条",
    )
    detection_records: list[RecordIn] = Field(
        default_factory=list,
        max_length=MAX_RECORDS_PER_SIDE,
        description="仪器检测记录，最多 12 条",
    )

    @model_validator(mode="after")
    def _ids_unique_within_group(self) -> "MatchRequest":
        for name, group in (
            ("sampling_records", self.sampling_records),
            ("detection_records", self.detection_records),
        ):
            ids = [r.id for r in group]
            if len(ids) != len(set(ids)):
                raise ValueError(f"{name} 内存在重复 ID")
        return self


class PartnerOptionOut(BaseModel):
    id: str = Field(description="对方记录 ID")
    time_diff_seconds: float = Field(description="与该对象的时间差（秒）")


class CertainPairOut(BaseModel):
    sampling_id: str
    detection_id: str
    barcode: str
    time_diff_seconds: float


class RecordVerdictOut(BaseModel):
    """单条记录的配对结论。"""

    id: str
    barcode: str
    timestamp: datetime
    status: Literal["certain", "review", "unmatched"] = Field(
        description=(
            "certain=确定配对（所有同等最优方案中都成立）；"
            "review=待核查（配对关系在不同最优方案间不唯一）；"
            "unmatched=始终未配对（任何同等最优方案都不包含它）"
        )
    )
    certain_partner_id: str | None = Field(
        default=None, description="status=certain 时的确定配对对象 ID"
    )
    matched_in_all_optimal: bool = Field(
        description=(
            "是否在所有同等最优方案中都被配对。review 记录借此区分"
            "'始终被配对但对象不定'(true) 与 '部分方案中未配对'(false)"
        )
    )
    possible_partners: list[PartnerOptionOut] = Field(
        default_factory=list,
        description="在至少一个同等最优方案中可行的配对对象",
    )


class StatusCounts(BaseModel):
    certain: int = Field(description="确定配对记录数")
    review: int = Field(description="待核查记录数")
    unmatched: int = Field(description="始终未配对记录数")


class SummaryOut(BaseModel):
    sampling_count: int
    detection_count: int
    candidate_pair_count: int = Field(
        description="满足'同条码且时间差不超过 10 分钟'的候选配对数"
    )
    max_pairs: int = Field(description="最优方案的最大配对数")
    min_total_time_diff_seconds: float = Field(
        description="最优方案的最小时间差总和（秒）"
    )
    certain_pair_count: int = Field(description="所有同等最优方案中都成立的确定配对数")
    sampling_status: StatusCounts
    detection_status: StatusCounts


class MatchResponse(BaseModel):
    summary: SummaryOut
    certain_pairs: list[CertainPairOut] = Field(
        description="确定配对列表：在所有同等最优方案中都成立，可直接进入分析库"
    )
    sampling_records: list[RecordVerdictOut]
    detection_records: list[RecordVerdictOut]
