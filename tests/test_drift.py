"""漂移审计核心算法的单元测试（含与暴力枚举的交叉验证）。"""

from __future__ import annotations

import itertools
import random

from app.drift import (
    STATUS_MAY_ALARM,
    STATUS_NO_ALARM,
    STATUS_WILL_ALARM,
    DriftBatch,
    audit_drift,
)


def _brute_force(batches, k, h):
    """枚举区间内所有整数读数组合，直接模拟报警规则。

    返回逐批的 (alarm_certain, alarm_possible, 可触发报警的偏差集合)。
    与状态集合 DP 完全独立，用于交叉验证。
    """
    ranges = [range(b.low, b.high + 1) for b in batches]
    readings = list(itertools.product(*ranges))
    alarms = [[False] * len(batches) for _ in readings]
    for row, combo in enumerate(readings):
        pos = neg = 0
        for i, d in enumerate(combo):
            pos = max(0, pos + d - k)
            neg = max(0, neg - d - k)
            if pos >= h or neg >= h:
                alarms[row][i] = True
                pos = neg = 0  # 报警后两侧清零
    out = []
    for i in range(len(batches)):
        column = [alarms[row][i] for row in range(len(readings))]
        if all(column):
            certain, possible = True, True
        elif any(column):
            certain, possible = False, True
        else:
            certain, possible = False, False
        deviations = {
            combo[i]
            for row, combo in enumerate(readings)
            if alarms[row][i]
        }
        out.append((certain, possible, tuple(sorted(deviations))))
    return out


def test_positive_drift_accumulates_and_alarms():
    # k=0,h=5：偏差 2,2,2 -> 2,4,6 >=5 于第 2 批报警
    result = audit_drift(
        [DriftBatch(2, 2), DriftBatch(2, 2), DriftBatch(2, 2)], k=0, h=5
    )
    assert [b.status for b in result.batches] == [
        STATUS_NO_ALARM,
        STATUS_NO_ALARM,
        STATUS_WILL_ALARM,
    ]
    assert result.batches[2].alarm_certain is True
    assert result.batches[2].possible_alarm_deviations == (2,)


def test_negative_drift_accumulates_and_alarms():
    # 负向累计：neg = max(0, neg - d - k)
    result = audit_drift(
        [DriftBatch(-2, -2), DriftBatch(-2, -2), DriftBatch(-2, -2)], k=0, h=5
    )
    assert [b.status for b in result.batches] == [
        STATUS_NO_ALARM,
        STATUS_NO_ALARM,
        STATUS_WILL_ALARM,
    ]


def test_tolerance_k_is_subtracted_each_batch():
    # k=2：每批偏差 2 恰好被允差吃掉，累计始终 0 => 永不报警
    result = audit_drift([DriftBatch(2, 2)] * 5, k=2, h=1)
    assert all(b.status == STATUS_NO_ALARM for b in result.batches)


def test_threshold_reached_at_exactly_h_alarms():
    # 达到 h（含等于）即报警
    result = audit_drift([DriftBatch(5, 5)], k=0, h=5)
    assert result.batches[0].status == STATUS_WILL_ALARM
    result = audit_drift([DriftBatch(4, 4)], k=0, h=5)
    assert result.batches[0].status == STATUS_NO_ALARM


def test_alarm_resets_both_sides():
    # k=0,h=3：第 0 批偏差 3 必报警清零；第 1 批偏差 -2 只产生 neg=2，不报警；
    # 第 2 批偏差 -1 使 neg=3 报警。
    result = audit_drift(
        [DriftBatch(3, 3), DriftBatch(-2, -2), DriftBatch(-1, -1)], k=0, h=3
    )
    assert [b.status for b in result.batches] == [
        STATUS_WILL_ALARM,
        STATUS_NO_ALARM,
        STATUS_WILL_ALARM,
    ]


