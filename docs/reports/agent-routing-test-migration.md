# 路由改造测试迁移 / Test migration

基线为 `81f62be32fbcf9dbbd329e34673aed705567da4b`。该版本是本次实际检出的 GitHub main，晚于设计文档引用的 `490a176`。原始测试可从这个 Git 提交恢复；没有修改主工作区的学习者文件。

## 为什么测试数减少

删除 planner、purpose 标签、任务 DAG 和 query-expander 后，依赖这些回调、内部节点名称、旧式 pending proposal 或 `plan_*` 错误码的测试不能原样执行。初次迁移清单 [JSON](agent-routing-test-migration.json) 记录 19 个模块中的 79 个保留函数、396 个被替换函数。它是迁移时的函数级清单，包含随后重写的模块，不是当前 pytest 参数展开后的用例计数。

基线全套曾为 1748 passed / 7 skipped。新套件用例数减少，不能把“现在全绿”解读为逐条保留了原来 1748 项覆盖。旧 planner 的结构约束被删除；写权限、冻结目标、所有者隔离、引用和时间绑定是必须迁移的行为约束。以下是现在实际执行的对应关系。

| 设计变更/保留行为 | 当前验证入口 | 验证边界 |
| --- | --- | --- |
| C1/C2/C6 两阶段、首轮跳过 resolver、删除 planner | `test_agent_direct_workflow.py`, `test_agent_workflow_routing.py`, `test_agent_workflow_graph.py` | 注入独立分类/消解和 native tool decisions；不再断言旧 DAG |
| C3 B 数字声明、C 固定话术 | `test_agent_redesign_behaviors.py`, `test_agent_redesign_contracts.py` | 工具计数为零，模型常识明确标注 |
| C4/C9 实体、期间、指标、数值/引用绑定 | `test_agent_evidence_v2.py`, `test_agent_redesign_contracts.py` | 跨公司、跨季度、跨年度、金额及有限事件极性反例；不是通用语义蕴含证明 |
| C5/C9 确认令牌、参数冻结、所有者/对话/过期、并发与重放 | `test_agent_consent_v2.py`, `test_agent_redesign_behaviors.py` | 真实临时 PostgreSQL；并发确认只有一次业务效果；崩溃状态不自动重试 |
| C7 类型/范围错误可纠正、旧客户端兼容 | `test_agent_tool_catalog.py`, `test_agent_chat_stream.py`, `test_agent_redesign_behaviors.py` | 目录全覆盖，缺失声明不可注册；非法参数在 I/O 前拒绝 |
| C8 所有者队列/游客本地队列、页面动作 | `test_agent_redesign_behaviors.py`, `frontend/scripts/agent-client-actions.test.mjs` | 所有者实际写临时数据库；游客事件不写库；动作重放及 storage 不可用时仍去重 |
| C10 公司表不作为网页闸门依据 | `test_company_directory.py`, `test_agent_web_research.py` | 正文实体整词检查和任意公司；公司展示表保留 |
| C11 三类停止、有限重试、保留部分证据 | `test_agent_redesign_contracts.py`, `test_agent_redesign_behaviors.py` | 分开验证 too_many_steps / tool_failures / no_progress，DSML、参数修复、失败正文不泄漏 |
| C12 上下文与持久化 | `test_agent_context*.py`, `test_agent_workflow_persistence.py`, `test_agent_protocol_roundtrip.py`, `test_agent_answer_language.py` | PostgreSQL 重新打开；他人无状态；运行时 key 不入 checkpoint；简体正文保留原标题 |
| C13 工具权限、现有业务服务不回归 | `test_agent_tool_catalog.py`, `test_public_api_access.py`, 原有材料/预测/知识/监控套件 | 真实权限及原有服务测试；模型评测不执行真实付费业务 |
| C14 网页冻结、去重、原始网页 provenance | `test_agent_web_ingestion.py`, `test_agent_redesign_behaviors.py`, `test_agent_citation_restore_v2.py` | 确认后不重新抓取；真实快照与私有文档；同步/索引/检索/历史恢复；外部向量服务使用受控存储 |
| 前端确认卡的实际展示 | `frontend/scripts/chat-clarification.test.mjs` | 两个日期、URL、来源目标、替代选项和 server card ID 不丢失 |

## 模型用例与端到端边界

[50 条 JSONL](../../scripts/evaluation/cases/agent-routing-v3.jsonl) 含设计要求的全部 33 个场景。真实模型负责指代、分类和工具选择；公开网页和既存材料由可控合成资料提供，业务副作用被禁止。R21、R23、R28 的工具循环分别注入预算、重复及非法参数故障，不计入真实模型选择准确率和延迟分母。它们仍必须独立检查停止原因或修复结果。

模型选对工具不证明真实服务可用；相应的 PostgreSQL、HTTP/native provider、同步/检索和浏览器动作测试各自验证服务边界。线上 SEC/Tavily/Milvus、UI 视觉验收与发布冒烟只能由发布记录证明。

当前全套结果、失败记录和未完成的发布门槛统一见 [验收报告](agent-routing-verification.md)。


