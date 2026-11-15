"""FastAPI 入口：采样登记 / 仪器检测记录配对服务。"""

from __future__ import annotations

from collections import Counter
from datetime import datetime

from fastapi import Body, FastAPI
from fastapi.responses import RedirectResponse

from .examples import DEMO_EXAMPLES
from .matching import Analysis, Record, analyze
from .schemas import (
    CertainPairOut,
    MatchRequest,
    MatchResponse,
    PartnerOptionOut,
    RecordIn,
    RecordVerdictOut,
    StatusCounts,
    SummaryOut,
)

app = FastAPI(
    title="采样-检测记录配对服务",
    description=(
        "把人工采样登记与仪器检测记录按'同条码 + UTC 时间差 ≤ 10 分钟'配对，"
        "每条记录至多用一次；先最大化配对数，再最小化时间差总和。\n\n"
        "接口只把**所有同等最优方案中都成立**的配对列为确定配对（certain），"
        "其余记录区分为待核查（review）与始终未配对（unmatched），"
        "不会任选一套最优方案冒充确定结论。\n\n"
        "右侧 `/match` 的 Examples 下拉框内置三组演示请求："
        "重复条码、交叉候选、等价最优。"
    ),
    version="1.0.0",
)


def _to_ms(dt: datetime) -> int:
    """带时区的 datetime -> UTC epoch 毫秒。"""
    return round(dt.timestamp() * 1000)


def _verdict_out(rec_in: RecordIn, analysis: Analysis, is_sampling: bool) -> RecordVerdictOut:
    verdicts = analysis.sampling if is_sampling else analysis.detection
    v = next(item for item in verdicts if item.id == rec_in.id)
    return RecordVerdictOut(
        id=v.id,
        barcode=rec_in.barcode,
        timestamp=rec_in.timestamp,
        status=v.status,
        certain_partner_id=v.certain_partner_id,
        matched_in_all_optimal=v.matched_in_all_optimal,
        possible_partners=[
            PartnerOptionOut(id=o.id, time_diff_seconds=o.time_diff_ms / 1000)
            for o in v.possible_partners
        ],
    )


def _status_counts(verdicts: tuple) -> StatusCounts:
    counter = Counter(v.status for v in verdicts)
    return StatusCounts(
        certain=counter.get("certain", 0),
        review=counter.get("review", 0),
        unmatched=counter.get("unmatched", 0),
    )


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse(url="/docs")


@app.get("/health", include_in_schema=False)
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post(
    "/match",
    response_model=MatchResponse,
    summary="配对采样登记与仪器检测记录",
    description=(
        "接收两组各不超过 12 条记录，返回确定配对（所有同等最优方案都成立）、"
        "每条记录的状态（certain / review / unmatched）及可行配对对象。"
    ),
)
def match(
    request: MatchRequest = Body(openapi_examples=DEMO_EXAMPLES),
) -> MatchResponse:
    sampling = [Record(r.id, r.barcode, _to_ms(r.timestamp)) for r in request.sampling_records]
    detection = [Record(r.id, r.barcode, _to_ms(r.timestamp)) for r in request.detection_records]

    analysis = analyze(sampling, detection)

    return MatchResponse(
        summary=SummaryOut(
            sampling_count=len(sampling),
            detection_count=len(detection),
            candidate_pair_count=analysis.candidate_pair_count,
            max_pairs=analysis.max_pairs,
            min_total_time_diff_seconds=analysis.min_total_time_diff_ms / 1000,
            certain_pair_count=len(analysis.certain_pairs),
            sampling_status=_status_counts(analysis.sampling),
            detection_status=_status_counts(analysis.detection),
        ),
        certain_pairs=[
            CertainPairOut(
                sampling_id=p.sampling_id,
                detection_id=p.detection_id,
                barcode=p.barcode,
                time_diff_seconds=p.time_diff_ms / 1000,
            )
            for p in analysis.certain_pairs
        ],
        sampling_records=[
            _verdict_out(r, analysis, is_sampling=True) for r in request.sampling_records
        ],
        detection_records=[
            _verdict_out(r, analysis, is_sampling=False) for r in request.detection_records
        ],
    )
