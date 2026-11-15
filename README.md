# 采样配对与质控漂移审计服务

本服务面向实验室仪器主管与质控员，提供两个互相独立的接口：

1. **`POST /match`** —— 把人工采样登记与仪器检测记录按"同条码 + 时间窗"配对，
   只把**所有同等最优方案中都成立**的配对列为确定配对，绝不任选一套最优方案
   冒充确定结论；
2. **`POST /drift-audit`** —— 根据连续批次读数的**误差区间**判定各批是否必然报警。
   仪器只给出读数误差区间时，服务**不使用中点代替实际读数**，而是对区间内所有
   可能整数读数逐批判定"必报警 / 可能报警 / 不报警"。

`GET /docs` 是 Swagger UI，两个接口都内置了可直接点击 **Try it out** 运行的演示
请求，无需手工拼 JSON。

## 快速开始（Docker Compose）

```bash
docker compose up --build
```

服务容器名为 `lab-qc-api`，监听 `http://localhost:8000`：

- `GET /docs` —— Swagger UI，可操作两个接口；Examples 下拉框内置演示请求；
- `POST /match` —— 采样-检测记录配对；
- `POST /drift-audit` —— 连续批次质控偏差报警审计；
- `GET /health` —— 健康检查（Compose healthcheck 使用）。

```bash
curl -s http://localhost:8000/health
# {"status":"ok"}
```

## 一、漂移审计 `/drift-audit`

### 请求

```json
{
  "batches": [
    {"low": 1, "high": 2},
    {"low": 1, "high": 2},
    {"low": 1, "high": 2},
    {"low": 2, "high": 2}
  ],
  "k": 0,
  "h": 4
}
```

| 字段 | 含义 | 约束 |
| --- | --- | --- |
| `batches` | 同一仪器从批次 0 起连续编号的整数偏差闭区间，数组顺序即批次编号 | 0–24 批；`low <= high`；端点绝对值 ≤ 20 |
| `k` | 整数允差，每批先从偏差中扣除 | 0–20 的整数 |
| `h` | 警戒值，任一侧累计达到即报警 | 1–40 的整数 |

每批区间推荐写成 `{"low": a, "high": b}`；同时接受二元数组 `[a, b]`、
`{"min": a, "max": b}`、`{"start": a, "end": b}`、`{"lower": a, "upper": b}`。
可选的批次编号字段 `index`（别名 `batch`、`batch_index`）若提供，必须等于该区间
在数组中的位置，从而显式保证从 0 连续编号。

### 报警规则

对某一批的真实整数偏差 `d`：

```
pos = max(0, pos + d - k)      # 正向累计
neg = max(0, neg - d - k)      # 负向累计
```

- **任一侧累计达到 `h`（`pos >= h` 或 `neg >= h`）即在该批报警**；
- 报警后**两侧同时清零**，下一批从 `pos = 0, neg = 0` 重新累计；
- 未报警时保留累计值进入下一批。

仪器给出的是一批读数的**误差闭区间** `[low, high]`，真实读数可为其中任意整数。
服务枚举所有可能读数（而不是取中点）。更关键的是：同一段历史读数会同时决定
`(pos, neg)`，报警又会让两者一起清零，因此正向、负向累计不能各自独立取极值。
服务逐批维护**所有可达 `(pos, neg)` 状态集合**，后续判定都基于该集合，
从而完整保留此前读数与报警重置造成的状态关联。

### 响应

```json
{
  "summary": {
    "batch_count": 4,
    "k": 0,
    "h": 4,
    "will_alarm_count": 0,
    "may_alarm_count": 3,
    "no_alarm_count": 1,
    "first_certain_alarm_index": null
  },
  "batches": [
    {
      "index": 0,
      "low": 1,
      "high": 2,
      "status": "no_alarm",
      "alarm_certain": false,
      "alarm_possible": false,
      "possible_alarm_deviations": []
    },
    {
      "index": 1,
      "low": 1,
      "high": 2,
      "status": "may_alarm",
      "alarm_certain": false,
      "alarm_possible": true,
      "possible_alarm_deviations": [2]
    }
  ]
}
```

| `status` | 含义 | 处置建议 |
| --- | --- | --- |
| `will_alarm` | 必报警：区间内**所有**读数、**所有**可能历史都在本批报警 | 按已报警处理并复校 |
| `may_alarm` | 可能报警：部分读数或部分历史在本批报警 | 复核原始记录 / 加测 |
| `no_alarm` | 不报警：所有读数与所有可能历史都不在本批报警 | 继续监测 |

- `index`：批次编号，从 0 起连续；
- `alarm_certain` / `alarm_possible`：与 `status` 等价的布尔判定；
- `possible_alarm_deviations`：存在某个可能历史，使该整数偏差在本批触发报警的
  读数集合；它为空当且仅当 `alarm_possible=false`。注意**不是**只看单批偏差：
  该集合已计入此前批次累计与报警清零的影响；
- `summary.first_certain_alarm_index`：首个必报警批次编号，不存在为 `null`。

### 演示请求（/docs 内置）

