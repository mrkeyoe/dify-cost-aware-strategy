# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-10-24

### Added

- `core/` 纯标准库实现，可在无 `dify_plugin` SDK 的环境下独立测试：
  - `core/sql.py`：轻量 SQL 形状解析（目标表、过滤列分类、LIMIT、聚合、`select *`），不执行 EXPLAIN。
  - `core/metadata.py`：表行数/索引列/分区列的元数据注册表，schema 前缀与大小写不敏感。
  - `core/estimator.py`：相对成本分模型（行数 × 选择性 × LIMIT 折减 × 列宽修正），未知表返回默认高成本信号。
  - `core/budget.py`：预算账本（`can_afford` / `charge` / `refund`），支持"刚好花完"。
  - `core/degrade.py`：结构化降级提示（人读文本 + 末尾可解析 JSON 块），内置采样查询/聚合表/缩小时间范围三种替代方案。
  - `core/policy.py`：调用前决策状态机 `ALLOW` / `DEGRADE` / `HALT`，连续降级计数、失败退款、外部终止。
  - `core/report.py`：运行结束成本报告（consumed / total / degrade 次数 / 终止原因 / elapsed）。
  - `core/loop.py`：成本感知规划循环，降级提示注入下一轮 `cost_hint` 与对话历史。
- `strategies/` 薄适配层：
  - `AgentStrategy` 子类 `CostAwareAgentStrategy`，`_invoke` 把 core 事件翻译为 Dify 消息；
  - 9 项策略参数（`total_budget`、`cost_overrides`、`degrade_enabled`、`max_degrade_attempts` 等）；
  - Provider 声明与 `dify_plugin` lazy import + stub，缺失 SDK 时仍可导入与单测。
- 106 个 `unittest` 用例，覆盖三条验收标准、成本单调性、降级死循环防护、最大迭代数保护、适配层接线与异常路径。
- GitHub Actions CI（Python 3.11 / 3.12，`python -m unittest discover -s tests -v`）。
- 中文 README（策略作用、参数、成本模型、快速开始、设计取舍、已知限制）。

### Known limitations

- 本机未安装 `dify_plugin`，Dify 运行时集成（真实 LLM / 工具桥接、插件清单加载）**未在本机验证**；
  `manifest.yaml`、provider/strategy YAML 的字段按公开插件目录结构编写，仍需对照目标 Dify 版本核对。
- 成本是**相对分**，不是货币；同一套 `table_metadata` + `cost_overrides` 内可比较，跨配置不可直接比较。
- SQL 解析是关键词级启发式，不含完整语法树；无法识别的形状按保守高成本处理。