def test_reset_prevents_carry_over_across_alarm():
    # k=0,h=4：第 0 批偏差 4 必报警清零。
    # 若不清零，第 1 批偏差 1 会累到 5 当即报警；清零后只有 1，不报警。
    # 第 2 批偏差 3 使 1+3=4，报警。
    result = audit_drift(
        [DriftBatch(4, 4), DriftBatch(1, 1), DriftBatch(3, 3)], k=0, h=4
    )
    assert [b.status for b in result.batches] == [
        STATUS_WILL_ALARM,
        STATUS_NO_ALARM,
        STATUS_WILL_ALARM,
    ]


def test_partial_interval_gives_may_alarm():
    # k=0,h=5：第 0 批 [0,5] 只有 5 报警 => 可能报警
    # 第 1 批若第 0 批报警则从零开始；若未报警则 pos 为 0..4。
    # 第 1 批偏差 0：历史 pos=4 时仍不报警 -> 非必报警；历史 pos=5 时已报警清零。
    result = audit_drift([DriftBatch(0, 5), DriftBatch(0, 0)], k=0, h=5)
    first, second = result.batches
    assert first.status == STATUS_MAY_ALARM
    assert first.alarm_possible is True and first.alarm_certain is False
    assert first.possible_alarm_deviations == (5,)
    assert second.status == STATUS_NO_ALARM


def test_may_alarm_due_to_history_not_only_reading():
    # 第 0 批偏差 4（确定不报警，pos=4）；第 1 批区间 [1,1] 必然 4+1>=5 报警
    result = audit_drift([DriftBatch(4, 4), DriftBatch(1, 1)], k=0, h=5)
    assert result.batches[0].status == STATUS_NO_ALARM
    assert result.batches[1].status == STATUS_WILL_ALARM
    assert result.batches[1].possible_alarm_deviations == (1,)


def test_wide_interval_both_sides_are_independent():
    # k=0,h=5，第 0 批 [-5,5]：只有 +5 触正警、-5 触负警，其余不报警。
    result = audit_drift([DriftBatch(-5, 5)], k=0, h=5)
    batch = result.batches[0]
    assert batch.status == STATUS_MAY_ALARM
    assert batch.possible_alarm_deviations == (-5, 5)


def test_empty_batches_valid():
    result = audit_drift([], k=0, h=1)
    assert result.batches == ()


def test_rejects_inverted_interval():
    import pytest

    with pytest.raises(ValueError):
        audit_drift([DriftBatch(2, 1)], k=0, h=5)


def test_random_cases_agree_with_brute_force():
    rng = random.Random(20261115)
    checked = 0
    for _ in range(400):
        n = rng.randint(0, 5)
        batches = []
        for _ in range(n):
            low = rng.randint(-5, 5)
            high = rng.randint(low, min(5, low + 3))
            batches.append(DriftBatch(low, high))
        k = rng.randint(0, 4)
        h = rng.randint(1, 8)
        result = audit_drift(batches, k=k, h=h)
        expected = _brute_force(batches, k, h)
        actual = [
            (b.alarm_certain, b.alarm_possible, b.possible_alarm_deviations)
            for b in result.batches
        ]
        assert actual == expected, (batches, k, h, actual, expected)
        checked += 1
    assert checked == 400


def test_random_cases_status_strings_match_flags():
    rng = random.Random(7)
    for _ in range(200):
        n = rng.randint(1, 6)
        batches = []
        for _ in range(n):
            low = rng.randint(-6, 6)
            high = rng.randint(low, 6)
            batches.append(DriftBatch(low, high))
        result = audit_drift(batches, k=rng.randint(0, 5), h=rng.randint(1, 10))
        for b in result.batches:
            if b.alarm_certain:
                assert b.status == STATUS_WILL_ALARM
            elif b.alarm_possible:
                assert b.status == STATUS_MAY_ALARM
            else:
                assert b.status == STATUS_NO_ALARM
            assert bool(b.possible_alarm_deviations) is b.alarm_possible
