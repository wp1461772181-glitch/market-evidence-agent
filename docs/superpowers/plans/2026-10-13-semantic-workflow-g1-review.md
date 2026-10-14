# G1 评审（Opus 5.5，只读）与处理记录

评审对象：`tests/test_agent_semantic_regressions.py`（首版 14 RED + 3 GUARD）。结论：14 条红灯都因文档所述原因失败，没有测试绕开语义图；需要在 S4 前加固测试。以下为逐条处理。

| 评审意见 | 处理 |
|---|---|
| GUARD "模型文本不能授权写" 写死了拒绝路径（`implicit_write_rejected`），缺陷 6 修好后会因安全原因变红 | 已改：只断言安全性质（未执行、终态非 succeeded/running、pending 不带动作），canonical 去掉凭空的 AAPL，`new`/`slot_reply` 各跑一遍 |
| 缺陷 3 的测试拦不住"用模型 canonical 决定动作"的错误修复（评审用错误修复模拟，测试仍转绿，且能把 import 改成 analyze 执行） | 已改：续接测试两种 canonical（裸 UUID / 改写成"生成 AI 分析"）都必须执行 `import` 且 symbol 来自 pending |
| 缺陷 3：`execute_action` 只查来源存在，不查属于哪只股票（先说导入 AAPL、回 NVDA 的 UUID 也会导入） | 新增 DB 红灯并**已修**（`source_symbol_mismatch`） |
| 依赖测试按位置比状态、注入了 answerer 跳过了"缺失范围"说明 | 已改：按 `task_id` 比，monkeypatch `stream_answer` 并断言 blocked 范围与错误码出现在说明里 |
| 财报测试 `== [tool]` 只允许一次调用，会卡死两步式修复 | 已改：用"仿库"执行器（过滤、新到旧、8-K 比 10-Q 新）并断言**送到回答里的证据来源**是最新 10-Q，不再断言调用次数/参数 |
| 扩写测试只比较 symbol | 已改：除 keyword 外所有参数必须一致 |
| scope 测试失败信息看不出原因 | 已改：先断言 `gate_reason != plan_schema_invalid` |
| 阶段识别依赖 `prompt == STAGE_PROMPTS[...]` | 已改：按 payload 键识别阶段，planner 提示词断言改用 planner 调用**实际发出**的 system 消息 |
| 缺陷 7 正例单独存在时会让"直接信任 planner scope"的不安全实现通过 | 已补 3 条 NET 反例（虚构 accession、未知键、两家公司 accession 对调，后者用已保存清单判归属）。它们现在因 schema 不支持而通过，S4 接受 `scope` 后必须仍通过 |
| 缺失红灯：依赖链传递、父任务过不了证据闸、blocked 审计 | 已补并已由 S2 转绿 |
| 缺失：重复 UUID 只执行一次、非法槽位不续接、reference_* 类 pending 不得写、检查点重开/会话隔离 | 已补（含 PostgreSQL 检查点用例） |
| 缺失：代词补错公司、assistant 文本不算依据、"announcement"含"no"不算否定、8-K 不得强加 financial_only、10-Q 传 form、扩写超预算收缩、变体全被拒、合并去重、新闻词被删 | 已补（GUARD 或 RED，见回归文件） |
| 未采纳 | "没有 AI 分析时 fallback 读同一 source 原文"改为不规定 fallback 形态，只规定"引用来源不得越过最新财报"（`test_missing_analysis_for_the_latest_report_never_borrows_a_newer_8k_analysis`） |

## 评审给 S4 的约束（已写入代码地图 S4 待办）

- 新增内容的依据：只认用户发言和服务端生成的 pending/结果集 scope 里的符号；不认结果集标题（网页来源），不认 assistant 文本。
- 动作与符号只能来自 pending；模型文本不得选择或改变写操作。
- `scope` 值必须出自用户原话或所选结果集，归属以已保存清单为准。
