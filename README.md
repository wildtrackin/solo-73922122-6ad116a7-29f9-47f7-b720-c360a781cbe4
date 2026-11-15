# 采样-检测记录配对服务

实验室数据工程师用本服务把**人工采样登记**与**仪器检测记录**对上号，供质控员判断哪些
结果可以进入分析库。服务只把**所有同等最优方案中都成立**的配对列为确定配对，绝不
任选一套最优方案冒充确定结论。

## 配对规则

- 输入两组记录：采样登记、检测记录，**每组最多 12 条**。
- 每条记录：组内唯一 `id`、可重复的容器条码 `barcode`、UTC 时间 `timestamp`
  （ISO 8601，必须带时区，推荐 `Z` 后缀）。
- 两条记录**条码相同且 UTC 时间差 ≤ 10 分钟（含边界）**才可配对。
- 每条记录至多参与一对。
- 优化目标（字典序）：**先使配对数最多，再使时间差总和最小**。

## 输出语义

| 状态 | 含义 | 处置建议 |
| --- | --- | --- |
| `certain` | 确定配对：在所有同等最优方案中都成立 | 可进入分析库 |
| `review` | 待核查：存在同等最优方案分歧（对象不定或时有时无） | 交质控员人工核查 |
| `unmatched` | 始终未配对：任何同等最优方案都不包含它 | 不进入分析库 |

`review` 记录附带：

- `matched_in_all_optimal`：`true` 表示"始终被配对但对象不定"，`false` 表示
  "部分最优方案中未配对"；
- `possible_partners`：在至少一个同等最优方案中可行的配对对象及时间差。

## 快速开始（Docker Compose）

```bash
docker compose up --build
```

服务监听 `http://localhost:8000`：

- `POST /match` —— 配对接口；
- `GET /docs` —— Swagger UI，**/match 的 Examples 下拉框内置三组演示请求**：
  - `duplicate_barcodes`（重复条码）：同条码多条记录，按时间窗唯一锁定；
  - `crossing_candidates`（交叉候选）：时间窗交叉，贪心最近邻总成本更差；
  - `equivalent_optima`（等价最优）：两套同等最优方案并存，接口只输出确定配对，
    其余标记待核查；
- `GET /health` —— 健康检查（Compose healthcheck 使用）。

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

## 校验规则（不满足返回 422）

- 每组 0–12 条记录；
- 同组内 `id` 不得重复；
- `id`、`barcode` 非空；
- `timestamp` 必须带时区（带偏移量的时间会归一化为 UTC 再比较）；
- 不接受未定义的字段。

## 算法说明

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
  main.py       # FastAPI 入口与响应组装
  matching.py   # 配对核心算法（最小费用最大流 + 确定性判定）
  schemas.py    # 请求/响应模型与校验
  examples.py   # /docs 内置的三组演示请求
tests/          # 算法单元测试与 API 集成测试
Dockerfile
docker-compose.yml
```
