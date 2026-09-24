# V2 开发进度

最后更新：2026-09-24（P0/P1 开发轮）。

本文件由开发执行者持续更新；计划正文见 [V2 开发与验收方案](evidence-driven-forecast-v2-plan.md)，启动提示词见 [GPT-6 交接](gpt6-v2-handoff.md)。

## 当前状态

- 已完成：方案编写、基线核对、P0 固定目标与价格口径、P1 持久化任务骨架。
- 基线代码：`a135bf9ca983008b5690dd254358e3aba06e12ae`。
- 本轮启动前：143 项旧代码测试和前端构建通过，工作树干净。
- 本轮已应用追加式 V2 迁移到本机开发数据库；旧表和旧预测语义未改。V2 路由可排队和查询，worker 仅有测试注入的处理器；默认 worker 会明确记录 `v2_processor_not_configured`，不会生成伪预测。
- 未实施：P2–P8 的真实证据研究、同口径训练、联合模型、前端 V2 流程、调度切换及到期评估。旧证据修订仍保持原数值概率。
- 下一步：P2 动态证据集合和冻结来源版本；P1 已有任务框架承接，不能把其 fixture 测试当作真实联合预测。

| 阶段 | 状态 | 代码/真实验收证据 |
|---|---|---|
| P0 预测合同与价格口径 | verified | 20 个 XNYS 交易日固定目标、绝对收益 ±2% 边界、根子合同/到期校验；固定拆股与分红 fixture；Yahoo 真实 AAPL 2020-08-31 4:1 拆股及无事件区间响应核验。已知跨期拆股阻断。 |
| P1 持久化与统一任务 | verified (skeleton) | 五张 V2 表追加迁移、幂等任务、租约/过期执行者隔离、原子版本发布、分支、API 202/查询、worker fixture；本机 `--check` 显示五表存在。真实处理器待 P2–P4。 |
| P2 动态证据集合 | not_started | — |
| P3 同口径市场基线 | not_started | — |
| P4 联合模型与渠道验证 | not_started | — |
| P5 主动预测与手动修订 | not_started | — |
| P6 每小时自动触发 | not_started | — |
| P7 到期评估 | not_started | — |
| P8 端到端交付 | not_started | — |

## 2026-09-24：P0/P1 验收记录

- P0 文件：`app/forecast_contract.py`、`app/market_data.py` 与对应测试。价格 `close` 仍取 provider `quote.close`；`adjclose` 仅检查，不混入收益口径。行情响应记录拆股、现金分红和公司行动字段形态；跨已知拆股区间无法建立合同。真实 Yahoo 请求在 AAPL 历史拆股区间返回 4:1 事件；近期无事件区间省略 `events` 字段，项目将其记录为“供应商报告零个事件”，不称独立证实未来无公司行动。
- P1 文件：`app/forecast_v2_models.py`、`app/forecast_jobs.py`、`app/forecast_v2.py`、`app/forecast_worker.py`、`app/forecast_v2_api.py`、`scripts/migrate_forecast_v2.py` 与对应测试。`--check` 只读，`--apply` 对 V2 表使用事务锁，支持重复运行；旧 API 在启动时不自动创建 V2 表。
- 验证：路由接入后全项目 **178 passed**，前端生产构建通过；开发数据库迁移重复 `--apply` 与只读 `--check` 均显示 `missing=[]`，五张 V2 表均在。fixture 能创建 `research_only` 版本，`joint_probabilities=null`；未进行真实 V2 模型调用。
- 明确限制：财报/媒体事实尚未进入 V2 模型；V2 worker 默认没有真实处理器；V2 路由未接前端；真实新公告触发、前向到期评估没有验证。P1 骨架通过不等于 V2 产品完成。

## 每次交接追加模板

```text
日期 / 阶段 / 状态：
本轮实现：
改动文件与提交：
验收命令及退出码：
真实输入 / fixture / mock 的分别结果：
任务、版本与模型证据：
尚未完成与阻塞原因：
下一步可直接执行的任务：
```
