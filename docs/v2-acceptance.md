# V2 验收快照（P8 进行中，非最终签收）

生成时间：2026-09-26T02:41:17Z（Asia/Kuala_Lumpur 为 2026-09-26T10:41:17+08:00）。本文件按 [V2 开发与验收方案](evidence-driven-forecast-v2-plan.md) 的 A01–A20 逐项记录当前可见证据，不把单元测试、历史回填和真实前向结果混为一谈。

状态含义：`live` 是实际本机/外部服务记录；`fixture` 是受控自动化验证；`partial` 表示只满足部分必要条件；`not accepted` 表示尚无足够证据。没有列出的证据不应被推定为已经验收。

## 本次快照的可复核基线

- 已提交代码基线：`7f0126cf762d38f8eb2e472a0a77fe08df128bb0`（`7f0126c`）。生成快照时工作树含并行中的未提交改动；本文件不能把它们视作已提交或已推送交付。
- 本机迁移检查：`./.venv/bin/python scripts/migrate_forecast_v2.py --check` 返回五张 V2 表均存在，`missing=[]`、`missing_columns=[]`。
- 本次运行：`./.venv/bin/pytest -q` 为 **293 passed**；`cd frontend && npm run build` 成功；`git diff --check` 成功。警告没有作为测试失败处理，详情由命令输出保留在执行环境中。
- 市场基线 artifact：`artifacts/v2/market-20260925-market-baseline-01/market_model.joblib` SHA-256 为 `8d78143290b4b0f09714385516a1b8d88f2aa72eaae33a8833f97c4efa839779`；`manifest.json` SHA-256 为 `75e66201b8b1dff85e8580bc495e88a4b46a357307b2c66fcd8ca711beee64f0`。数据清单和训练行哈希分别见 [模型评估](v2-model-evaluation.md)；该 artifact 是 `historical_research`、`research_only`，不是已发布模型。
- 当前真实运行的可定位记录：worker 曾消费任务 `372eb633-08ec-4e33-9813-b51991ec8ad0` 并产生研究版 `1e0572e8-bd57-4952-b195-7dee12c34dde`；它的目标日为 2026-10-22，baseline/joint 概率均为空。四轮已记录的 SEC 扫描均无新公告，详见 [进度记录](v2-progress.md)。
- 本机 `.env` 与 `logs/` 均被 Git 忽略规则覆盖；本次没有执行可能泄露内容的全仓密钥扫描，因此这只能证明忽略边界存在，不能替代专用密钥审计。

## A01–A20 矩阵

