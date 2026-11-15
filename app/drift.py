"""连续批次质控偏差的报警审计核心算法。

语义（严格按题面）：

* 第 i 批的真实偏差 d_i 是闭区间 [low_i, high_i] 内的**某个整数**，
  而不是区间中点；审计必须覆盖区间内所有可能读数。
* 每批正向累计 ``pos = max(0, pos + d - k)``，
  负向累计 ``neg = max(0, neg - d - k)``。
* 任一累计值达到 h（``>= h``）即在该批报警；报警后**两侧**同时清零，
  下一批从 ``(0, 0)`` 开始。
* 对每一批，在“此前所有批次的可能读数 + 本批区间内所有读数”上量化：
    - 所有可能历史都在本批报警  -> will_alarm（必报警）
    - 所有可能历史都不在本批报警 -> no_alarm（不报警）
    - 其余                      -> may_alarm（可能报警）

关键点：不能对每批独立地从零推算，也不能把正向/负向累计当成互不相关的
两个数分别取极值。前一批读数会同时决定 (pos, neg)，报警又会让两者一起
清零；因此本实现逐批维护**可达状态集合** ``{(pos, neg), ...}``，
所有后续判断都建立在这个集合上，从而保留读数与重置造成的状态关联。
"""

from __future__ import annotations

from dataclasses import dataclass, field

STATUS_WILL_ALARM = "will_alarm"  # 必报警：所有可能读数/历史都在本批报警
STATUS_MAY_ALARM = "may_alarm"    # 可能报警：部分可能读数/历史在本批报警
STATUS_NO_ALARM = "no_alarm"      # 不报警：所有可能读数/历史都不在本批报警


@dataclass(frozen=True)
class DriftBatch:
    """一批质控偏差的整数闭区间 [low, high]。"""

    low: int
    high: int


@dataclass(frozen=True)
class BatchAudit:
    """单批审计结论。"""

    index: int
    low: int
    high: int
    status: str
    alarm_certain: bool
    alarm_possible: bool
    possible_alarm_deviations: tuple[int, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class DriftAudit:
    """整段连续批次的审计结论。"""

    k: int
    h: int
    batches: tuple[BatchAudit, ...]


def audit_drift(batches: list[DriftBatch], k: int, h: int) -> DriftAudit:
    """审计连续批次的报警确定性。

    ``states`` 是下一批开始时所有可能的 (正向累计, 负向累计) 状态。初始为
    ``{(0, 0)}``；每批把每个可达状态与本批区间内的每个整数偏差组合，
    报警分支写入 ``(0, 0)``，未报警分支写入新的累计值。
    """

    if k < 0:
        raise ValueError("k 必须为非负整数")
    if h < 1:
        raise ValueError("h 必须为正整数")

    # 未报警状态下 pos/neg 都 < h，故状态集合至多 h*h 个。
    states: set[tuple[int, int]] = {(0, 0)}
    audits: list[BatchAudit] = []

    for index, batch in enumerate(batches):
        if batch.low > batch.high:
            raise ValueError("每批偏差区间必须满足 low <= high")

        next_states: set[tuple[int, int]] = set()
        alarm_deviations: set[int] = set()
        alarm_transitions = 0
        history_count = len(states)
        width = batch.high - batch.low + 1

        for pos, neg in states:
            # 对固定状态 (pos, neg)，报警条件可解成关于 d 的两端闭区间：
            #   正向 pos + d - k >= h  <=>  d >= h - pos + k
            #   负向 neg - d - k >= h  <=>  d <= neg - k - h
            # pos/neg 均 < h 时两段不会重叠（正向段在右端、负向段在左端）。
            low_alarm_hi = min(batch.high, neg - k - h)   # 负向报警：低端
            high_alarm_lo = max(batch.low, h - pos + k)   # 正向报警：高端
            for d in range(batch.low, low_alarm_hi + 1):
                alarm_transitions += 1
                alarm_deviations.add(d)
            for d in range(high_alarm_lo, batch.high + 1):
                alarm_transitions += 1
                alarm_deviations.add(d)

            # 未报警的 d 必然落在中间区间；逐个推进可达状态。
            safe_lo = max(batch.low, neg - k - h + 1)
            safe_hi = min(batch.high, h - pos + k - 1)
            for d in range(safe_lo, safe_hi + 1):
                next_states.add((max(0, pos + d - k), max(0, neg - d - k)))

        if alarm_transitions:
            # 任一报警分支都会使两侧清零，下一批从 (0, 0) 开始。
            next_states.add((0, 0))

        total_transitions = history_count * width
        alarm_certain = alarm_transitions == total_transitions
        alarm_possible = alarm_transitions > 0

        if alarm_certain:
            status = STATUS_WILL_ALARM
        elif alarm_possible:
            status = STATUS_MAY_ALARM
        else:
            status = STATUS_NO_ALARM

        audits.append(
            BatchAudit(
                index=index,
                low=batch.low,
                high=batch.high,
                status=status,
                alarm_certain=alarm_certain,
                alarm_possible=alarm_possible,
                possible_alarm_deviations=tuple(sorted(alarm_deviations)),
            )
        )
        states = next_states

    return DriftAudit(k=k, h=h, batches=tuple(audits))