| 名称 | 场景 |
| --- | --- |
| `gradual_positive_drift` | 正向缓漂：单批偏差小，但累计状态使报警可能性逐批扩大 |
| `reset_after_alarm` | 报警后清零：后续批次从 `(0,0)` 重新累计，不会错误继承旧累计 |
| `both_sides_uncertain` | 双向不确定：宽区间使正负两侧都可能越限，逐批随历史分化 |

```bash
curl -X POST http://localhost:8000/drift-audit \
  -H 'Content-Type: application/json' \
  -d '{"batches":[{"low":3,"high":4},{"low":-1,"high":1},{"low":-4,"high":-3}],"k":0,"h":3}'
```

### 校验规则（不满足返回 422）

- `batches` 最多 24 批；每批 `low <= high`；端点绝对值不超过 20；
- `k` 为 0–20 的整数，`h` 为 1–40 的整数；
- 区间端点为整数；不接受 `extra` 未定义字段。

## 二、记录配对 `/match`

### 配对规则

- 输入两组记录：采样登记、检测记录，**每组最多 12 条**。
- 每条记录：组内唯一 `id`、可重复的容器条码 `barcode`、UTC 时间 `timestamp`
  （ISO 8601，必须带时区，推荐 `Z` 后缀）。
- 两条记录**条码相同且 UTC 时间差 ≤ 10 分钟（含边界）**才可配对。
- 每条记录至多参与一对。
- 优化目标（字典序）：**先使配对数最多，再使时间差总和最小**。

### 输出语义

| 状态 | 含义 | 处置建议 |
| --- | --- | --- |
| `certain` | 确定配对：在所有同等最优方案中都成立 | 可进入分析库 |
| `review` | 待核查：存在同等最优方案分歧（对象不定或时有时无） | 交质控员人工核查 |
| `unmatched` | 始终未配对：任何同等最优方案都不包含它 | 不进入分析库 |

`review` 记录附带：

- `matched_in_all_optimal`：`true` 表示"始终被配对但对象不定"，`false` 表示
  "部分最优方案中未配对"；
- `possible_partners`：在至少一个同等最优方案中可行的配对对象及时间差。

### 请求示例

```bash
curl -X POST http://localhost:8000/match \
  -H 'Content-Type: application/json' \
  -d '{
    "sampling_records": [
      {"id": "S-01", "barcode": "BC-100", "timestamp": "2026-11-14T08:00:00Z"}
    ],
    "detection_records": [
      {"id": "D-01", "barcode": "BC-100", "timestamp": "2026-11-14T08:04:00Z"}
    ]
  }'
```

### 校验规则（不满足返回 422）

- 每组 0–12 条记录；
- 同组内 `id` 不得重复；
- `id`、`barcode` 非空；
- `timestamp` 必须带时区（带偏移量的时间会归一化为 UTC 再比较）；
- 不接受未定义的字段。

## 算法说明

### 漂移审计

逐批维护可达状态集合 `{(pos, neg)}`，初始 `{(0, 0)}`。对每个状态与本批每个
整数读数 `d`：若 `pos + d - k >= h` 或 `neg - d - k >= h`，该迁移记为报警并汇入
下一批状态 `(0, 0)`；否则汇入 `(max(0, pos+d-k), max(0, neg-d-k))`。本批报警
迁移数等于 0 / 全部 / 介于两者之间，分别对应 `no_alarm` / `will_alarm` /
`may_alarm`。未报警的 `(pos, neg)` 均 `< h`，状态数不超过 `h²`（≤ 1600），
最坏 24 批 × 41 个读数 × 1600 状态的判定在 1 秒内完成。实现已与 3000 组随机
小规模用例的**全读数组合暴力枚举**交叉验证一致。

### 记录配对

建模为二分图最小费用最大流：左部采样记录、右部检测记录，候选边权为时间差
（毫秒整数）。持续沿最短路增广直到不可增广，即得"配对数最大、同流量下总时间差
最小"的字典序最优匹配。

确定性判定通过精确重算完成，而非启发式：

- **确定配对**：禁用该候选边后最优值（配对数, 总时间差）变差 ⟺ 它存在于所有
  同等最优方案；
- **可行对象**：强制选入该候选边（删除两端点）后仍能达到整体最优值 ⟺ 它存在于
  某个同等最优方案；
- **始终被配对**：删除该记录后最优值变差 ⟺ 它在所有同等最优方案中都被配对。

每组 ≤ 12 条记录时候选边 ≤ 144 条，重算约 200 次微型流，单次请求开销在毫秒级。
算法结果已用 3000 个随机小规模用例与暴力枚举全部匹配交叉验证一致。

## 本地开发与测试

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt pytest httpx
pytest                 # 运行测试
uvicorn app.main:app --reload
```

## 目录结构

```
app/
  main.py            # FastAPI 入口、响应组装、/docs 文案
  drift.py           # 漂移审计核心（逐批可达状态集合）
  drift_schemas.py   # /drift-audit 请求/响应模型与校验
  drift_examples.py  # /drift-audit 内置演示请求
  matching.py        # 配对核心算法（最小费用最大流 + 确定性判定）
  schemas.py         # /match 请求/响应模型与校验
  examples.py        # /match 内置演示请求
tests/               # 两个算法单元测试与 API 集成测试
Dockerfile
docker-compose.yml
```