2026-10-16 补充：DeepSeek-Flash 官方接口的思考强度配置现在与 OpenRouter 分别使用各自原生参数；`test_agent_chat_provider.py` 原来“DeepSeek 不传参数”的断言已按官方 API 合约更新。CLI-only 元数据发现也移除了内置公司映射，旧 probe 测试改为显式提供合成 DiscoveryScope，新增默认 ticker 查询/未配置 RSS 的契约和全 app AST 防漂移检查。这些是 C7/C10/C11 的适配变化，不放宽来源、归属或确认权限。

## V4 reference/grant repair (base c9a75bc)

No behavior coverage was deleted; two catalogue test functions were renamed from costly to server_write to broaden the tokenless probes. Baseline for this repair was 1270 passed / 7 skipped. The earlier planner migration counts above describe the earlier redesign, not this repair.

| Changed test | Reason and preserved behavior |
| --- | --- |
| acceptance/chat_stream/parent_model_profiles native fixtures | Classifier metadata no longer contains an answer; a separate native answer call follows B classification. Expected first-turn B model calls change from one to two. |
| workflow_persistence | Answer supplied through independent answerer; reopen/isolation/private-runtime-key assertions remain. |
| redesign_behaviors | Explicit granted-action fixture replaces positive verb-whitelist authorization; denial/negative tests grant the relevant tool where needed so they still exercise the intended guard. Native entity interpretations include original company mention or literal ticker and controlled official issuer metadata. |
| tool_catalog | Default owner schemas exclude ungranted writes; catalogue confirmation covers reversible and costly server writes. Tokenless adapter probes now include all server writes. |
| owner queue behavior | Public invocation denies server mutation; actual owner add/remove passes through real temporary PostgreSQL consent and its execution handler. Guest browser queue remains immediate and leaves no server queue row. |
| frontend chat clarification | Additional agent_suggestion boolean preserves only strict true; normal server button values remain intact. Existing confirm/change/cancel/reply protocol remains. |

Original `agent-routing-v3.jsonl` stays byte-for-byte unchanged. `agent-routing-v3-confirmed.jsonl` changes ONLY R16/R27 expectations, by explicit user decision: a server confirmation card containing the queue operation replaces immediate owner mutation. All other case inputs and expectations are identical. The evaluator checks the frozen operation name and target count, rather than pretending a preconfirmation adapter call occurred. Compare these migrated results with the historical v3 baseline transparently; they are not identical old acceptance semantics.

New v4 tests cover missing/invalid B generation and one retry, copied/forged source references, B/C scope exemption, official issuer mapping including shared-name-prefix counterexample, unknown action filtering, hidden-tool forgery, negative veto, guest exclusions, typed suggestions, replay/concurrency, cancellation, frozen second-stage web consent and accurate failed/no-write outcomes. Deterministic probes and live model trials have separate denominators in the new report.

User-approved V4 follow-up clarification: Reference now additionally accepts server-derived `assistant_message:<id>` sources from the current conversation. `text` remains an exact normalized substring of that identified message; a forged ID or a quote found only in another supplied history is rejected. The `mention` field describes what was resolved; it is not an extra exact quote of the newest user message. Older assistant words still cannot supply facts or write grants. V02 continues the actual saved V01 conversations so setup answers are not regenerated or canned.

The exact changed/new/renamed function inventory for this repair is [v4 migration JSON](agent-routing-v4-test-migration.json). Function counts differ from parametrized pytest case counts; the final source suite has 1316 passing cases versus baseline1270, with7 skips retained.

## V5 approved policy migration (base1280881)

当前验收见[V5报告](agent-routing-v5-verification.md)，逐函数清单见[V5迁移JSON](agent-routing-v5-test-migration.json)。上面的1316通过是历史V4提交结果。

- 固定token/步骤/总时长用例改为：超过旧累计值仍计量和继续、15个正常读调用完成、持续流续期、空心跳/仅role帧不续期、取消释放连接；停滞仍保留部分证据。
- 正常引用/金额/事件反例保留，新增逐段保留、缺失引用、裸数字、确定性来源计算与无效算式。只有旧股价每句关键词限制取消。
- 敏感词“不进模型”的预期改为可进入分类，仍无密钥/删除能力与无授权写；产品配置帮助独立验证。运行时key不得入持久化的原断言保留。
- 数量不足改为显式部分确认卡；错误序号、零数量、多提目标、冻结实际目标与确认重放仍拒绝/保持幂等。SEC缺目录的下一步改为发现失败可重试，不要求用户手动先扫描。
- 矛盾refinement用例改为明确任务切换可继续、仅时间调整恢复旧字段、否定/元语言提及不能改成价格任务。公司映射仍使用受控官方元数据。
- 原无引用占位输出`first second`改为带真实fixture金额及引用的回答，所有权/事件顺序/会话持久化/运行时key不落盘断言不变。
- 原v3及v3-confirmed语料未改。新增v5语料仅迁移R21/R24，控制器探针仍单独计量；未执行新的付费试验。
