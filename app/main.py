"""FastAPI 入口：采样配对服务 + 连续批次质控偏差报警审计。"""

from __future__ import annotations

from collections import Counter
from datetime import datetime

from fastapi import Body, FastAPI
from fastapi.responses import RedirectResponse

from .drift import DriftBatch, audit_drift
from .drift_examples import DRIFT_DEMO_EXAMPLES
from .drift_schemas import (
    BatchAuditOut,
    DriftAuditRequest,
    DriftAuditResponse,
    DriftAuditSummaryOut,
)
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
    title="采样配对与质控漂移审计服务",
    description=(
        "## 1. 采样-检测记录配对 `/match`\n\n"
        "把人工采样登记与仪器检测记录按'同条码 + UTC 时间差 ≤ 10 分钟'配对，"
        "每条记录至多用一次；先最大化配对数，再最小化时间差总和。\n\n"
        "接口只把**所有同等最优方案中都成立**的配对列为确定配对（certain），"
        "其余记录区分为待核查（review）与始终未配对（unmatched），"
        "不会任选一套最优方案冒充确定结论。\n\n"
        "`/match` 的 Examples 下拉框内置三组演示请求："
        "重复条码、交叉候选、等价最优。\n\n"
        "## 2. 连续批次漂移审计 `/drift-audit`\n\n"
        "仪器只给出每批读数的**误差闭区间**，绝不取中点代替实际读数。"
        "对区间内所有可能整数读数逐批判定：必报警（will_alarm）、"
        "可能报警（may_alarm）或不报警（no_alarm）。"
        "任一侧累计达到 h 的批次会触发报警并使两侧清零，后续批次因此"
        "保留此前读数与重置的状态关联，而非孤立地逐批重算。\n\n"
        "`/drift-audit` 的 Examples 下拉框内置三组演示请求："
        "正向缓漂、报警后清零、双向不确定。"
    ),
    version="1.1.0",
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


@app.post(
    "/drift-audit",
    response_model=DriftAuditResponse,
    summary="逐批判定连续批次质控偏差的报警确定性",
    description=(
        "接收同一仪器从 0 连续编号的最多 24 批整数偏差闭区间（端点绝对值 ≤ 20）、"
        "整数允差 k（0–20）与警戒值 h（1–40）。每批：\n\n"
        "- 正向累计 `pos = max(0, pos + d - k)`；\n"
        "- 负向累计 `neg = max(0, neg - d - k)`；\n"
        "- 任一侧达到 h（>= h）即在该批报警，且两侧清零、下一批从零开始。\n\n"
        "对每批区间内所有可能整数读数逐批返回 will_alarm / may_alarm / no_alarm。"
        "后续批次基于此前所有可能读数产生的可达累计状态集合判定，"
        "保留累计与报警重置造成的状态关联；不使用区间中点。"
    ),
)
def drift_audit(
    request: DriftAuditRequest = Body(openapi_examples=DRIFT_DEMO_EXAMPLES),
) -> DriftAuditResponse:
    result = audit_drift(
        [DriftBatch(low=b.low, high=b.high) for b in request.batches],
        k=request.k,
        h=request.h,
    )

    counts = Counter(batch.status for batch in result.batches)
    first_certain = next(
        (batch.index for batch in result.batches if batch.alarm_certain), None
    )

    return DriftAuditResponse(
        summary=DriftAuditSummaryOut(
            batch_count=len(result.batches),
            k=result.k,
            h=result.h,
            will_alarm_count=counts.get("will_alarm", 0),
            may_alarm_count=counts.get("may_alarm", 0),
            no_alarm_count=counts.get("no_alarm", 0),
            first_certain_alarm_index=first_certain,
        ),
        batches=[
            BatchAuditOut(
                index=batch.index,
                low=batch.low,
                high=batch.high,
                status=batch.status,
                alarm_certain=batch.alarm_certain,
                alarm_possible=batch.alarm_possible,
                possible_alarm_deviations=list(batch.possible_alarm_deviations),
            )
            for batch in result.batches
        ],
    )