| ID | 当前结论 | 可复核证据与缺口 |
|---|---|---|
| A01 | `partial` | `tests/test_forecast_v2_processor.py::test_real_input_processor_freezes_observed_sources_and_leaves_probabilities_empty` 和真实 AAPL 任务证明冻结行情/证据与研究版目标合同可保存。P4 未完成，`joint_probabilities=null`，所以“联合概率有合法模型出处”未满足。 |
| A02 | `partial` | `tests/test_v2_research_bridge.py::test_context_event_extraction_reuses_cache_and_keeps_media_coverage_visible` 覆盖相同冻结输入的抽取缓存；GOOGL 小批研究也有缓存命中记录。尚未以真实“无新财报但行情变化”的新根任务完整验收。 |
| A03 | `fixture` | `tests/test_evidence_context_v2.py::test_review_change_appends_event_version_and_incremental_context_keeps_background` 覆盖旧背景继承。没有真实风险事件在后续预测中持续保留的前向案例。 |
| A04 | `fixture` | `tests/test_official_monitor_v2.py::test_persisted_monitor_queues_exact_72_hour_fetched_source_once` 覆盖新官方材料在窗口内落地入队。真实定时扫描尚未发现新公告，因此没有真实自动修订链。 |
| A05 | `fixture` | 同一监控 72 小时测试与 `tests/test_official_monitor_v2.py::test_monitor_rejects_old_or_expired_roots` 覆盖 UTC 边界/过期根。没有真实边界时刻扫描。 |
| A06 | `fixture` | 根合同不可变由 `tests/test_forecast_contract_v2.py::test_fixed_target_revision_keeps_root_target_and_expires_at_end_session` 与处理器手动修订测试覆盖。尚未有真实子版后再经过 72 小时的自动触发案例。 |
| A07 | `fixture` | `test_evidence_context_v2.py::test_duplicate_media_content_is_deterministically_one_event_and_cross_symbol_prior_is_rejected` 与 monitor 同轮合并测试覆盖去重。多次转载的真实媒体链尚未验收。 |
| A08 | `partial` | 上传/星级不改概率由 `tests/test_evidence_features_v2.py::test_media_channel_is_explicitly_unsupported_until_its_training_path_exists` 和媒体桥接测试覆盖；NVDA 测试上传、冻结与真实研究引文已有记录，仍为 `unconfirmed/pending_review`。不是五星真实新闻，也没有媒体数值通道。 |
| A09 | `fixture` | `tests/test_forecast_v2_processor.py::test_manual_revision_inherits_parent_and_appends_new_observed_source` 验证 parent、目标与旧版不变。真实“较老未到期版本 + 新材料”的手动修订尚未发生。 |
| A10 | `fixture` | `tests/test_forecast_api_v2.py::test_revision_rejects_missing_version_and_expired_target` 覆盖 API 拒绝。页面的复盘入口未做当前浏览器验收。 |
| A11 | `fixture` | `test_v2_research_bridge.py::test_context_event_extraction_rejects_bad_event_quote_without_passing_source_to_research` 与 evidence context 的 future-source 测试均失败关闭。真实媒体试点的研究引文也实际匹配冻结正文。 |
| A12 | `fixture` | `tests/test_forecast_jobs_v2.py` 覆盖幂等、并发、租约恢复；`test_official_monitor_v2.py::test_concurrent_monitor_lock_records_a_visible_non_scanning_run` 覆盖监控锁。尚无真实网络失败后的端到端恢复演练。 |
| A13 | `fixture` | `tests/test_forecast_contract_v2.py::test_split_or_unknown_price_basis_blocks_contract_and_cash_dividend_is_retained_as_warning` 及 P7 的 split/缺行动/锚点测试覆盖阻断。真实未来目标尚未成熟，不能称前向拆股处理验收。 |
| A14 | `not accepted` | P4 联合模型尚未完成，没有可验证的“同行情、不同有效证据”联合概率响应或封存敏感性结果。 |
| A15 | `live + fixture` | 真实 AAPL 研究版和处理器/API fixture 均保持 joint 概率为空；P3 artifact 的封存 Brier 0.633996 劣于先验 0.616585，未被发布。该项只验证了失败时不伪造联合概率。 |
| A16 | `partial` | P7 的 pending、晚到价格、价格更正、拆股与历史 cutoff 测试已覆盖；真实 AAPL 2026-10-22 前向目标仍 pending，尚无成熟标签或同根多版本真实评分。 |
| A17 | `not accepted` | 本次生产构建成功，但独立隐藏浏览器不可用：本机 IAB 返回 `Browser is not available: iab`，仅发现含用户现有页面的 Chrome extension browser。为不扰动用户页面，没有打开可见 Chrome tab；因此桌面、411px 窄屏、切股票和刷新恢复均待浏览器验收。 |
| A18 | `partial` | README 与 V2 工作台代码保留 legacy 区域，且 V2 读模型明确区分研究版；本次无法完成独立 UI 验收，尚未以浏览器确认旧 Week 4/8/9 页面语义和隔离展示。 |
| A19 | `partial` | `test_evidence_features_v2.py::test_media_channel_is_explicitly_unsupported_until_its_training_path_exists` 明确阻断媒体数值融合；真实官方/媒体研究试点均不产生联合概率。尚未有渠道各自的联合模型 artifact 与验收分桶。 |
| A20 | `partial` | monitor 代码/fixture 保存 incomplete 与水位线，P6 实际四轮扫描也没有把“无新公告”写成完整全历史覆盖。尚未得到一次真实超出 `max_filings`、无分页游标的补扫恢复完成案例。 |

## 三个必须分开的结论

1. **工程闭环：部分完成。** V2 的持久化任务、研究版处理、手动修订入口、每小时 SEC 扫描、独立评估记录和读接口均有代码与多项 fixture；真实新公告自动修订、真实新材料手动修订、完整补扫以及独立浏览器验收仍未完成。
2. **官方/媒体联合模型：未达标。** P4 没有可发布的联合 artifact；P3 纯行情 artifact 也未通过先验比较。所有当前真实 V2 版本都只能标为 `research_only`，不能显示或推断为验证过的涨跌概率。
3. **真实前向效果：尚无样本。** 已有的来源研究和行情训练包含历史研究材料；真实 AAPL 目标尚未到期。没有成熟的前向预测可用于判断准确率、证据增益或修订是否改善结果。

## P8 收口前仍需补齐

- 获得并保留真实新官方公告触发的自动修订，以及真实新材料触发的手动修订证据。
- 完成 P4 的渠道分开数据、封存评估、消融/时间打乱和发布门槛；未达标则继续维持 `research_only`。
- 在成熟目标日安全刷新 Yahoo 行情、复核公司行动，并积累真实前向评估样本。
- 在不影响用户现有页面的独立浏览器可用时，补做桌面和 411px 窄屏核验、截图和 A17/A18 的最终状态。
