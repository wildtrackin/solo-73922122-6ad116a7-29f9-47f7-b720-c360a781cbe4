"""采样登记与仪器检测记录的确定性配对核心算法。

建模为二分图匹配：
  * 左部 = 人工采样登记，右部 = 仪器检测记录；
  * 两侧记录条码相同且 UTC 时间差不超过 10 分钟（含边界）时连边，
    边权 = 时间差（毫秒，整数，避免浮点误差）；
  * 每条记录至多参与一对 => 匹配约束。

优化目标（字典序）：先最大化配对数，再最小化时间差总和。
用最小费用最大流求解：边权非负，不断沿 s->t 最短路增广直到不存在增广路。
增广到不能增广 => 配对数最大；SSP（successive shortest paths）不变式
保证同一流量下费用最小 => 时间差总和最小。

确定性判定（不得任选一套最优方案冒充确定结论）：
  * essential 边：把该边禁用后重算，若最优值（配对数, 总时间差）变差，
    则该边存在于所有同等最优解 => 确定配对；
  * possible 边：把该边强制选入（删除其两个端点后重算），若剩余部分
    仍能达到整体最优值，则该边存在于某个同等最优解；
  * 记录级：把该记录删除后重算，若最优值变差，则它在所有最优解中都被配对。

规模：每组 <= 12 条记录，候选边 <= 144 条，重算约 200 次微型流，开销可忽略。
"""

from __future__ import annotations

from dataclasses import dataclass, field

MAX_TIME_DIFF_MS = 10 * 60 * 1000  # 配对窗口：10 分钟（含边界）

STATUS_CERTAIN = "certain"      # 确定配对：在所有同等最优方案中都成立
STATUS_REVIEW = "review"        # 待核查：在部分（或全部但对象不定）最优方案中出现
STATUS_UNMATCHED = "unmatched"  # 始终未配对：任何同等最优方案都不包含它


@dataclass(frozen=True)
class Record:
    """一条待配对记录。ts_ms 为 UTC epoch 毫秒。"""

    id: str
    barcode: str
    ts_ms: int


@dataclass(frozen=True)
class Pair:
    sampling_id: str
    detection_id: str
    barcode: str
    time_diff_ms: int


@dataclass(frozen=True)
class PartnerOption:
    """某个同等最优方案中可行的配对对象。"""

    id: str
    time_diff_ms: int


