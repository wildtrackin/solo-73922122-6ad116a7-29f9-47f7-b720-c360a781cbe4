"""配对核心算法的单元测试。"""

from app.matching import (
    MAX_TIME_DIFF_MS,
    Record,
    analyze,
)


def rec(rid: str, barcode: str, minute: float) -> Record:
    """用分钟数构造记录，便于阅读。"""
    return Record(id=rid, barcode=barcode, ts_ms=round(minute * 60_000))


def verdicts_by_id(verdicts):
    return {v.id: v for v in verdicts}


def test_unique_optimum_all_certain_with_duplicate_barcodes():
    """重复条码 + 时间窗可唯一区分 => 全部确定配对。"""
    sampling = [
        rec("S1", "BC-100", 0),
        rec("S2", "BC-100", 30),
        rec("S3", "BC-200", 60),
    ]
    detection = [
        rec("D1", "BC-100", 4),
        rec("D2", "BC-100", 26),
        rec("D3", "BC-200", 66),
    ]
    result = analyze(sampling, detection)
    assert result.max_pairs == 3
    assert result.min_total_time_diff_ms == (4 + 4 + 6) * 60_000
    assert {(p.sampling_id, p.detection_id) for p in result.certain_pairs} == {
        ("S1", "D1"),
        ("S2", "D2"),
        ("S3", "D3"),
    }
    assert all(v.status == "certain" for v in result.sampling)
    assert all(v.status == "certain" for v in result.detection)


def test_crossing_candidates_global_optimum_beats_greedy():
    """交叉候选：最近邻贪心（60s）会导致更差总成本，最优解不含它。"""
    sampling = [rec("S1", "BC-300", 0), rec("S2", "BC-300", 4)]
    detection = [
        rec("D1", "BC-300", 3),
        rec("D2", "BC-300", 8),
        rec("D3", "BC-300", 30),  # 超出所有时间窗
    ]
    result = analyze(sampling, detection)
    # 全局最优：S1-D1(180s) + S2-D2(240s) = 420s，而非贪心的 60s+480s=540s
    assert result.max_pairs == 2
    assert result.min_total_time_diff_ms == 420_000
    assert {(p.sampling_id, p.detection_id) for p in result.certain_pairs} == {
        ("S1", "D1"),
        ("S2", "D2"),
    }
    det = verdicts_by_id(result.detection)
    assert det["D3"].status == "unmatched"
    assert det["D3"].possible_partners == ()


def test_equivalent_optima_yield_no_certain_pairs():
    """等价最优：两套成本相同的最优方案 => 无确定配对，全部待核查。"""
    sampling = [rec("S1", "BC-400", 0), rec("S2", "BC-400", 10)]
    detection = [rec("D1", "BC-400", 5), rec("D2", "BC-400", 5)]
    result = analyze(sampling, detection)
    assert result.max_pairs == 2
    assert result.min_total_time_diff_ms == 600_000
    assert result.certain_pairs == ()
    for v in (*result.sampling, *result.detection):
        assert v.status == "review"
        # 四条记录在所有最优方案中都被配对，只是对象不定
        assert v.matched_in_all_optimal is True
        assert len(v.possible_partners) == 2


def test_sometimes_matched_record_is_review_but_not_in_all():
    """某记录只在部分最优方案中被配对 => review 且 matched_in_all=False。"""
    sampling = [rec("S1", "BC-Q", 0)]
    detection = [rec("D1", "BC-Q", 5), rec("D2", "BC-Q", 5)]
    result = analyze(sampling, detection)
    assert result.max_pairs == 1
    assert result.certain_pairs == ()
    sam = verdicts_by_id(result.sampling)
    det = verdicts_by_id(result.detection)
    # S1 在所有最优方案中都被配对（对象不定）
    assert sam["S1"].status == "review"
    assert sam["S1"].matched_in_all_optimal is True
    # D1/D2 各自只出现在部分最优方案中
    assert det["D1"].status == "review" and det["D1"].matched_in_all_optimal is False
    assert det["D2"].status == "review" and det["D2"].matched_in_all_optimal is False


