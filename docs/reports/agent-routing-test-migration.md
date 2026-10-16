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
