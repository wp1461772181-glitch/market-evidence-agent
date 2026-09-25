# V2 开发进度

最后更新：2026-09-24（P2 小批次验证与 P3 数据审计）。

本文件由开发执行者持续更新；计划正文见 [V2 开发与验收方案](evidence-driven-forecast-v2-plan.md)，启动提示词见 [GPT-6 交接](gpt6-v2-handoff.md)。

## 当前状态

- 已完成：方案编写、基线核对、P0 固定目标与价格口径、P1 持久化任务骨架。P2 已完成动态证据、缓存抽取与研究入口的代码连接，并在官方来源上做了小批次实测；仍未通过全部 P2 验收。
- 基线代码：`a135bf9ca983008b5690dd254358e3aba06e12ae`。
- 本轮启动前：143 项旧代码测试和前端构建通过，工作树干净。
- 本轮已应用追加式 V2 迁移到本机开发数据库；旧表和旧预测语义未改。V2 路由可排队和查询，worker 仅有测试注入的处理器；默认 worker 会明确记录 `v2_processor_not_configured`，不会生成伪预测。
- 未实施：P2 的真实媒体样本及 8-K 附件到冻结上下文的完整接入；P3 的修订行与价格更正回放；P4–P8 的联合模型、前端 V2 流程、调度切换及到期评估。旧证据修订仍保持原数值概率。
- 下一步：补齐 P2 媒体/附件验证，积累足够可核对事件并推进 P4；P1 的任务框架尚未配置真实执行处理器，不能把研究报告或纯行情 artifact 当作联合预测。

| 阶段 | 状态 | 代码/真实验收证据 |
|---|---|---|
| P0 预测合同与价格口径 | verified | 20 个 XNYS 交易日固定目标、绝对收益 ±2% 边界、根子合同/到期校验；固定拆股与分红 fixture；Yahoo 真实 AAPL 2020-08-31 4:1 拆股及无事件区间响应核验。已知跨期拆股阻断。 |
| P1 持久化与统一任务 | verified (skeleton) | 五张 V2 表追加迁移、幂等任务、租约/过期执行者隔离、原子版本发布、分支、API 202/查询、worker fixture；本机 `--check` 显示五表存在。真实处理器待 P2–P4。 |
| P2 动态证据集合 | in_progress | SEC 历史目录按页限量扫描、8-K 同 accession 99.1 附件读取、可恢复的本地语料清单；真实 `--plan` 发现五股 396 条候选，保存 2 份官方快照。冻结事件、双时间边界、特征合同、缓存抽取、正反研究与引文定位已实现；AAPL/GOOGL 真实抽取 2 次，GOOGL 真实研究 1 次且重复抽取命中缓存。媒体样本与附件完整接入未完成。详见 [证据试点记录](v2-evidence-audit.md)。 |
| P3 同口径市场基线 | partial (research_only) | 六份价格快照各 1,421 个会话且哈希验证通过；固定 20 会话纯行情根行共 6,425（train 3,305/calibration 1,140/test 1,980），历史不足/跨分区/跨拆股行明确剔除。首次不可覆盖模型 artifact 已训练且可回放；封存测试 Brier 0.634，比类别先验 0.617 更差，未发布为默认模型。修订行等仍待做。详见 [模型评估](v2-model-evaluation.md)。 |
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

## 2026-09-24：P2 证据试点与 P3 行情审计

- P2 增加 `evidence_context.py`、`evidence_features.py`、`v2_research_bridge.py` 和 SEC 分页/附件读取及受限语料脚本；来源在 V2 事件表中冻结，研究只消费冻结内容与可定位引文。财务同比缺双端引文时保持缺失；媒体未经模型验证时返回 `unsupported_evidence_channel`，星级不直接改概率。
- 真实样本：2 份官方正文，AAPL 分析范围截断；DeepSeek 新抽取 2 次、合计 12,434 tokens；GOOGL 动态研究 1 次成功，重复准备时抽取缓存命中。开发库没有真实手动媒体材料；8-K 附件抓取能力尚未完整接到冻结上下文。细节见 [证据试点记录](v2-evidence-audit.md)。
- P3 只读审计先发现训练分区 2021–2023 的本机价格不足；随后受限脚本从 Yahoo 保存五股和 SPY 的 6 份历史研究快照，quote-close 行为、公司行动、获取时间与 SHA-256 已记录，重新执行只读计划显示全部齐备。严格读入后生成 6,425 条固定目标根行；这一数据准备阶段尚未训练模型。细节见 [行情覆盖审计及补充](v2-market-data-audit.md)和 [脱敏清单摘要](v2-dataset-manifest-summary.json)。

## 2026-09-25：P3 首次纯行情模型

- `training_data_v2.py` 从六份受限快照生成 6,425 条固定目标根行，按日期锁定 train/calibration/test，剔除跨拆股和跨分区标签；训练入口再次核对第 20 个 XNYS 会话、收益和标签合同。
- `training_v2.py` 只用 train 拟合、calibration 独立 sigmoid 校准，生成本地不可覆盖 `market-20260925-market-baseline-01`；哈希加载与单条概率回放通过。产物是 `research_only`，联合概率为空。
- 对封存 test 的首次固定评估：市场 LR Brier 0.633996、log-loss 1.047881，均差于训练类别先验的 0.616585 / 1.019233。没有根据测试结果调参或重跑；不升级为默认预测。详见 [模型评估](v2-model-evaluation.md)。

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