@dataclass(frozen=True)
class RecordVerdict:
    id: str
    status: str  # STATUS_CERTAIN / STATUS_REVIEW / STATUS_UNMATCHED
    matched_in_all_optimal: bool
    certain_partner_id: str | None
    possible_partners: tuple[PartnerOption, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class Analysis:
    max_pairs: int
    min_total_time_diff_ms: int
    candidate_pair_count: int
    certain_pairs: tuple[Pair, ...]
    sampling: tuple[RecordVerdict, ...]
    detection: tuple[RecordVerdict, ...]


class _MinCostMaxFlow:
    """最小费用最大流（SSP：每次沿 s->t 最短路增广 1 单位）。

    节点数 <= 26、边数 <= 数百，Bellman-Ford 足够快且对任意符号的权都正确。
    返回的最大流在同等流量下费用最小。
    """

    __slots__ = ("n", "g")

    def __init__(self, n: int) -> None:
        self.n = n
        # 边: [to, residual_cap, cost, reverse_edge_index]
        self.g: list[list[list[int]]] = [[] for _ in range(n)]

    def add_edge(self, u: int, v: int, cap: int, cost: int) -> None:
        fwd = [v, cap, cost, len(self.g[v])]
        rev = [u, 0, -cost, len(self.g[u])]
        self.g[u].append(fwd)
        self.g[v].append(rev)

    def solve(self, s: int, t: int) -> tuple[int, int]:
        """返回 (最大流量, 该最大流量下的最小费用)。"""
        n, g = self.n, self.g
        flow = 0
        cost = 0
        inf = float("inf")
        while True:
            dist = [inf] * n
            prev_v = [-1] * n
            prev_e = [-1] * n
            dist[s] = 0
            for _ in range(n - 1):
                updated = False
                for u in range(n):
                    du = dist[u]
                    if du == inf:
                        continue
                    for i, e in enumerate(g[u]):
                        if e[1] > 0:
                            nd = du + e[2]
                            if nd < dist[e[0]]:
                                dist[e[0]] = nd
                                prev_v[e[0]] = u
                                prev_e[e[0]] = i
                                updated = True
                if not updated:
                    break
            if dist[t] == inf:
                return flow, cost
            # 源点出边容量均为 1，瓶颈必为 1，直接增广 1 单位
            v = t
            while v != s:
                e = g[prev_v[v]][prev_e[v]]
                e[1] -= 1
                g[v][e[3]][1] += 1
                v = prev_v[v]
            flow += 1
            cost += int(dist[t])


def _optimal_matching(
    n_left: int, n_right: int, edges: list[tuple[int, int, int]]
) -> tuple[int, int, frozenset[tuple[int, int]]]:
    """求字典序最优（配对数最大、时间差总和最小）匹配。

    edges: (左下标, 右下标, 时间差ms)。
    返回 (配对数, 总时间差ms, 其中一个最优匹配的边集合)。
    """
    src = n_left + n_right
    sink = src + 1
    mf = _MinCostMaxFlow(sink + 1)
    for l in range(n_left):
        mf.add_edge(src, l, 1, 0)
    for r in range(n_right):
        mf.add_edge(n_left + r, sink, 1, 0)
    for l, r, c in edges:
        mf.add_edge(l, n_left + r, 1, c)
    pairs, cost = mf.solve(src, sink)
    matched: set[tuple[int, int]] = set()
    for l in range(n_left):
        for e in mf.g[l]:
            # g[l] 中指向右部节点且残余容量为 0 的正向边 = 被使用的匹配边
            if n_left <= e[0] < n_left + n_right and e[1] == 0:
                matched.add((l, e[0] - n_left))
    return pairs, cost, frozenset(matched)


def analyze(sampling: list[Record], detection: list[Record]) -> Analysis:
    """对两组记录做确定性配对分析。"""
    n_left, n_right = len(sampling), len(detection)

    # 1) 候选边：同条码且 |Δt| <= 10 分钟
    diff_of: dict[tuple[int, int], int] = {}
    for li, s in enumerate(sampling):
        for ri, d in enumerate(detection):
            if s.barcode == d.barcode:
                diff = abs(s.ts_ms - d.ts_ms)
                if diff <= MAX_TIME_DIFF_MS:
                    diff_of[(li, ri)] = diff
    edges = [(l, r, c) for (l, r), c in diff_of.items()]

    # 2) 字典序最优值及其中一个最优匹配
    opt_pairs, opt_cost, one_optimal = _optimal_matching(n_left, n_right, edges)

    def optimum_of(remaining: list[tuple[int, int, int]]) -> tuple[int, int]:
        pairs, cost, _ = _optimal_matching(n_left, n_right, remaining)
        return pairs, cost

    # 3) essential 边（必属于已求出的最优匹配）：禁用后最优值变差 =>
    #    存在于所有同等最优解 => 确定配对
    essential: set[tuple[int, int]] = set()
    for l, r in one_optimal:
        reduced = [e for e in edges if not (e[0] == l and e[1] == r)]
        if optimum_of(reduced) != (opt_pairs, opt_cost):
            essential.add((l, r))

    # 4) possible 边：强制选入（删除两端点）后剩余部分仍可达整体最优值 =>
    #    存在于某个同等最优解
    possible: set[tuple[int, int]] = set(one_optimal)
    for (l, r), c in diff_of.items():
        if (l, r) in possible:
            continue
        reduced = [e for e in edges if e[0] != l and e[1] != r]
        pairs, cost = optimum_of(reduced)
        if pairs + 1 == opt_pairs and cost + c == opt_cost:
            possible.add((l, r))

    # 5) 记录级判定：删除该记录后最优值变差 => 它在所有最优解中都被配对
    def left_matched_in_all(li: int) -> bool:
        return optimum_of([e for e in edges if e[0] != li]) != (opt_pairs, opt_cost)

    def right_matched_in_all(ri: int) -> bool:
        return optimum_of([e for e in edges if e[1] != ri]) != (opt_pairs, opt_cost)

    def possible_partners_of(idx: int, is_left: bool) -> list[tuple[int, int]]:
        """[(对方下标, 时间差ms)]，仅含在某个同等最优解中可行的对象。"""
        out = []
        for l, r in possible:
            if is_left and l == idx:
                out.append((r, diff_of[(l, r)]))
            elif not is_left and r == idx:
                out.append((l, diff_of[(l, r)]))
        return out

    def build_verdicts(
        side: list[Record],
        other: list[Record],
        is_left: bool,
    ) -> tuple[RecordVerdict, ...]:
        verdicts = []
        for idx, rec in enumerate(side):
            partners = possible_partners_of(idx, is_left)
            has_essential = any(
                (l if is_left else r) == idx for l, r in essential
            )
            if has_essential:
                # 确定配对的记录在所有最优解中对象唯一（否则该边不可能 essential）
                status = STATUS_CERTAIN
                matched_in_all = True
                certain_partner_id = other[partners[0][0]].id
            elif partners:
                status = STATUS_REVIEW
                matched_in_all = (
                    left_matched_in_all(idx) if is_left else right_matched_in_all(idx)
                )
                certain_partner_id = None
            else:
                status = STATUS_UNMATCHED
                matched_in_all = False
                certain_partner_id = None
            options = tuple(
                sorted(
                    (PartnerOption(id=other[j].id, time_diff_ms=c) for j, c in partners),
                    key=lambda o: o.id,
                )
            )
            verdicts.append(
                RecordVerdict(
                    id=rec.id,
                    status=status,
                    matched_in_all_optimal=matched_in_all,
                    certain_partner_id=certain_partner_id,
                    possible_partners=options,
                )
            )
        return tuple(verdicts)

    sampling_verdicts = build_verdicts(sampling, detection, is_left=True)
    detection_verdicts = build_verdicts(detection, sampling, is_left=False)

    certain_pairs = tuple(
        sorted(
            (
                Pair(
                    sampling_id=sampling[l].id,
                    detection_id=detection[r].id,
                    barcode=sampling[l].barcode,
                    time_diff_ms=diff_of[(l, r)],
                )
                for l, r in essential
            ),
            key=lambda p: (p.sampling_id, p.detection_id),
        )
    )

    return Analysis(
        max_pairs=opt_pairs,
        min_total_time_diff_ms=opt_cost,
        candidate_pair_count=len(edges),
        certain_pairs=certain_pairs,
        sampling=sampling_verdicts,
        detection=detection_verdicts,
    )
