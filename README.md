# dify-cost-aware-strategy

> 一个面向 [Dify](https://dify.ai) 的 **成本感知 Agent 策略**：在每次工具调用前估算相对查询成本，
> 用「运行预算 + 结构化降级提示」替代宽表/大表场景下的高代价全表扫描与不必要 API 调用。

- 纯标准库核心逻辑（`core/`），**无需 dify_plugin SDK 即可跑通全部单测**；
- 预算与降级逻辑全部封装在策略层，**不修改任何工具实现**；
- 成本估算只依赖元数据 + 查询文本，**不执行 EXPLAIN、不连数据库、不产生额外延迟**。

---

## 1. 它解决什么问题

Dify 默认的 Agent 推理循环不感知数据工具的资源成本。一个"看看订单分布"的指令，模型很容易生成：

```sql
select * from orders;          -- 1,000,000 行，全表扫描
```

而真正需要的可能只是一次采样或一次预聚合。本策略在**工具实际执行之前**插入一层成本判断：

```text
LLM 计划调用工具
      │
      ▼
[成本估算] 目标表行数 × 选择性 × LIMIT 折减 × 列宽修正  →  相对成本分
      │
      ▼
[预算账本] estimated_cost + consumed > total_budget ?
      │                                   │
     否                                  是
      ▼                                   ▼
  放行并预扣                          阻止执行（工具零调用）
  累计 consumed                       注入结构化降级提示 → LLM 重新规划
                                          │
                                          ▼
                                  连续超预算 ≥ max_degrade_attempts → 终止运行
```

运行结束时输出成本报告（consumed / total / degrade 次数 / 终止原因 / 耗时）。

---

## 2. 快速开始

### 2.1 本地跑测试（不需要 Dify、不需要联网）

```bash
git clone git@github.com:mrkeyoe/dify-cost-aware-strategy.git
cd dify-cost-aware-strategy
python -m unittest discover -s tests -v
```

预期结果（本机 Python 3.12.14 实测）：

```text
Ran 106 tests in 0.005s

OK
```

`core/` 与 `tests/` 只使用 Python 标准库；`strategies/` 对 `dify_plugin` 做 lazy import，
SDK 缺失时回退到最小 stub，因此**没有 SDK 也能导入、也能测**。

### 2.2 在 Dify 中启用

1. 把本仓库按 Dify 插件打包方式安装到 Dify（`manifest.yaml` 已声明 `agent_strategies`）；
2. 在 Agent 应用里把策略切换为 **成本感知策略 / Cost-Aware Strategy**；
3. 按下表填写参数。最小可用配置只需要 `total_budget` 与 `table_metadata`。

> ⚠️ **诚实声明**：本机没有安装 `dify_plugin`，也没有可用的 Dify 运行时，
> 因此 **"在 Dify 里实际加载并跑通"这一步未经验证**。`manifest.yaml`、provider/strategy YAML
> 的字段按公开插件目录结构编写，仍需对照你使用的 Dify 版本核对（见 §8）。

### 2.3 不装 Dify 也能演练策略行为

```python
from core.config import StrategyConfig
from core.estimator import CostEstimator
from core.policy import CostAwarePolicy

config = StrategyConfig(
    total_budget=100,
    cost_overrides={"db.scan_query": 50, "db.sample_query": 10},
)
policy = CostAwarePolicy(config, CostEstimator(config))
policy.consume(80)  # 本次运行已花掉 80

blocked = policy.before_call("db.scan_query", {"sql": "select * from orders"})
print(blocked.action)          # DecisionAction.DEGRADE
print(blocked.degrade_prompt)  # 结构化降级提示

allowed = policy.before_call("db.sample_query", {"sql": "select * from orders limit 100"})
print(allowed.action, policy.consumed)  # DecisionAction.ALLOW 90
print(policy.report().to_text())
```

---

## 3. 参数说明

| 参数 | 类型 | 默认 | 作用 |
| --- | --- | --- | --- |
| `total_budget` | number（必填） | `100` | 单次 Agent 运行的总预算（**相对单位**，不是货币） |
| `cost_overrides` | string(JSON) | `{}` | 按工具名覆盖成本，如 `{"web_search": 20, "db.scan": 50}`；优先级最高 |
| `degrade_enabled` | boolean（必填） | `true` | 超预算时注入降级提示（true）还是直接终止（false） |
| `max_degrade_attempts` | number | `3` | **连续**降级次数上限，超过即终止，防止"反复换方案仍超预算"的死循环 |
| `table_metadata` | string(JSON) | `{}` | 表元数据：`{"orders": {"row_count": 1000000, "indexed_columns": ["id"], "partition_column": "dt"}}` |
| `database_tools` | string(JSON) | `[]` | 强制按数据库查询计价的工具名列表（入参无 SQL 字段时也生效） |
| `unknown_cost` | number | `40` | 数据库工具无法确定目标表/行数时的成本（保守高成本信号） |
| `default_tool_cost` | number | `15` | 未覆盖成本的非数据库工具的静态成本 |
| `max_iterations` | number | `8` | 规划循环硬上限（独立于预算护栏的第二道保险） |

`table_metadata` 也可以写成数组形式：`[{"name": "orders", "row_count": 1000000}]`；
缺少 `row_count`（或 `rows`）的条目会被跳过，对应表按"未知高成本"处理，而不是被当成 0 行。

---

## 4. 成本模型

### 4.1 公式

```text
cost = clamp( 5 × log10(max(row_count, 10)), 1, 60 )
     × selectivity            # WHERE 选择性
     × limit_factor           # LIMIT 折减（聚合时恒为 1）
     × column_factor          # select * 且非聚合 = 1.15
     下限 0.5，保留两位小数
```

以 `orders` 表（1,000,000 行，`id/user_id` 有索引，`dt` 为分区列）为例，实测输出：

| 查询 | 成本 | 说明 |
| --- | ---: | --- |
| `select * from orders` | **34.5** | 无 WHERE，全表扫描 |
| `select * from orders where status = 'paid'` | **8.62** | 等值过滤但未命中索引 |
| `select * from orders where dt = '2026-10-01'` | **4.14** | 命中分区裁剪 |
| `select * from orders where id = 42` | **2.76** | 命中索引等值查找 |
| `select * from orders limit 100` | **0.69** | LIMIT 折减（下限 2%） |
| `select count(*) from orders` | **30.0** | 聚合不享受 LIMIT 折减（仍要扫全表） |

### 4.2 选择性系数（越小越便宜）

| 情形 | 系数 |
| --- | ---: |
| 无 WHERE | `1.0` |
| 命中分区列 | `0.12` |
| 索引等值 | `0.08` |
| 索引 IN 列表 | `0.15` |
| 普通等值（未命中索引） | `0.25` |
| 范围过滤 | `0.35` |
| 非索引 IN | `0.40` |
| 有过滤但无法识别列 | `0.60` |
| LIKE | `0.70` |

### 4.3 判定与兜底顺序

1. `cost_overrides` 精确命中工具名 → 直接用该静态成本（`basis=override`）；
2. 判定为数据库工具 → 按元数据 + SQL 形状估算（`basis=table_rows`）；
3. 非数据库工具 → `default_tool_cost`（`basis=static`）；
4. 数据库工具但**目标表未知 / 无元数据** → `unknown_cost=40`，`confidence=low`，`basis=unknown`。

数据库工具的判定（任一成立）：在 `database_tools` 中；入参含严格 SQL 字段（`sql`、`statement`、
`sql_query`、`query_sql`、`sqlText`）；入参 `query` 字段既像 SQL **又**含 `FROM`（避免把
`web_search` 的 `query` 误判成 SQL）；或工具名命中 `sql|query|db|warehouse|bigquery|clickhouse|postgres|mysql|hive|spark|trino|snowflake|duckdb|sqlite|redshift|doris|starrocks`。

> 这些系数是**经验启发式**，目的是让"全表扫描 ≫ 分区扫描 ≫ 索引点查"的**大小关系稳定**，
> 不是数据库优化器的代价模型。跨配置的数字不可直接比较。

---

## 5. 降级提示长什么样

被拦下的调用不会执行，取而代之的是这样一段**结构化**提示（同时进入下一轮规划的系统槽位与对话历史）：

```text
[BUDGET_CONSTRAINT] 本次工具调用被预算策略阻止，未执行。
status: blocked
tool: db.scan_query
estimated_cost: 50 (override, confidence=high)
consumed: 80 / total_budget: 100
remaining: 20 | deficit: 30
degrade_attempt: 1/3
cost_note: 由 cost_overrides 配置的静态权重
alternatives:
  - sample: 采样查询 — 加 LIMIT / TABLESAMPLE 只取少量样本，先验证假设再决定是否需要全量
  - preaggregate: 聚合表 — 改读已预聚合的宽表子集或物化视图，避免明细全表扫描
  - narrow_time_range: 缩小时间范围 — 补充分区过滤条件（如 dt BETWEEN ...），只扫描必要分区
plan_instruction: 不要重复提交同一调用；请从 alternatives 中选择一个成本更低的方案，或直接基于已获得的结果作答。若再次超预算且达到 max_degrade_attempts，本次运行会终止。
constraint: {"alternatives": ["sample", "preaggregate", "narrow_time_range"], "consumed": 80.0, ..., "status": "blocked", "tool": "db.scan_query", "type": "budget_blocked", "version": 1}
```

- 前 7 行是**人读约束**（固定字段顺序，便于日志检索）；
- `alternatives` 是三个**可执行方向**，不是泛泛的"请省钱"；
- 最后一行 `constraint:` 是可 `json.loads` 的机器可读块，方便宿主采集/断言。

运行结束的报告（`CostReport.to_text()`）：

```text
成本报告 / cost report
- status: degraded
- consumed: 90.00 / 100.00 (remaining 10.00)
- degrade_count: 1
- calls: allowed=1 blocked=1 failed=0
- tool_costs: db.sample_query=10.00
- degrade_reasons:
  - [budget_exceeded] db.scan_query (estimated 50.00, attempt 1/3): db.scan_query 估算成本 50.00（override）超出剩余预算 20.00，已阻止执行并要求重新规划
- elapsed_seconds: 0.420
```

---

## 6. 验收标准如何被覆盖

| 验收标准 | 测试位置 |
| --- | --- |
| 已消耗 80、再调成本 50 → 阻止执行并注入降级提示 | `tests/test_acceptance.py::TestCriterion1BlockAndInject`、`tests/test_policy.py::TestDegradePath` |
| 降级后选择成本 10 的采样查询 → 放行且 `consumed=90` | `tests/test_acceptance.py::TestCriterion2SamplerIsAllowed`、端到端 `TestEndToEndLoopAcceptance` |
| 运行结束报告含 `degraded=true` 与具体降级原因 | `tests/test_acceptance.py::TestCriterion3Report`、`tests/test_report.py` |
| 成本随行数单调递增 / 索引 < 分区 < 非索引 < 全扫 | `tests/test_estimator.py` |
| `max_degrade_attempts` 防死循环 | `tests/test_acceptance.py::TestDegradeLoopGuard`、`tests/test_policy.py::TestHaltPaths` |
| 最大迭代数保护、失败退款 | `tests/test_acceptance.py::TestIterationGuard`、`tests/test_policy.py::TestFailureRefund` |
| 适配层接线（无 SDK 可导入、未接线时报错清晰） | `tests/test_adapter.py` |

---

## 7. 项目结构

```text
.
├── core/                     # 纯标准库：可在无 SDK 环境独立测试
│   ├── sql.py                #   SQL 形状解析（表/过滤列/LIMIT/聚合），不执行 EXPLAIN
│   ├── metadata.py           #   表行数/索引/分区元数据注册表
│   ├── estimator.py          #   相对成本模型
│   ├── budget.py             #   预算账本（can_afford/charge/refund）
│   ├── policy.py             #   ALLOW / DEGRADE / HALT 状态机
│   ├── degrade.py            #   结构化降级提示（文本 + JSON 块）
│   ├── report.py             #   成本报告
│   └── loop.py               #   成本感知规划循环（LLM/Executor 以协议注入）
├── strategies/               # 薄适配层：只做翻译
│   ├── _sdk.py               #   dify_plugin lazy import + 最小 stub
│   ├── schema.py             #   9 项策略参数声明
│   ├── cost_aware.py         #   AgentStrategy 子类（_invoke 事件→消息）
│   ├── provider.py           #   Provider
│   └── cost_aware_strategy.yaml
├── provider/                 # provider 声明 + 转导出入口
├── tests/                    # 106 个 unittest 用例（含三条验收标准）
├── manifest.yaml             # Dify 插件清单（待对照目标版本核对）
├── main.py                   # 插件进程入口（守卫式导入）
├── pyproject.toml / requirements.txt
└── .github/workflows/ci.yml  # py3.11/3.12 矩阵跑 unittest
```

---

## 8. 设计取舍

1. **相对成本分，而非货币成本。** 精确代价模型需要统计信息、EXPLAIN、甚至真实执行，
   都会给 Agent 循环增加延迟与不确定性。这里只要求"可比较"：同一套配置下，
   索引点查一定比全表扫描便宜，这就足以驱动预算决策。
2. **不执行 EXPLAIN。** EXPLAIN 本身可能触发优化器开销、需要连接权限，且在不同引擎上语义不一。
   代价是**估算精度换稳定延迟**：元数据缺失时宁可给高成本（`unknown_cost`），也不猜低。
3. **预算在策略层，工具零改动。** 拦截点放在"调用之前"，被拦下的工具根本不会被执行；
   不需要给每个工具加装饰器，也不会污染工具实现。
4. **降级是"结构化提示 + 次数上限"，不是自动改写 SQL。** 自动改写（比如给任意 SQL 塞 LIMIT）
   容易改变语义、制造错误结论；让 LLM 在明确约束下重新规划更安全。`max_degrade_attempts`
   是防死循环的硬闸门：连续超预算达到上限直接终止，并如实写进报告。
5. **预扣（charge-before-execute）+ 失败退款。** 先记账再执行，避免并发/重试绕开预算；
   工具失败则退款，防止"失败也计费"把预算白白吃掉。
6. **连续降级计数，而非累计。** 一旦有一次调用被放行，计数清零——"每次都能挤出一点预算"
   不应被误判为死循环；只有**连续**无法推进才终止。
7. **单次运行内状态，不持久化。** 账本随运行创建、随报告丢弃，避免跨会话串账；
   报告通过消息返回给宿主，需要长期留存时由宿主侧采集。

---

## 9. 已知限制与诚实声明

1. **Dify 运行时集成未在本机验证。** 本机没有 `dify_plugin` SDK，也没有 Dify 实例。
   - 已验证：`core/` 全部逻辑 + `strategies/` 的**接线**（用 fake LLM/Executor 注入，覆盖拦截、提示注入、记账、报告、异常路径）。
   - 未验证：真实 `AgentStrategy` 的构造签名、`_invoke` 的宿主调用约定、消息工厂的确切位置、
     `manifest.yaml` / provider / strategy YAML 的字段是否与目标 Dify 版本完全一致。
   - 因此 `strategies/cost_aware.py` 中 `_build_llm` / `_build_executor` 默认抛
     `IntegrationNotWired`，**不会假装已经接好**：真正接入时需要把 `session.model.llm` 与工具调用
     适配成 `core.loop.LLMProtocol` / `ToolExecutorProtocol`。
2. **成本是相对分，不是账单。** 请勿把它当成美元/人民币；它的用途是**排序与限额**。
   默认 `total_budget=100` 只是直觉化的刻度。
3. **SQL 解析是启发式。** 支持常见的 `SELECT ... FROM/JOIN ... WHERE ... GROUP BY/ORDER BY/LIMIT`，
   不做完整语法树。CTE、子查询、UNION、复杂表达式可能估不准；估不准时按保守高成本处理。
   `select *` 的列宽修正固定为 1.15，不感知真实列数与字节数。
4. **元数据需要人工提供且会过期。** `row_count` 是配置时的静态值；表快速增长后成本分会偏低，
   需要定期更新 `table_metadata`（本插件刻意不自动采集，以免引入数据库连接与权限问题）。
5. **非数据库工具只有静态权重。** `cost_overrides` 是常量，不感知 token 数、响应时间或限流；
   需要更细粒度计量时应在工具侧埋点，而不是让策略猜。
6. **降级提示依赖 LLM 的遵循能力。** 提示是结构化的，但模型仍可能重复提交同一调用；
   `max_degrade_attempts` 与 `max_iterations` 是兜底，不是"保证模型一定听话"。
7. **CI 只跑单测。** 不覆盖真实 Dify 环境的端到端；也不做 lint/type-check（当前以最小依赖为先）。

---

## 10. 许可

[MIT](LICENSE) © 2026 mrkeyoe