def test_candidate_edge_but_never_optimal_means_unmatched():
    """有候选边但任何最优方案都不用 => 始终未配对。"""
    sampling = [rec("S1", "BC-X", 0), rec("S2", "BC-X", 9)]
    detection = [rec("D1", "BC-X", 5)]
    result = analyze(sampling, detection)
    # 候选边 S1-D1(300s)、S2-D1(240s)，最优唯一：S2-D1
    assert result.max_pairs == 1
    assert {(p.sampling_id, p.detection_id) for p in result.certain_pairs} == {("S2", "D1")}
    sam = verdicts_by_id(result.sampling)
    assert sam["S1"].status == "unmatched"
    assert sam["S1"].possible_partners == ()


def test_ten_minute_boundary_is_inclusive():
    """时间差恰好 10 分钟可配对，超过 1 毫秒则不可。"""
    ok = analyze([rec("S1", "B", 0)], [rec("D1", "B", 10)])
    assert ok.max_pairs == 1
    assert ok.certain_pairs[0].time_diff_ms == MAX_TIME_DIFF_MS

    over = analyze(
        [Record("S1", "B", 0)], [Record("D1", "B", MAX_TIME_DIFF_MS + 1)]
    )
    assert over.max_pairs == 0
    assert over.sampling[0].status == "unmatched"


def test_barcode_must_match():
    """时间接近但条码不同 => 不可配对。"""
    result = analyze([rec("S1", "BC-A", 0)], [rec("D1", "BC-B", 1)])
    assert result.max_pairs == 0
    assert result.candidate_pair_count == 0
    assert result.sampling[0].status == "unmatched"
    assert result.detection[0].status == "unmatched"


def test_each_record_used_at_most_once():
    """一条采样记录不能同时配两条检测记录。"""
    sampling = [rec("S1", "B", 0)]
    detection = [rec("D1", "B", 1), rec("D2", "B", 2)]
    result = analyze(sampling, detection)
    assert result.max_pairs == 1
    assert len(result.certain_pairs) <= 1


def test_pair_count_has_priority_over_total_cost():
    """先最大化配对数：宁可总时间差更大，也要多配一对。"""
    # 2 对的最小总成本是 60+540=600s；若先优化成本会只配 1 对 60s
    sampling = [rec("S1", "B", 0), rec("S2", "B", 0)]
    detection = [rec("D1", "B", 1), rec("D2", "B", 9)]
    result = analyze(sampling, detection)
    assert result.max_pairs == 2
    assert result.min_total_time_diff_ms == 600_000


def test_empty_groups():
    result = analyze([], [])
    assert result.max_pairs == 0
    assert result.certain_pairs == ()
    assert result.sampling == ()
    assert result.detection == ()


def test_mixed_statuses_in_one_instance():
    """确定配对、待核查、始终未配对可同时出现。"""
    sampling = [
        rec("S1", "BC-400", 0),    # 等价最优 -> review
        rec("S2", "BC-400", 10),   # 等价最优 -> review
        rec("S3", "BC-500", 60),   # 唯一 -> certain
        rec("S4", "BC-600", 300),  # 无候选 -> unmatched
    ]
    detection = [
        rec("D1", "BC-400", 5),
        rec("D2", "BC-400", 5),
        rec("D3", "BC-500", 63),
        rec("D4", "BC-700", 300),  # 无候选 -> unmatched
    ]
    result = analyze(sampling, detection)
    assert {(p.sampling_id, p.detection_id) for p in result.certain_pairs} == {("S3", "D3")}
    sam = verdicts_by_id(result.sampling)
    det = verdicts_by_id(result.detection)
    assert sam["S1"].status == "review" and sam["S2"].status == "review"
    assert det["D1"].status == "review" and det["D2"].status == "review"
    assert sam["S3"].status == "certain" and sam["S3"].certain_partner_id == "D3"
    assert det["D3"].status == "certain" and det["D3"].certain_partner_id == "S3"
    assert sam["S4"].status == "unmatched"
    assert det["D4"].status == "unmatched"


def test_max_scale_runs_fast():
    """12x12 满候选规模下应在可接受时间内完成。"""
    import time

    sampling = [rec(f"S{i}", "B", i) for i in range(12)]
    detection = [rec(f"D{i}", "B", i + 5) for i in range(12)]
    start = time.monotonic()
    result = analyze(sampling, detection)
    elapsed = time.monotonic() - start
    assert result.max_pairs == 12
    assert elapsed < 5.0
