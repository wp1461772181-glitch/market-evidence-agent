# 语义工作流修复：代码地图与进度账本

更新：2026-10-14（G3 + Sol 审计处理）。草稿 worktree：`/tmp/market-workflow-20261012/worktree`（基线 `8cfbd5d`）。
稳定快照（不含运行数据/密钥）：`~/Documents/Market-Evidence-Agent-semantic-draft-snapshot-20261013/`。
先读：`2026-10-13-semantic-workflow-handoff.md`（缺陷原文）。本文件只补"在哪、怎么验、做到哪了"。

## 进度账本

| 段 | 内容 | 模型 | 状态 |
|---|---|---|---|
| S0 | 基线、快照、本地图 | Sonnet | 完成：聚焦 3 文件 63 passed |
| S1 | 红灯回归测试 `tests/test_agent_semantic_regressions.py` | Sonnet | 完成 |
| G1 | 审测试（只读） | **Opus** | 完成：结论见 `2026-10-13-semantic-workflow-g1-review.md`；意见已落实到测试（40 例，含 DB 与检查点用例） |
| S2 | P0：planner 提示词/枚举/示例；任务依赖强制（`blocked`） | Sonnet | **完成**：5 个红灯转绿 |
| S3 | P1 机械项：财报参数透传（`financial_only`/`limit`）、期间不静默丢失、扩写仅知识任务+真多路检索+合并去重+预算收缩；`execute_action` 来源↔股票校验 | Sonnet | **完成**：8 个红灯转绿 |
| S4 | P1 设计与安全项（剩余 12 例红灯 + S4 自补 23 例） | **Opus** | **完成**：12 条转绿；自补 23 例（14 RED 转绿、5 GUARD、4 NET）；16 个变异探针全部被抓到。见下文"S4 结果" |
| S5 | 全量测试 + 前端 + 真实 DeepSeek/Tavily 验收（9 条） | Sonnet | **完成，验收有未通过项**：后端 `902 passed, 7 skipped`、回归文件 82 例、前端 i18n 71 passed + build；真实模型 31 个聊天轮次、3 轮修复（5 个根因，各先写红灯）；仍未通过：场景 1 的"营收"原话（预先存在的证据门，需决策）；场景 7 NOT VERIFIED LIVE（需 RAG）。见"S5 结果" |
| S6 | S5 遗留项：网页行相关性摘录（营收原话）、语义阶段对瞬时提供商失败重试一次、价格行带 symbol；真实验收重跑；验收中发现并修一处解析器提示词问题 | Sonnet | **完成**：后端 `935 passed, 7 skipped`、回归文件 95 例、网页工具文件 41 例；真实 9 个聊天轮次（上限 10）：(a) 原话 PASS、(c) PASS、(b) 机制 PASS 但答案措辞有遗留（见"S6 结果"）；未改证据门。见"S6 结果" |
| G2 | 独立评审最终 diff 与验收证据（只读） | **Opus** | 评审已出（`/tmp/market-g2/g2-review.md`：阻断 2、应修 7、可选 2）；第一批处理见 G2-A，其余见"G2 处理 A"末尾的"未处理" |
| G2-A | 评审第一批：阻断 1（planner 可读任意 URL）、阻断 2（写操作对象来自模型）及 `requested_action` 子串匹配、第 9 项（general 无证据回答）；第 11 项仅文档 | Sonnet | **完成**：回归文件 214 例（+119，其中 56 个在修复前失败）、全量后端 `1054 passed, 7 skipped`；前端 i18n 71 passed + build；55 个变异探针全部被抓到；评审 3–8、10 未处理。见"G2 处理 A" |
| G2-B1 | 评审第二批：3（planner 查询丢期间/数字/否定）、4（报告类型与最新限定）、5（跟进选错集合、复用不过闸）、7（重试计费）、8（摘录选中缺主语的指标句）、10（对比句拆分）+ 4 条小项（回退文案、URL 判断、原因码、`truncated`） | Sonnet | **完成**：回归文件 338 例（+124）、`tests/test_agent_web_research.py` 52 例（+11）、全量后端 `1189 passed, 7 skipped`；其中 95 个断言在改前的代码上失败（77 个新红灯 + 18 个有意改过期望的既有断言）；前端 i18n 71 passed + build；93 个变异探针无存活（2 个等价变体已说明）；评审 6、Q1–Q3 留给 B-2。见"G2 处理 B1" |
| G2-B2 | 评审第 6 项与 Q1/Q3（canonical 自由文本不进入后续阶段、服务端核实的位置）、B1 观察 (a)(b)（总结之后的任务、关键词任务的保留规则）、真实模型复核 | Sonnet | **完成，真实复核有 1 项未能在线重验**：回归文件 517 例（+179）、`tests/test_agent_web_research.py` 52 例、全量后端 `1368 passed, 7 skipped`；前端 i18n 71 passed + build；变异探针 81 个，全部被抓（首轮 75 个被抓；6 个存活——`前0条` 与正常位置并存、点击选项被指向另一个集合、错误路径丢掉原问题、关键词保留读整句、关键词保留读续接后的问题、写放宽用于任何原话——补 6 个用例后重跑全被抓；更早两轮不完整的探针里另有 5 个存活，已在最终轮之前补测试：句子大小写、NFKC、URL 里的序数、`前0条`、位置排序去重）（另有 6 个回退探针证明新增红灯确实在改前失败）；真实 DeepSeek/Tavily 12 个聊天轮次：1、2a–2c、3、5a、5b（修复后）、6 通过，4b 在被测构建上失败（已修并有单测，未在线重验）。见"G2 处理 B2"与"发布前状态" |
| Round C | B2 之后的线上缺陷：复用答案把"位置"写成引用编号（`[2]`，而该轮只有 1 号引用卡）；复用证据消息补一句固定英文（用各自摘录开头的方括号数字、位置不是引用编号）、行字段改名、audit 增加 `unmatched_citation_markers` | Sonnet | **完成，真实复核通过**：回归文件 546 例（+29，其中 26 例在改前失败，另有 3 个既有测试的行字段键名随改名更新）、`tests/test_agent_chat_stream.py` 13 例（+1）、`tests/test_agent_web_research.py` 52 例、全量后端 `1398 passed, 7 skipped`；前端未改动，i18n 71 passed + build 通过；`git diff --check` 干净；`deterministic_confidence_gate`、`bounded_tool_result` 与基线逐字相同；变异探针 34 个（新增句子与辅助函数各自被关掉都变红），最终代码上全部被抓；真实 DeepSeek/Tavily 3 个聊天轮次（上限 4）：第 3 轮答案的 13 个内联 `[1]` 全部对应唯一的 1 号引用卡，`unmatched_citation_markers=[]`。见"Round C" |

| G3 | 用户 2026-10-14 四点决定（放宽代词规则、写操作指代、澄清标题按缺失信息、保留测试会话）+ Codex 对比审查（`2026-10-14-codex-comparison-review.zh-CN.md`）里属于本草稿的缺陷 | Sonnet | **完成**：见下文"G3"一节；实时模型未重跑，审计与测试交 GPT-6.1-Sol（提示词 `2026-10-14-sol-audit-prompt.zh-CN.md`） |

| G3-Sol | 独立审计报告（`/tmp/market-g3/sol-audit-report.md`，结论"不可发布"：5 阻断、7 应修）的集中处理 | Sonnet | **完成**：阻断 B1–B5 与应修 R1–R6 已修，R7 与 3 个可选项未做（见下文"G3-Sol"一节）；发布交 GPT |

**结果（S4 后）**：回归文件 63 例全绿；全量后端 `880 passed, 7 skipped, 0 failed`（`PYTHONPATH=. .venv/bin/python -m pytest -q`）。前端未改动、未重跑；真实模型验收未做（S5）。

**当前结果（S5 后）**：回归文件 82 例、全量后端 `902 passed, 7 skipped, 0 failed`；前端 `npm run test:i18n` 71 passed、`npm run build` 通过（>500 kB 警告为既有）；`git diff --check` 干净。真实 DeepSeek/Tavily 验收的结论只看下面"S5 结果"，不要与测试通过混为一谈。

**当前结果（S6 后）**：回归文件 95 例、`tests/test_agent_web_research.py` 41 例、全量后端 `935 passed, 7 skipped, 0 failed`；前端未改动，`npm run test:i18n` 71 passed、`npm run build` 通过；`git diff --check` 干净。真实模型结论只看下面"S6 结果"。

**当前结果（G2-B2 后）**：回归文件 517 例、`tests/test_agent_web_research.py` 52 例、全量后端 `1368 passed, 7 skipped, 0 failed`；前端未改动，`npm run test:i18n` 71 passed、`npm run build` 通过；`git diff --check` 干净；`deterministic_confidence_gate`、`bounded_tool_result` 与基线逐字相同。真实模型结论只看"G2 处理 B2"的 C5 表与"发布前状态"，不要与测试通过混为一谈。

**当前结果（G3-Sol 后）**：新增 `tests/test_agent_semantic_audit_fixes.py` 54 例；全量后端 `1549 passed, 7 skipped, 0 failed`；前端 `npm run test:i18n` 72 passed；`git diff --check` 干净；`deterministic_confidence_gate`、`bounded_tool_result` 与基线逐字相同；主目录 56 个受保护文件哈希与 `main-dirty-hashes.json` 一致。

**当前结果（G3 后）**：回归文件 542 例、新增 `tests/test_agent_semantic_deixis.py` 72 例与 `tests/test_agent_semantic_review_fixes.py` 29 例、`tests/test_agent_web_research.py` 52 例、`tests/test_agent_chat_stream.py` 13 例、全量后端 `1495 passed, 7 skipped, 0 failed`；前端 `npm run test:i18n` 72 passed（+1 标题用例）、`npm run build` 通过；`git diff --check` 干净；`deterministic_confidence_gate`、`bounded_tool_result` 与基线逐字相同；主目录源码文件未动。

**当前结果（Round C 后）**：回归文件 546 例、`tests/test_agent_chat_stream.py` 13 例、`tests/test_agent_web_research.py` 52 例、全量后端 `1398 passed, 7 skipped, 0 failed`；前端未改动，`npm run test:i18n` 71 passed、`npm run build` 通过；`git diff --check` 干净；`deterministic_confidence_gate`、`bounded_tool_result` 与基线逐字相同。真实模型结论只看"Round C"的"真实复核"。

**当前结果（G2-B1 后）**：回归文件 338 例、`tests/test_agent_web_research.py` 52 例、全量后端 `1189 passed, 7 skipped, 0 failed`；前端未改动，`npm run test:i18n` 71 passed、`npm run build` 通过；`git diff --check` 干净；`deterministic_confidence_gate`、`bounded_tool_result` 与基线逐字相同。无真实模型/网络调用，无影子服务。

**当前结果（G2-A 后）**：回归文件 214 例、`tests/test_agent_web_research.py` 41 例、全量后端 `1054 passed, 7 skipped, 0 failed`（`PYTHONPATH=. .venv/bin/python -m pytest -q`）；前端未改动，`npm run test:i18n` 71 passed、`npm run build` 通过；`git diff --check` 干净；`deterministic_confidence_gate`、`bounded_tool_result` 与基线逐字相同。无真实模型/网络调用，无影子服务。

## 怎么跑

```sh
cd /tmp/market-workflow-20261012/worktree
PYTHONPATH=. .venv/bin/python -m pytest -q --tb=short -p no:cacheprovider -W ignore tests/test_agent_semantic_regressions.py
PYTHONPATH=. .venv/bin/python -m pytest -q -p no:cacheprovider -W ignore tests/test_agent_semantic_workflow.py tests/test_agent_workflow_execution.py tests/test_agent_chat_stream.py   # 基线 63 passed
```
全量与前端命令见 handoff 文档 §7。不要用 `grep` 直接扫 `frontend/src/AgentChatPanel.tsx`（单行极长）。

## 缺陷 → 位置 → 红灯测试 → 修复方向

| # | 缺陷 | 当前位置 | 红灯测试（regressions 文件） | 修复方向 |
|---|---|---|---|---|
| 1 P0 | planner 提示词没给 purpose 枚举，真模型写自然语言 | `semantic.py:22` `STAGE_PROMPTS["planner"]`；校验 `semantic.py:190` | `test_planner_prompt_gives_a_live_model_the_closed_purpose_set`、`test_planner_prompt_lists_every_legal_purpose_and_one_valid_example` | 由 `PURPOSE_TOOLS` 生成提示词：合法标签、工具↔标签对应、一个能过 `validate_plan` 的 JSON 示例；不放宽校验 |
| 2 P0 | 普通任务不检查依赖，父失败子仍执行 | `graph.py:649` `_retrieve_knowledge`（只有 summarize 分支读 `depends_on`，654–698） | `test_failed_parent_blocks_dependent_task_but_not_independent_sibling` | 每个任务执行前查父状态；失败→子任务记 `status="blocked"`；独立兄弟照常，终态 `partially_succeeded` |
| 3 P1 | pending 的 import 之后回 UUID 丢动作 | `graph.py:238–268` 语义分支清掉 `resume_slots/pending_request`；`graph.py:506–510` 只看 `original_question`；`graph.py:1119–1125` pending 不存 `selected_action` | `test_pending_import_resumes_when_the_user_replies_with_a_source_id`（GUARD：`test_pending_action_is_not_resumed_by_an_unrelated_question`、`test_model_text_cannot_authorize_a_write_for_a_bare_source_id`） | 服务端 pending 记录用户自己的显式动作；回复是合法槽位值才续接；动作权限来自 pending 而非模型文本；保留 owner/幂等；**S4 已修** |
| 4 P1 | 财报分析/原文只传 symbol | `graph.py:566–574` 语义分支 args 构造（对照旧 `_required` 425–486） | `test_latest_financial_report_requests_keep_report_type_and_latest`（2 例） | 透传 `financial_only`、latest→`limit=1`、form/accession/source；读取与 fallback 绑定同一来源 |
| 5 P1 | 扩写 OR 拼接并截 80 字符；web 也扩写 | `graph.py:699–729`（OR 拼接 717–719）；`MaterialArgs.keyword` 上限 80（`agent_tools.py:100`） | `test_knowledge_expansion_runs_each_variant_with_identical_filters`、`test_web_search_is_not_query_expanded` | 仅知识任务；原问题+≤2 变体各自一次检索、相同冻结过滤、合并去重；web 不扩写 |
| 6 P1 | 指代校验只防删除，不防新增 | `semantic.py:139–153` `validate_reference` | `test_reference_resolution_cannot_add_unsupported_scope`（3 例）（GUARD：`test_pronoun_completion_from_recent_dialog_stays_allowed`） | 新增的公司/期间/否定词必须能在 recent、pending、catalog、focus 中找到依据，否则 clarify；**S4 已修** |
| 7 P1 | planner 无法表达任务级 source/form/period | `semantic.py:183` 严格键集；`graph.py:570–577` 复制全局 slots | `test_planner_scope_is_local_to_each_task`（2 例） | 任务增加可选 `scope`（source_id/accession_number/form/period）；值必须出自用户文本或所选结果集；不得用全局第一个值覆盖；**S4 已修** |
| 9 P1（G1 复核时发现） | 期间（如 `2025Q4`）不是任何工具参数，语义路径静默丢掉，返回最新一份文件并标成功（旧路径会要求 accession） | `graph.py` `_route_intent` 语义分支 | `test_period_without_accession_clarifies_instead_of_dropping_the_period` | **S3 已修**：clarify `period_requires_accession` |
| 8 P1（本次新发现） | 一个任务的主题词误伤独立 web 任务（"苹果**最新**股价和英伟达新闻" → `plan_topic_changed`，整轮失败；去掉"最新"即成功） | `graph.py:557` `validate_task_query(state["question"], task["query"])` 拿整句比每个 web 任务 | `test_topic_word_of_one_task_does_not_reject_an_independent_web_task` | 主题/新鲜度校验改为任务级（用任务自己的子句/scope），或缺词时确定性补入；不能让别的任务的词否决它；**S4 已修** |

## S4 结果（契约 → 实现 → 测试）

| 契约 | 实现（位置） | 测试 |
|---|---|---|
| pending 续接：动作与公司只来自用户自己的请求；模型文本不能选择或改变写操作 | `_clarify`（语义轮）把**用户原话**、`requested_action(原话)`、已校验的公司、`owner/conversation`、`expires_at`（30 分钟，`PENDING_ACTION_TTL`）存进 pending；`_resolve_reference` 在"本人本会话、未过期、动作类 pending、`is_pending_reply` 成立、回复没换公司"时**不调用 resolver 模型**直接续接（`_resumed_action`）；`_classify_intent`/`_route_intent` 直接用 pending 的动作和槽位，跳过 intent/planner；幂等键 = 会话+pending id；每轮清 pending | 原 4 条红灯转绿；自补 `..._uses_the_source_id_the_user_typed`、`..._of_another_conversation_...`、`..._naming_another_company_...`、`test_expired_pending_action_is_not_resumed`、`test_guest_cannot_complete_a_pending_import`（真实 `execute_action` + PostgreSQL：`owner_login_required`，无台账行） |
| 新增内容要有依据：只认用户发言、服务端 pending、结果集 scope | `validate_reference(..., context=用户近期发言, pending=当前有效 pending)`：canonical 新增的公司、source id、accession、期间（`period_terms` 归一，中英一致）、URL 必须在依据里出现，新增否定词要求用户发言里有否定；结果集**标题**和 assistant 文本不算；无依据 → `reference_scope_changed`/`reference_negation_changed` → 专门的澄清文案（不给来源选项），不调用工具 | 原 5 条红灯转绿；自补 `test_model_cannot_add_a_source_id_to_a_write_request`（**发现的真实漏洞**：模型给导入请求补上 source id 就会被执行）、`..._accession_to_a_read_request`、`..._url_for_the_web_tool_to_read`（被注入的模型可让 web 工具去取第三方 URL）、`test_result_set_titles_are_not_evidence_...`、GUARD `..._scope_is_evidence_...`、`test_a_yes_no_question_form_is_not_a_negation`（是不是/有没有/or not）、`test_a_financial_acronym_is_not_an_added_company`（EPS/GAAP/CEO） |
| web 主题/新鲜度校验任务级 | `task_clause()`：按连接词/标点切子句，只含公司名的子句并入后一句（"苹果和英伟达的最新新闻"共享"最新"），不含公司的子句视为共享；任务用"点名它的子句 + 共享子句"做 `validate_task_query`，找不到时退回整句 | 原红灯转绿；GUARD `test_dropping_the_freshness_word_...` 与自补 GUARD `test_shared_freshness_word_still_applies_to_every_company_news_task` 保持绿 |
| 任务级 scope：值出自用户原话或所选结果集，归属以已保存清单为准，失败关闭 | `validate_plan` 接受可选 `scope`（仅 4 键、格式校验、工具必须能用该键、只能单公司）；`_task_scopes()`：声明值须出自用户原话/canonical 或所选结果集（否则 `plan_scope_unsupported`）；未声明的请求值只在所有取数任务同一公司时复制；没人能承接的值 → `plan_scope_ambiguous`；同一 id 给两家公司 → `plan_scope_conflict`；`_saved_owners()` 用同步引擎在线程里查 `sec_filing_inventory`/`uploaded_evidence`，清单说属于别家 → `plan_scope_owner_mismatch`，清单不认识的 id 放行（工具按公司过滤）；查库出错 → `plan_scope_unverified`；有期间无 source/accession → clarify `period_requires_accession`；planner 提示词说明 `scope`；删除被取代的全局 `plan_source_scope_ambiguous` | 原 2 条红灯转绿；3 条 NET 仍绿；自补 `test_period_given_only_in_a_task_scope_asks_for_an_accession`、`test_two_report_forms_without_task_scopes_are_not_guessed`、NET `..._source_id_cannot_serve_two_companies`、`..._owned_by_another_company_is_rejected`（PostgreSQL）、`..._on_a_tool_that_cannot_use_it_is_rejected` |
| `blocked` 的前端呈现（不加新枚举） | 无需改代码：`blocked` 只在任务级；消息级仍是 `partially_succeeded`，前端已有"部分来源可用；缺项已标出" | GUARD `test_blocked_task_reaches_the_client_as_a_known_message_status`（读取 `frontend/src/api.ts` 的 done 状态白名单做跨端断言） |

NET/GUARD 不是空测：用临时变异探针（不入库）逐一删掉 15 处检查（过期、会话绑定、换公司、id 冲突、清单归属、来源出处、值覆盖、工具键、未知键、子句合并、scope 依据、标题依据、assistant 依据、子串否定、期间无 pin；共 16 个探针），对应测试全部变红。

**行为变化（S5/G2 需知）**：模型改写里新增的公司/编号/期间/URL/否定一律澄清；多公司财报问题需要 planner 给每个任务 `scope`，否则 `plan_scope_ambiguous` 失败关闭；web 任务带 `scope` → `plan_scope_invalid`；动作类 pending 30 分钟过期。

## S5 结果（真实 DeepSeek `deepseek-flash` + Tavily，影子 API 9030，RAG 关闭，游客身份）

原始输出：`/tmp/market-s5-acceptance/raw/*.json`（不含密钥；驱动 `driver.py`/`run.py`）。31 个聊天轮次（`POST /agent-chat/stream`）+ 约 70 次单阶段模型探针（只调一个阶段，不是聊天轮次）+ 约 16 次 Tavily 直连探针。

| # | 场景 | 结论 | 证据 |
|---|---|---|---|
| 1 | 搜两条苹果营收新闻 → 总结 → 解释第二条 | **FAIL（原话）**；去掉"营收"后 **PASS** | 原话两次都是 `requested_metric_missing:revenue`：证据门要求可见摘录含 revenue/sales/营收/收入，而 `bounded_tool_result` 把每条网页摘录截成 700 字；`deterministic_confidence_gate`/`bounded_tool_result` 与基线逐字相同，基线查询与重试查询回放同样为 False，属预先存在、需产品决策。无"营收"措辞：A1b 1 次 `web_research`；A2、A3 `tool_calls=0`、`reused_sources=true`、同样两条来源、无动作卡、无新增搜索，引用序号与标题对应；A3 在重启影子进程后仍从检查点读到 |
| 2 | 检查点重开 / 切模型 / 取消 / 指代不明 | **PASS** | 每轮都重开 saver；A2→A3 跨进程重启；C2 用 `deepseek-chat` 接 `deepseek-flash` 会话；C1 在首个 `tool_start` 后关流，库中助手行 `cancelled`，C2 得 `reference_unavailable`（取消轮证据未被复用）；新会话"总结刚刚的内容"→`reference_unavailable` 澄清，0 次工具 |
| 3 | 苹果 → 英伟达 → "苹果那组" | **PASS** | A5：`tool_calls=0`，只复用 RFI 与新浪两条苹果来源，无英伟达内容；A4 的英伟达搜索另走一次 `web_research` |
| 4 | 先搜两条英伟达再总结 | **PASS（修复后）** | 首次 FAIL `plan_scope_mismatch`（意图阶段 3 次里 1 次只给 `web_research`，计划里的从属总结被拒）；修复后 E1b：1 次 `web_research` + `existing_result_summary` succeeded，`reused_sources=true`。"父失败→子 blocked"未能在线触发，仍只有测试覆盖 |
| 5 | "苹果最新股价和英伟达新闻"；"…已保存股价…分别列出" | **PASS（修复后）** | F2 一次通过（`query_stock_prices` + `web_research`，分块回答）。F1 三次：`provider_timeout`（planner 25 s）、`plan_scope_mismatch`（意图只给 `web_research`，而 planner 正确地规划了已保存价格）、修复后通过，两任务各自成功并分块 |
| 6 | 最新财报限 10-Q/10-K；显式 accession；2025Q4 | **PASS（修复后）** | G1 `query_material_analysis{financial_only:true}`→10-Q 0000320193-26-000020；G2/G3 首次都被 resolver 的 `clarify` 拦下（`reference_unavailable`），修复后 G2 `query_sec_filings{financial_only:true, limit:1}`、G3 两个工具同一 accession、引用类型 `saved_filing` 与 `saved_ai_analysis` 分开且答案分列；G4 `period_requires_accession`。未在线做 NVDA（最新 8-K 比 10-Q 新）的区分用例 |
| 7 | 来源范围内扩写（多个合法变体、过滤不变） | **NOT VERIFIED LIVE** | 需要真实 RAG，影子库禁用。测试覆盖：`test_knowledge_expansion_runs_each_variant_with_identical_filters`、`test_web_search_is_not_query_expanded`、`test_rejected_variants_leave_only_the_original_query`、`test_expansion_degrades_instead_of_exceeding_the_tool_budget`、`test_variant_results_are_merged_without_duplicate_evidence`、`test_query_variants_preserve_period_negation_and_exact_source` |
| 8 | 游客 pending 续接 | **PASS** | H1 `missing_action_source`；H2 回 UUID → 同一 `import`（AAPL + 该 source_id）→ `owner_login_required`，`agent_action_requests` 0 行；H3/H4：无关新问题正常回答、无任何写入工具 |
| 9 | 无证据 / 提供商错误 / 预算耗尽 | 前两项 **PASS**；预算 **未在线触发** | I1（AMD 无已保存 10-K 正文）、I2（不存在的 accession）→ `no_source`，固定回退文案、0 条引用；I3 无效 key → `error{provider_invalid_key, stage:resolver, 401}`，无答案、无泄漏。预算：没有不造作的办法在线触发；测试 `test_semantic_stage_reserves_and_intent_repairs_once`、`test_stream_deadline_returns_normal_done_and_checkpoint_status`、`test_final_generation_is_skipped_after_large_actual_tool_evidence`、`test_final_actual_usage_over_limit_is_recorded_and_stops_normally` |

### 在线发现的根因与修复（每项先写红灯，再最小改动；S4 安全属性未放宽）

| 根因 | 修复 | 测试 |
|---|---|---|
| resolver 对"自成一体"的新问题偶发回 `clarify`/`followup`（10 个新会话轮次里 2 次），用户被告知"之前的可核实结果已不可用" | `semantic.standalone_request`：无结果集、无 pending、无回指词、且用户原话自己点名公司/accession/来源时，模型的 clarify/followup 视为误拒，用原话作 canonical；回指词（`BACK_REFERENCE_CUES` + 强提示词）、有结果集、无范围的问题都不放行；`graph._resolve_reference` 只在模型拒绝时介入，模型给出的合法改写照常校验。resolver 提示词补上 new/followup/slot_reply/clarify 的定义 | `test_resolver_refusal_of_a_self_contained_question_...`（4）、`test_search_then_summarize_request_is_not_taken_for_a_missing_reference`（2）、GUARD `test_back_reference_without_any_result_still_clarifies`（5）、`test_clarify_is_kept_for_a_question_that_names_no_scope`、`test_followup_to_an_existing_result_set_is_never_overridden`、提示词 2 例 |
| planner 把"给我标题和来源链接"抄进网页查询（`Apple revenue news reports with titles and source links`） | planner 提示词：网页查询只含检索关键词，标题/链接/来源/数量/总结/列表不属于查询 | `test_planner_prompt_keeps_presentation_requests_out_of_the_web_query` |
| 意图阶段对复合请求只给 `web_research`，而 planner 正确地加了从属总结或已保存价格 → `plan_scope_mismatch` | 意图提示词给每个标签下定义（价格问题属 saved_data，产品没有实时报价）、"搜索再总结"需要两个目的、多部分请求每部分一个目的；`graph._route_intent`：依赖本计划任务的 `summarize_existing` 不算意图之外的目的（planner 新增检索仍被拒） | `test_intent_prompt_defines_the_purposes_and_places_a_price_question_in_saved_data`、`test_intent_prompt_asks_for_both_purposes_of_a_search_then_summary`、`test_dependent_summary_is_accepted_when_the_intent_stage_named_only_the_search`、GUARD `test_planner_cannot_add_a_retrieval_the_intent_stage_did_not_name` |
| Tavily 的 `include_raw_content` 响应超过 180 kB 上限（"Apple revenue news" 为 195 kB）→ 整个搜索 `web_research_unavailable`（预先存在，`agent_web_research.py` 草稿前未改） | `_search(query, include_raw_content=True)`；仅在 `response_too_large` 时去掉原文重试一次，页面改走既有的受限直连抓取；其他错误不重试；上限未提高 | `test_oversized_search_response_retries_without_raw_content_instead_of_failing`、GUARD `test_other_search_failures_are_not_retried`、`test_search_can_ask_tavily_for_snippets_only` |

临时变异探针（不入库，7 个）：6 个被抓；"从属总结必须有 depends_on"是等价变体（resolver 引用路径上，无依赖的总结任务在更早处就被 `semantic_intent_reference_mismatch`/`plan_summary_source_missing` 拒绝）。

### 需要用户决策、未改代码

1. **证据门对"营收"类指标词的要求**（场景 1 原话失败的直接原因）：门检查的是被截成 700 字的摘录，今天对多种合法查询（包含基线自己的查询与重试）都判 `requested_metric_missing`。可选：保留（宁可拒答）、让网页行保留更长摘录、或把标题/Tavily 片段计入门。放宽证据门属安全规则变更，未动。
2. **planner 阶段 25 秒提供商超时**：F1 的 4 次干跑里 1 次、在线 1 次超时（意图错误导致 planner 在价格是否走网页间纠结）；意图修复后 planner 3–5 s。是否对 `provider_timeout`（`retryable:true`）在语义阶段重试一次是产品决策。
3. **价格工具结果没有 symbol 字段**，模型会写"无法确认这组数据就是苹果"（F1c、F2）；价格答案里的 `[n]` 也没有对应引用卡（H4 为 `[1]`–`[5]`，0 个 citations 事件）。两者在基线已如此，非本草稿引入。
4. 网页搜索只返回 3 条，E1b 的总结与引用卡覆盖 3 条而用户要"两条"（轻微）。

## S6 结果（相关性摘录、阶段重试、价格 symbol、真实重跑；影子 9030，RAG 关闭，游客，`deepseek-flash` + Tavily）

对 S5"需要用户决策"的处理：1 用相关性摘录解决，**证据门与 `bounded_tool_result` 仍与基线逐字相同，未放宽**；2 已实现（一次重试）；3 价格行已带 `symbol`（仍无价格引用卡、无前端新类型）；4 未动。

| 根因 | 修复（位置） | 测试 |
|---|---|---|
| 网页行 `excerpt` 取页面文本开头，`bounded_tool_result` 再按位置截到 700 字，含"营收"的句子在进门之前就没了（`requested_metric_missing:revenue`；单元级复现与线上一致） | `agent_web_research.py`：`focus_excerpt(text, query, limit=700)` + `_focus_source`（作用于页面抓取、Tavily 抽取、抓取失败回退三种行；URL 直读不变）。按句/行切分（每段 ≤348 字；长句在空白/逗号处切，且不切开被问到的词）；按查询里**不同词组**打分：普通词（大小写不敏感；拉丁词按词边界，CJK 子串）、门使用的指标同义组（`METRIC_GROUPS`）与公司别名组（`ENTITY_ALIASES`）；先贪心覆盖每个词组，再放首句，再按分数/位置补满；保持原序，非相邻段用 `" … "` 连接，每段都是原文子串，总长 ≤700；无可用词或无匹配 → 原样返回（今天的行为）；被选中后行的 `truncated=True` | `test_agent_web_research.py` 19 例：纯函数 14（含与门源码逐字一致的漂移检查）、`web_research` 工具 2、"截断后门仍通过"端到端 1、GUARD 2（无匹配与 URL 直读不变） |
| 语义阶段一次瞬时提供商失败（planner 25 s 超时）就结束整轮 | `semantic.py::call_stage`：`retryable` 的 `ProviderFailure`（504/502/503/429，含 `HTTPException` 转换）重试同一阶段请求一次；仅当距回合开始 <45 s 且 token 预算检查仍通过（`_within_stage_budget`）；失败尝试 0 token；不重试不可重试错误、`provider_redirect_rejected`、无效 JSON（intent 自己的一次修复不变） | `test_agent_semantic_regressions.py` 12 例（整轮图级 1 例；"不重试"GUARD 5 例） |
| 价格行没有 symbol，模型写"无法确认这组数据就是苹果" | `agent_tools.py::query_stock_prices` 的 SELECT 增加 `symbol` | `test_agent_acceptance.py::test_saved_price_rows_name_the_symbol_they_belong_to`（真实 PostgreSQL） |
| **验收中新发现**：追问"总结一下你刚刚找到的两条新闻"被 S4 规则拒绝（`reference_scope_changed`）。解析器选对了集合和条目，但把两个标题（含 Q2/Q3）贴进 `canonical_question`，标题不算期间依据，被正确拒绝 | 解析器提示词末尾补一句：followup 的 canonical 保持用户原话，引用由 `result_set_id`/`item_ids` 承载，不贴标题、不新增公司/期间/数字/否定。**只改提示词，校验未动** | `test_resolver_prompt_keeps_a_followup_in_the_users_words`；单阶段抽样：改前 15/20 有效（"解释第二条"句 6/10），改后 36/36 |

**测试通过（不是线上证据）**：后端 `935 passed, 7 skipped`（S5：902）；回归文件 95 例（+13）、网页工具文件 41 例（+19）、验收文件 +1；前端 `npm run test:i18n` 71 passed、`npm run build` 通过（>500 kB 警告为既有，前端未改动）；`git diff --check` 干净。临时变异探针（不入库）：`focus_excerpt` 17 个全部被抓（初版有 6 个存活，已补强测试）；重试 7 个全部被抓。随机性质检查 1500 例（输出 ≤ 上限、每段为原文子串）无违例，最坏一次调用 17 ms。

**真实模型验收（9 个聊天轮次，上限 10；另有 1 次不经模型的 Tavily 直连探针、约 100 次单阶段解析器抽样（只调解析器一个阶段，不是聊天轮次））**：

| # | 场景 | 结果 | 流中状态与证据 |
|---|---|---|---|
| a | "帮我找一下跟苹果营收相关的新闻报道"（S5 失败的原话） | **PASS** | `succeeded` / `heuristic_coverage_pass`；1 次 `web_research`（`Apple revenue news`）、3 行、3 张引用卡、`tool_calls=1`。直连探针：同一查询在门之前保留了含 "Revenue grew 16% …" 的句子，门返回 `(True, heuristic_coverage_pass)`（S5 同一查询为 `requested_metric_missing:revenue`） |
| b-1 | 新会话：两条营收新闻（标题+链接）→ 总结 → 解释第二条 | **机制 PASS，内容有遗留** | b1 `succeeded`/`heuristic_coverage_pass`（查询 `Apple 苹果 营收 新闻`，2 行：一篇 2025-08 的界面新闻 + 一个 TradingKey 行情页，答案如实说只有一条营收新闻）；b2、b3 都是 `succeeded`/`verified_result_reuse`、`tool_calls=0`、`reused_sources=true`、同样两个来源、无新搜索、无动作卡；b3 因第二条是行情页，答案说"没有第二条新闻" |
| b-2 | 同一流程重跑（新会话） | r1 PASS；**r2 FAIL（`waiting_for_input` / `reference_scope_changed`）**；修提示词后 r2b、r3 机制 PASS | r1 `succeeded`（`Apple revenue news`，Yahoo/AP/CNBC 三行）；r2 被 S4 规则拒绝（见上表末行，已诊断）；重启影子后 r2b `succeeded`/`verified_result_reuse`、`tool_calls=0`、AP+CNBC 两条、`reused=true`；r3 `succeeded`/`verified_result_reuse`、`tool_calls=0`、只引用 CNBC（第二条）——但答案开头写"当前证据里只有一条新闻"（见下） |
| c | "苹果最新股价和英伟达新闻" | **PASS** | `succeeded`/`heuristic_coverage_pass`；`query_stock_prices{AAPL}`（5 行）+ `web_research`（`NVDA Nvidia latest news`，3 行）；价格段写"苹果（AAPL）最新存档股价"，没有"无法确认"字样，价格段无 `[n]` 标记，3 张引用卡全是 NVDA 网页 |

**未通过 / 未验证 / 需要决策**：

1. **"第二条"的答案措辞**（b3、r3 两次都出现；S5 的 A3 没有）：重用路径只把被选中的那一条交给回答模型（`_retrieve_knowledge` 的 `evidence`），用户原话仍说"第二条"，模型就写"证据集只有一条，无法定位第二条"，再解释这一条。选择本身是对的（id 与第二条一致，无新搜索）。可选改法：在 `evidence` 里加一句"这些就是用户所指的已保存记录，直接回答"。**未改**：只剩 1 个聊天轮次，无法在线验证；且它会让模型对解析器选错的情况更自信，属回答措辞的产品决策。
2. **S4"新增内容需依据"规则与带季度的标题**：本轮只用提示词降低了解析器贴标题的概率（36/36），**没有**放宽校验。更确定的做法（模型改写不合法时丢弃改写、用原话）会改变 S4 的合同（现有 S4 测试期望此时澄清），按规则未动，等决定。
3. **词法门看到更深的句子**：摘录选择提高了"句子里出现营收类词"的命中，包括模板或别家公司的句子（b1 的 TradingKey 行里有"该公司报告年度营收为 9.6726 亿美元"，被选进摘录；模型自己指出量级与苹果不符）。门仍是词法检查、且合并所有行的文本，没有改。要不要让门按行或按实体检查是 S5 决策项 1 的延续。
4. 既有行为，未改：抓取失败回退行的 `truncated` 用 `len(raw) > 5000` 判断，而内容已被截在 5000，所以恒为 False（直连探针里 Yahoo 的 5000 字导航样板行就是这样）；这类行没有匹配时仍原样返回样板文字。
5. 仍未验证（需真实 RAG / 不好诱发）：来源范围内多变体扩写；预算耗尽；父任务失败 → 子任务 `blocked`。
6. 在线重试路径本轮没有被真实超时触发（影子里没有发生 provider 超时），只有单元与图级测试。

## G2 处理 A（评审阻断 1、阻断 2、第 9 项；第 11 项仅文档）

来源：`/tmp/market-g2/g2-review.md`（只读评审）。范围：阻断 1、阻断 2（含 `requested_action` 子串匹配）、第 9 项；第 11 项只补文档。严格 TDD：每项先写红灯并确认失败原因，再做最小改动。S4/S5/S6 的校验与测试未改、未放宽；`deterministic_confidence_gate`（`graph.py`）与 `bounded_tool_result`（`agent_chat_stream.py`）按 `ast` 取函数源码与 `8cfbd5d` 逐字相同。没有既有测试需要修改（`requested_action` 在全量套件里被调用的 45 个字符串，新旧实现 0 差异）。

| 评审项 | 改了什么（位置） | 测试（`tests/test_agent_semantic_regressions.py`） |
|---|---|---|
| 1 阻断：planner 可让网页工具读任意第三方 URL | `semantic.validate_web_query_url(query, original)`：web 任务的查询含 `://` 或以 `http` 开头时，必须与**用户原话**（`state["original_question"]`）里的某个 URL 逐字相同（`named_urls`），否则 `plan_source_scope_mismatch`。`graph._route_intent` 的 web 分支在其余检查和任何工具调用之前先调它。服务端为旧来源选择写的"请读取 <url> 并概括网页内容"句子本身就是原话，所以仍可读 | RED 7：`test_planner_cannot_make_the_web_tool_read_a_url_the_user_never_wrote`（5：评审原例、搜索词+URL 片段、`Https://`、`http://`、`https:` 无斜杠）、`..._pick_a_third_url_when_the_user_typed_two`、`test_a_url_from_an_earlier_turn_is_not_the_users_words_of_this_turn`；GUARD 4：`test_a_url_the_user_wrote_may_be_read_exactly`（3）、`test_a_search_phrase_without_a_url_is_still_searched`；既有 `test_model_cannot_add_a_url_for_the_web_tool_to_read` 不变 |
| 2 阻断：写操作对象仍由模型选定（仅所有者） | `graph._user_targets(original)`：非续接、意图恰为 `["system_action"]` 的轮次，`entities` 里 `symbols/symbol/source_id/accession_number/form` 只取 `extract_slots(原话)`（`_classify_intent` 语义分支）。`graph._names_more_than_user(canonical, original)`：canonical 带有原话没有的公司（去掉 `NON_TICKERS`）、source id、accession、form 或 64 位任务 id（比较不分大小写）时，`_route_intent` 在调用 planner 之前直接走既有澄清：forecast→`missing_action_symbol`，status→`missing_task_id`，其余→`missing_action_source`；`semantic_action` 照存，pending 里只有原话里的槽位，用户自己的回复照常续接（S4 的 `_resumed_action` 未动） | RED 15：`test_a_target_only_the_resolver_wrote_is_clarified_not_executed`（12：创建预测/导入/AI 分析/刷新原文/表单/第二家公司/状态的 UUID 与 64 位 id，以及"原话已写全、改写再多一个"）、`test_forecast_after_a_news_search_does_not_take_the_company_from_the_catalog`（评审的整轮例子；回复"NVDA"后只执行一次）、`test_the_clarified_target_is_completed_by_the_users_own_reply`、`test_a_financial_acronym_the_resolver_wrote_is_not_another_target`；GUARD 7：`test_a_target_the_user_typed_still_executes`（4）、`test_a_guest_who_names_the_target_still_ends_at_owner_login_required`（2，真实 `execute_action`，仍是 `owner_login_required`）、`test_the_canonical_question_is_compared_with_the_users_words_case_insensitively` |
| 2b `requested_action` 子串匹配 | `actions.requested_action` 重写：英文词按 ASCII 词边界匹配（`important`≠`import`、`forerunner`≠`run`、`second`≠`sec`、`created`≠`create`）；import 需 `导入`/`import` 后 25 字符内有对象（资料/材料/来源/source/material/filing/accession/accession 号/UUID），`加入资料库` 前后 25 字符内有对象（"将资料加入资料库"可，单独"加入资料库"不可）；index 需 `建立索引`/`重建索引`，或 `(re)index(ing)` 后 20 字符内有 资料/材料/source/material/filing/document；analyze 需动词短语（生成/创建/重新/开始/request/run … 分析/analysis）前后 25 字符内有对象（accession/accession 号/UUID/10-K/10-Q/8-K/filing/report/source/财报/年报/季报/报告/来源/资料/材料/原文/`AI 分析`）；原有的"分析/analyze + accession"不变；status/refresh/fetch/forecast 保持，只加词边界。对象表里的 `AI 分析`、`资料` 等是为既有用例"生成 AI 分析""生成 MSFT AI 分析"和文档里的"将资料加入资料库"补的（按评审要求"合法说法就扩对象表"） | RED 17：`test_ordinary_words_are_not_taken_for_a_write_request`（16：评审三例、`thread job status`、`refresh every second`、`fetch the second column`、`Dow Jones indexes`、`import AAPL`、`导入`、`加入资料库`、对象太远、`psychoanalysis`、`created a forecast` 等）、`test_a_read_question_that_the_intent_model_calls_an_action_runs_nothing`（`Nasdaq-100 index` 不再触发无槽位的全局 index 任务）；GUARD 36：`test_explicit_action_phrases_are_still_recognised`（25，含测试套件和 `agent-routing-requirements` 文档里的说法）、`test_read_questions_stay_reads`（11） |
| 9 `general` 不取证直接回答 | `semantic.names_company_or_metric(text)`：公司别名/ticker（不含 `NON_TICKERS`、`CHAT_ACRONYMS`、单字母；"3 PM" 这类钟点先剔除）、拼错的 ticker（`suggestion`）、accession/source id、报告期间（`PERIOD`、FY/Q1–4/财年/第N季度/年报/季报）、指标词（revenue/sales/profit/earnings/EPS/cash flow/debt/营收/收入/利润/每股收益/现金流/负债）。`graph._route_intent`：意图为 `general`、不是问候、且（原话+canonical）含其中任一项时不走 `general_answer`，以 `gate_reason=semantic_intent_invalid` 结束；`_fallback` 给该原因专门文案（并避免被通用的"语义工作流未能安全完成"覆盖）。问候仍是固定回复，一般闲聊照常由模型回答 | RED 17：`test_general_label_on_a_company_or_metric_question_is_not_answered_from_memory`（14）、`test_a_pronoun_resolved_to_a_company_question_is_not_answered_from_memory`、`test_the_users_own_words_decide_even_when_the_rewrite_dropped_them`、`test_semantic_intent_invalid_has_its_own_friendly_message`；GUARD 16：`test_greetings_and_generic_chat_keep_working`（含 "Hello, I need help"、HTML/SQL、"3 PM"、NYC、NASA、IFRS） |

**行为变化（用户需知）**

1. 网页 URL：只有**本轮原话**里的 URL 能被读取。上一轮用户贴的链接、被 resolver 补进改写里的链接不再被读（`plan_source_scope_mismatch`，显示通用的"语义工作流未能安全完成"），用户需要在本轮重新贴链接。按评审的字面规则，以 `http` 开头的查询（如"HTTP/3 新闻"）也被拒。
2. 写操作的对象必须写在同一条消息里：代词补全（"给它创建预测"，公司只在早先发言或结果集里）现在澄清 `missing_action_symbol`，用户回复代码后执行一次；原话已写全而改写另外加了公司/编号/表单时同样不执行，改走澄清。
3. 动作词：不再算动作的典型说法有"import AAPL"（无对象）、"导入"（无对象）、"开始分析"（无对象）、`创建索引`/`更新索引`（只认"建立索引/重建索引"）、"created a forecast"（过去式）、粘连词"refreshSEC"。意图模型标了 `system_action` 但原话没有明确动作时仍是 `implicit_write_rejected`（既有行为）。
4. `general`：判断是词面的，已知会误拒的类型：定义类问题含指标词（"What does revenue mean?"、"什么是现金流"）、含别名的闲聊（"What fruit is an apple?"，别名表把 apple/苹果 映射到 AAPL）、全大写英文句（"Please HELP me" 把 HELP 当 ticker）。`semantic_intent_invalid` 同时是"意图阶段输出无效"的原因码（既有），那种情况也会显示这条文案。

**记录（评审第 11 项，仅文档）**

- 重复澄清：`_clarify` 用 `resumed.get("id")` 复用同一个 pending id，所以一个动作请求连续多次澄清（先缺来源、回了 accession 又缺公司）仍是同一个 id、同一个幂等键（`sha256(会话:pending id)`）；每次重新澄清都把 `expires_at` 重置为"此刻 + 30 分钟"，有效期因此是"距最后一次澄清 30 分钟"，不是从第一次起算。
- `reference_*` 类 pending（指代无法确认时的澄清）不带 owner/conversation/expires_at：它只活一轮（`_resolve_reference` 每轮清 `pending_request`），没有 `selected_action`，原因不在 `ACTION_SLOT_REASONS` 内，不能续接写操作（`test_reference_choice_reply_cannot_become_a_write`）。

**变异探针（不入库，在草稿副本里改源码后跑整份回归文件）**：55 个——URL 检查 6（整体关闭、去掉 `://` 子句、去掉 `http` 开头子句、连用户自己的 URL 也拒、拿 canonical 代替原话比较、不调用）、写目标 17（entities 不限于原话、不做提前澄清、公司/source id/accession/form/任务 id 各自不检测、不排除 `NON_TICKERS`、大小写敏感、三种原因码丢失、pending 丢失显式动作、`_user_targets` 少键）、动作词 16（import/index/analyze/status/refresh/fetch/forecast 的词边界与对象要求、距离窗口、`加入资料库`、`建立索引`、`AI 分析` 对象）、general 16（整体关闭、各子条件、单字母/业务缩写/聊天缩写、钟点"3 PM"、地名缩写、只看原话、只看 canonical、通用文案覆盖、不路由）。首轮 2 个存活（任务 id 大小写、`slots["period"]` 子句），补了两条断言后全部被抓到。另外把所有 G2-A 检查关掉并还原旧 `requested_action` 的组合状态下，119 个新用例失败 56 个，等于上表各行的 RED 数（其余 63 个是 GUARD/固定）。

**未处理（评审其余项，等决定或下一轮；其中 3、4、5、7、8、10 已在下一节 G2-B1 处理）**：3（planner 查询可丢掉期间/数字/否定，而闸检查的正是这个查询）、4（报告类型与"财报/最新"限定来自模型改写）、5（跟进可能选中别家公司的结果集，复用时跳过证据闸）、6（canonical 自由文本被当用户发言往下传；Q1 的"跟进只用原话"方案）、7（阶段重试可能重复计费且不计入预算）、8（`focus_excerpt` 选中缺主语的指标句；Q2 的选句侧方案）、10（对比句拆分）、Q3（"第二条"措辞只写服务端核对过的 position/of）。本轮对第 6 项只收窄了"planner 查询→网页工具"通道里的 URL 部分；自由文本查询仍没有期间/数字/否定保护（第 3 项）。 **（其中 6、Q1–Q3 已在 G2-B2 处理。）**

## G2 处理 B1（评审第 3、4、5、7、8、10 项与 7 条小项）

来源：`/tmp/market-g2/g2-review.md`（只读评审）+ 本轮任务书。范围：评审 3、4、5、7、8、10 与 B7 四条小项；评审 6 与 Q1–Q3（决定 canonical 怎么选）留给 B-2，未动。严格 TDD：每项先写红灯并确认失败原因，再做最小改动；`deterministic_confidence_gate`（`graph.py`）与 `bounded_tool_result`（`agent_chat_stream.py`）按 `ast` 取函数源码与 `8cfbd5d` 逐字相同。没有网络、真实模型或影子服务调用。

| 评审项 | 改了什么（位置） | 测试 |
|---|---|---|
| 3 planner 查询丢掉期间/数字/否定 | `semantic.validate_query_keeps_clause(clause, query)`：web 任务非 URL 分支在 `validate_task_query` 之后，拿 `task_clause(原话, task.symbols)`（找不到子句时整句原话）检查：子句每个报告期间（`period_terms` 归一，`2025年第三季度`＝`Q3 2025`）都在查询里；子句每个数字组都在查询里（不计 accession/UUID/任务 id、期间表达本身、用户要的结果条数）；查询的否定数不少于子句（`negation_count`，是不是/有没有/or not 不算）。任一不满足 → `plan_topic_changed`。`_figures`/`RESULT_COUNT`/`PERIOD_TEXT` 为辅助；`CLAUSE_BREAK` 不再在两个数字之间的逗号处切分（`1,000`） | RED：`test_planner_query_cannot_drop_the_period_the_user_wrote`（6）、`..._a_number_...`（4）、`..._a_negation_...`（4）、`test_a_period_without_any_company_...`、`test_one_task_dropping_its_own_period_...`；GUARD：保留期间/数字/否定（含 FY25＝FY2025、1,000、两个否定要两个）、条数不算（`3条/前5篇/3个/top 3`）、各任务按自己的子句、canonical 补的期间不向 planner 要求（钉住"本轮原话"） |
| 4 报告类型与"财报/最新"限定来自模型改写 | (a) `semantic.validate_reference`：原话里的 10-K/10-Q/8-K 必须留在 canonical，只有 canonical 有的 form 要在用户发言/pending（含 `pending.form`）/所选结果集 scope 里有依据，否则 `reference_scope_changed`（`named_forms`）；(b) `TOPIC_GROUPS` 增加 财报/年报/季报/financial report/earnings report/annual report/quarterly report 一组（丢了 → `reference_topic_changed` / `plan_topic_changed`）；(c) `graph._task_scopes` 只读原话 + 所服务的 pending（`resume_slots`）+ 所选结果集 scope（允许值与"请求里点名的值"都从这里来），`graph._filing_scope(task, scope, words)` 只读 `task_clause(原话, task.symbols)`，不再读 canonical 和 planner 自己写的 query | RED：`test_reference_resolution_cannot_swap_drop_or_add_a_filing_form`（4）、`..._cannot_drop_the_report_type`（4）、`test_planner_query_cannot_drop_the_report_type`、`test_a_form_the_assistant_wrote_earlier_is_not_evidence`、`test_a_form_only_the_rewrite_names_does_not_scope_the_saved_filing_read`（financial_only/limit 不丢、不选 8-K）、`test_a_task_scope_value_only_the_rewrite_holds_is_not_accepted`、`test_a_report_word_the_planner_wrote_does_not_limit_...`、`test_report_words_only_the_rewrite_holds_...`、`test_each_company_task_takes_the_report_limits_of_its_own_clause`、`test_the_pendings_own_form_...`、`test_the_selected_sets_own_form_...`、`test_a_followup_on_a_saved_filing_set_stays_pinned_...`；GUARD：忠实翻译保留 form/报告类型、用户自己更早发言里的 form 可补代词 |
| 5 跟进选中别家结果集，复用跳过证据闸 | (a) `semantic.validate_reference`：relationship `followup` 时，原话点名的公司（`named_companies`，不含业务/聊天缩写）必须 ⊆ 所选集 `scope.symbols`，否则 `reference_scope_changed`；集的 scope 没有 symbol 时靠 (b)。(b) `graph._reuse_gate` + `_retrieve_knowledge` 的 summarize 分支：复用的行在标成功前过基线闸，问题是只由用户原话里的指标词（`METRIC_GROUPS`，按词边界）、期间（按年/季度归一，英文行里找不到"2025年"）和公司名（经 `verified_symbols`）合成的"合成问题"；网页行以 `source_type=web` 交给闸，已存文件行带其集合 scope 的单一 symbol 且问题里不放公司（否则闸会在摘录里找 ticker 字面）；失败 → 任务 `failed`（原因＝闸的原因）、不写总结，`gate_reason` 即该原因 | RED：`test_a_followup_naming_one_company_cannot_use_another_companys_result_set`、`..._two_companies_needs_both_...`、`test_a_set_without_symbols_in_its_scope_is_still_gated_...`（`requested_entity_missing`）、`test_reuse_is_gated_on_the_metric_...`（`requested_metric_missing:profit`）、`test_the_period_in_the_users_words_is_checked_...`（`requested_period_missing`）、`test_a_failed_reuse_leaves_the_independent_task_standing`（`partially_succeeded`）；GUARD/钉住：自己公司的集合、不点名公司、有指标的行、已存文件行的指标闸（`test_reused_saved_rows_are_gated_on_the_metric_too`）、"总结一下那两条新闻"/"those two articles" 不会因词面覆盖失败、网页行按 web 处理（只有 Apple 没有 AAPL）、已存文件行靠集合 symbol、`steps` 不算 `eps` |
| 7 阶段重试重复计费且不计入预算 | `semantic.call_stage`：`NO_RETRY_CODES` 加入 `provider_incompatible_response`、`provider_response_too_large`；重试 `provider_timeout` 前把 estimate＋2,000 输出余量计入 `used` 并计入返回的 cost（重试的预算检查含这笔，放不下就不重试、抛原失败）；其他可重试失败仍免费；新增 `attempts`/`STAGE_MAX_REQUESTS=3`，意图修复接着外层的计数，修复＋重试合计每阶段最多 3 个请求 | RED：`test_a_response_the_provider_already_produced_is_never_asked_for_again`（2）、`test_a_timed_out_attempt_is_charged_...`、`test_a_charged_timeout_has_to_fit_...`、`test_a_charged_timeout_counts_against_the_budget_after_the_retry_answers`、`test_intent_repair_and_the_retry_make_at_most_three_requests`、`test_the_repair_may_still_retry_...`（含计费）、`test_a_charged_retry_chain_reaches_the_turn_total`；GUARD：其他瞬时失败免费 |
| 8 `focus_excerpt` 选中缺主语/别家公司的指标句 | `agent_web_research._query_groups` 返回 (模式, 角色)；`focus_excerpt`：查询里同时有闸认识的公司和指标词时，含指标的句子只有自己或原文紧邻的前一句含该公司别名才可选，被选中时前一句（逐字）与它一起加入（两句放不下则都不加）；覆盖阶段、首句保留和填充阶段都遵守；没有可用的句子时返回空串（该行因无摘录被丢弃）。无公司或无指标词的查询与旧实现逐字相同（9,000 组随机比对 0 差异） | RED：`test_focus_excerpt_does_not_select_a_metric_sentence_that_follows_another_companys_sentence`（评审用例）、`..._keeps_a_metric_sentence_together_with_the_sentence_...`、`..._fill_never_adds_...`、`..._never_keeps_a_leading_metric_sentence_...`、`..._is_empty_when_every_sentence_...`、`test_a_subjectless_revenue_sentence_beside_another_company_no_longer_passes_the_gate`（未改的闸现在给 `requested_metric_missing:revenue`）、种子随机性质检查（120 页×3 个上限）；既有 `test_focus_excerpt_never_exceeds_the_bound_and_never_invents_text` 增加 3 种页面形状 |
| 10 对比句拆分 | `semantic.CLAUSE_FILLER` 增加 比/相比/怎么样/怎样/如何/呢：只含"公司＋比较/怎么样"的子句视为只有公司名，并入相邻子句（"苹果的营收跟英伟达比一下"里英伟达任务也带营收要求）；句尾的只含公司的子句并入**最后一个点名公司**的子句（不点名公司的子句仍由所有任务共享，"苹果的营收和利润，英伟达比一下"里两个任务都带营收和利润）。`CLAUSE_BREAK` 不在数字之间的逗号处切分 | RED：`test_a_clause_that_only_names_a_company_keeps_the_words_of_its_neighbour`（8）、`test_a_trailing_company_clause_keeps_a_shared_clause_shared`（2）、`test_the_task_for_a_company_named_in_a_comparison_clause_must_keep_the_topic`；GUARD：独立子句仍独立（3）；旧/新 `task_clause` 的 8,000 组随机比对只在含新虚词的句子上不同 |
| B7a 回退文案 | `graph._fallback`：`plan_source_scope_mismatch` →"请把要读取的完整链接直接发给我。"；`reference_scope_changed` → 与澄清同一段文案（`REWRITE_NOTICE`） | `test_a_url_the_user_never_wrote_ends_with_a_friendly_request_for_the_link`、`test_a_rewrite_that_changed_the_scope_has_its_own_fallback_text` |
| B7b URL 判断 | `semantic.validate_web_query_url`：只看 `://`（包含开头的 `http(s)://`），"HTTP/3 news"、`https:` 无斜杠的短语是普通搜索 | `test_a_phrase_that_merely_starts_with_http_is_a_search_not_a_url`（4）、`test_a_query_with_a_scheme_separator_is_a_url_...`（6）、`test_a_search_phrase_starting_with_http_is_searched_through_the_whole_turn` |
| B7c 原因码 | `general` 无证据的拒绝改用 `semantic_general_requires_evidence`（友好文案不变）；`semantic_intent_invalid` 回到通用文案 | 3 个既有断言改码、`test_general_requires_evidence_has_its_own_friendly_message` |
| B7d 回退行 `truncated` | `agent_web_research._search`：在 5,000 字符截断处记录是否截过（`raw_content_truncated`，没截时不带该键）；`_research` 回退行用它，不再恒为 False | `test_search_says_when_it_cut_the_raw_content_of_a_result`、`test_fallback_row_is_truncated_exactly_when_the_search_text_was_cut` |

**既有测试的改动（10 个函数）**：`test_planner_stage_retries_a_provider_timeout_once`（超时的那次现在计费，成本＝40＋估算＋2,000）；`test_planner_cannot_make_the_web_tool_read_a_url_the_user_never_wrote` 去掉参数 `https:attacker.example/...`（无 `://`，不再算 URL，改为 GUARD 并入 `test_a_phrase_that_merely_starts_with_http_...`）；`test_general_label_on_a_company_or_metric_question_is_not_answered_from_memory`、`test_a_pronoun_resolved_to_a_company_question_is_not_answered_from_memory`、`test_the_users_own_words_decide_even_when_the_rewrite_dropped_them` 的原因码改为 `semantic_general_requires_evidence`；`test_semantic_intent_invalid_has_its_own_friendly_message` 改名为 `test_general_requires_evidence_has_its_own_friendly_message` 并加上"`semantic_intent_invalid` 用通用文案"；`tests/test_agent_web_research.py`：`..._matches_case_insensitively_and_by_metric_synonym_group`（3 句）、`..._follows_the_gate_when_one_phrase_asks_for_two_metric_groups`（2 句）、`..._does_not_cut_a_requested_term_in_two`（1 处）的夹具补了公司名（新规则要求指标句旁有公司；断言本身不变，在旧实现上也通过），`..._never_exceeds_the_bound_and_never_invents_text` 只追加了 3 种页面形状。

**行为变化（用户需知）**

1. 网页查询必须带上用户写的期间、数字和否定；"财报/年报/季报"一类词必须原样（或在组内）保留，把"财报"改写成 "earnings" 会被拒（`plan_topic_changed`）。两个中文否定对应的查询里也要有两个否定词（"excluding iPhone and Mac" 只算一个）。
2. 用户要几条结果（`3条`、`前5篇`、`3个…新闻`、`top 3`、`3 articles`）不算查询里要保留的数字（本轮加的豁免，任务书未写；planner 提示词本来就要求把条数留在查询外）。
3. 跟进里原话点名的公司必须都在所选结果集的 scope 里；"总结刚才那两条新闻，另外再搜英伟达"这类复合跟进会被澄清（`reference_scope_changed`，文案是"改动了你没有写明的…"）。
4. 复用现有结果时，问题里的指标/期间/公司必须能在被复用的行里找到；否则任务失败、不给总结（回退文案）。
5. 已存文件行的复用：用户的公司名只经 `verified_symbols` 检查（不放进合成问题，否则闸会在摘录里找 ticker 字面）。
6. 只由"不含公司名的指标句"组成的页面，摘录为空，该行被丢弃（以前会当作该公司的营收证据）。

**变异探针（不入库，在草稿副本里改源码后跑上述两个测试文件）**：93 个——B1 16（检查不调用、子句取自 canonical/整句、期间/数字/否定各自去掉、否定改"有无"、期间按原文比较、三类条数豁免、accession/UUID 数字、期间数字、千分位、数字间逗号）、B2 21（form 保留/依据缺失、依据只来自原话/缺 pending/缺集合 scope/含助手文本；报告类型组整体与 7 个成员各一；`_task_scopes` 的允许值/请求值/form 读 canonical、集合 scope 不读；`_filing_scope` 读 canonical/planner 查询/整句）、B3 14（followup 检查去掉/对空 symbols 也查/交集代替子集；闸恒过、合成问题用整句、web 行当已存行、已存行不带 symbol、公司不进 verified_symbols、指标按子串、不查期间/指标、失败不算失败、只闸一类行）、B4 13（两个 502 各自重试、重定向重试、超时不计费、成本不含计费、`used` 不加、重试预算不含计费、无请求上限、修复忘记计数、所有瞬时失败计费、余量 0、耗尽错误丢计费、修复返回丢计费）、B5 10（资格不强制、邻句不同行、只看自身、任意邻句、下一句当邻句、无指标/无公司/整体关闭、实体/指标角色丢失）、B6 10（6 个虚词各一、整组、尾部并入最后一句、尾部丢弃、前置不前移）、B7 9。首轮有 6 个存活：4 个补测试后被抓（已存行的指标闸、修复路径上首次超时的计费、尾部公司子句并入"前一个点名公司"的子句、前置公司子句并入后一子句）；2 个是等价变体：`covered` 是否含邻句（3 万个随机页面输出相同，该行已还原为原样并去掉该探针）、`strict` 里的"查询有指标词"条件（没有指标词时 `needs_neighbour` 本来就为假）。在最终代码上整套重跑：93 个中 92 个被抓，唯一存活的就是后一个等价变体。旧/新实现的随机比对：`focus_excerpt` 在无公司或无指标词的查询上 9,000 组、`task_clause` 8,000 组只在含新虚词的句子上不同。

**记录/观察（未改）**：(a) 图里排在 `summarize_existing` 任务之后的取数任务不会被执行（`_after_retrieve` 在 `semantic_final` 且没有剩余来源时直接 final；有剩余来源时先 `retrieve_knowledge` 取下一个任务，但随后 `next_action` 仍是 `semantic_final`，工具执行被跳过）——B1 的测试把总结排在最后；(b) 知识关键词类任务（`query_materials`/`search_material_analysis`/FAQ）的 planner 查询同样被闸当问题，但本轮的"保留期间/数字/否定"检查只加在网页任务；(c) 评审 6、Q1–Q3 未动。 **（以上 (a)(b)(c) 均已在下一节 G2 处理 B2 处理：(a)(b) 见 C3/C4，(c) 见 C1/C2。）**

## G2 处理 B2（评审第 6 项与 Q1、Q3；B1 观察 (a)(b)；真实模型复核）

来源：`/tmp/market-g2/g2-review.md`（第 6 项、答 Q1、Q3）+ 本轮任务书（C1–C5）。严格 TDD：每项先写红灯并确认失败原因，再做最小改动（6 个"回退探针"在副本里去掉各修复，新增红灯确实失败：C2 选择 20 例/行字段 16 例、C3 两处各 1 例、C4 15 例、slot_reply 5 例）。`deterministic_confidence_gate`（`graph.py`）与 `bounded_tool_result`（`agent_chat_stream.py`）按 `ast` 取函数源码与 `8cfbd5d` 逐字相同。只改了 `semantic.py`、`graph.py`、`state.py`（只加 3 个键）与回归测试文件；前端未改动。

| 项 | 改了什么（位置） | 测试（`tests/test_agent_semantic_regressions.py`，共 +179 例） |
|---|---|---|
| C1 模型自由文本不进入后续阶段（评审 6、Q1） | `semantic.downstream_question(reference, words, pending, is_write)`：`validate_reference` 通过**之后**由服务端选下游问题。**(a)** `followup`：用户原话；点击"结果集选项"时消息只是集合 id，用 pending 保存的原问题（`words`），不是 id、不是模型文本。**(b)** `new` 且原话无代词/回指词：原话。**(c)** `new` 且原话含代词/回指词（`ANAPHORA` = S5 守卫的强/弱列表 + 他们/她们/该公司/这家公司/那家公司/their/that company/the company，先剔除 URL）：只有改写的 `reference_residue` 与原话相同才保留改写——残余 = 去掉公司别名（含"公司/集团/Inc." 后缀）、ticker（与校验同一读法，`NON_TICKERS`/聊天缩写不算公司）、代词（`COMPANY_PRONOUNS` = 公司代词短语 + 弱指示词 这/那/此/该/其/它/it/this/that…）、所有格 's、的、句末助词（呢吗吧啊呀嘛么哦啦）及全部空白/标点/大小写（NFKC+casefold）；否则澄清 `reference_unavailable`，文案"请直接写明公司名称"（`COMPANY_NOTICE`，无选项；`SemanticError.hint="company"` → `semantic_hint`）；原话已点名公司时改写不可信就直接用原话（不澄清）；显式写请求（`requested_action(原话)`）不走 (c)，永远用原话。**(d)** S4 `_resumed_action` 不经此处；模型自己标成 `slot_reply` 的回复：有待办请求且回复是该待办的有界槽位值（`is_pending_reply`）→ 待办原话 + 本次回复，否则按 `new`。`graph._resolve_reference`：`words`/`choice_set`；`validate_reference(original=words)`；`original_question=words`；`semantic_reference.canonical_question` 换成所选问题（intent/planner 载荷、`messages` 的用户消息、回答模型所见问题都取它）；`semantic_conflict`（改写指向原话没有的写目标时仍先澄清，G2-A 行为不变）。resolver 提示词补一句（代词替换只换公司名，不翻译/缩写/改写；跟进的引用放在 `result_set_id/item_ids`）。planner 载荷 `symbols` 含所选结果集 scope 的公司（服务端状态，`validate_plan` 本来就允许） | RED：跟进改写带注入句→只有原话进入 intent/planner/回答；自成一体的新请求 3 种恶意改写（注入句、别名表外的公司、换词）；代词改写翻译/转述/追加/注入 4 种→澄清且不执行；点击选项用 pending 原问题；写请求带代词；planner 得到集合公司；提示词；`slot_reply` 3 例（无待办、有界槽位值、非槽位值）；点击选项被 resolver 指向另一个集合→再次澄清且保留原问题。GUARD：忠实补全 6 种保留；原话已点名公司时不澄清。纯函数：残余相同 15、不同 14、回指词 21、各关系规则表 1 |
| C2 服务端核实的位置（Q3） | 复用行带 `position`/`of`（所选集合自己的条目顺序；Round C 起字段名为 `place_in_earlier_list`/`earlier_list_size`）；`semantic.ordinal_positions(原话, 总数)`：第N条/篇/则/个（中文或阿拉伯数字，含十几/二十几）、前N条、最后一条、first/second/…/1st–6th + one/article/story/item…、first two/N、last one；季度/月/工作日/URL 内的词不算；`个` 只在后跟记录名词、功能词或句尾时算。原话有位置时 `item_ids` 由集合顺序推出（模型选的不同就忽略）；超出范围（含 0、前九条）→ `reference_item_unavailable` 澄清（`ITEM_NOTICE`，无选项）；只有核实过位置才在复用证据消息里加一句固定英文（单条 "…it is record 2 of 3 in that result set."；多条 "…they are records 1 and 2 of 3 …"，只有服务端数字）；点击选项的回复用 pending 原问题里的位置 | RED：10 种说法选对条目（模型选了别的）、前两条/Compare the first two/前2篇、第一条和第三条、5 种越界、选项回复里的位置；GUARD：无位置不加句子且模型的 id 照旧校验、非法 id 仍澄清、别家公司的集合仍拒绝；读法表 40 例 + 越界 8 例 |
| C3 总结之后的任务 | 失败原因：总结任务把 `next_action` 设为 `semantic_final`，其后取到待执行工具的任务没有改回，`_after_retrieve` 在无剩余来源时直接去最终回答，工具从未执行。`_retrieve_knowledge` 交出待执行任务时设 `next_action="retrieve"`。测试同时暴露**第二个缺陷**（B1 引入）：复用闸用整句请求里的全部公司（NVDA 的总结被要求出现 AAPL → `requested_entity_missing`），改为该任务自己的子句 `task_clause(原话, task.symbols)` | `test_a_task_planned_after_a_summary_of_the_same_turn_still_runs`（搜索 NVDA → 总结 → 已存 AAPL 价格：三项都跑、各自 succeeded、回答看到全部证据）；只回退第一处时在"价格工具没被调用"处失败，只回退第二处时在 `sum: failed` 处失败 |
| C4 关键词任务的保留规则 | `query_materials`/`search_material_analysis`/`query_research_faq` 的 planner 查询**取前 80 个字符**（即工具真正搜索的关键词，也是闸判断的查询）必须保留该任务子句的期间、数字、否定（与网页任务同一函数 `validate_query_keeps_clause`），否则 `plan_topic_changed`。已保存来源类工具的期间要同时钉住 source id（否则更早的 `period_requires_accession`），FAQ 没有来源可钉 | 26 例：3 个工具 × 期间/数字/否定（含保留版通过）；期间/数字落在 80 字符之后被拒；无需保留项（含条数、"有没有"）照常；各任务只检查自己的子句；续接问题里的期间不向关键词查询要求 |

### 本轮在线发现并修复（各先写红灯）

| 发现 | 修复 | 测试 |
|---|---|---|
| 点击"结果集选项"的回复**从来不能通过**：校验拿模型改写和那串不透明的集合 id 比数字，几乎必然 `reference_quantity_changed`（C1 的测试暴露，非本轮引入） | 校验与下游都用 pending 保存的原问题（`words`） | `test_a_reference_choice_reply_is_worked_on_in_the_pendings_saved_question`、`test_the_position_of_a_choice_reply_comes_from_the_words_the_choice_answers` |
| 模型自己标 `slot_reply` 可绕开 C1（其改写原样当问题） | 见 C1 (d) | 3 例 |
| 5b 在线：`帮我创建预测`（英伟达新闻之后）被 resolver 标成对新闻集的跟进，意图阶段是写操作，"跟进必须总结"检查以 `semantic_intent_reference_mismatch` 通用失败结束（未执行任何写，但没有追问公司） | 显式写请求（`requested_action(原话)`，意图恰为 `system_action`）不要求总结：写不从集合取任何东西，目标仍只来自原话，缺公司就澄清 `missing_action_symbol`，回复公司后只执行一次 | `test_a_write_request_the_resolver_calls_a_followup_is_clarified_not_failed`；在线修复后重跑通过 |
| 4b 在线：`它的最新新闻呢`（苹果营收情况之后）被标成对苹果已存文件集的跟进，意图阶段是网页搜索，同一检查通用失败 | 原话没有任何指向记录的词（`STRONG_BACK_REFERENCE` 不命中，只有代词）且意图是检索类时，集合只提供公司（planner 被告知），搜索按新请求跑，不复用集合；指向记录的词（刚刚/那组/第二条…）仍是不匹配 | `test_a_pronoun_followup_for_a_new_search_is_planned_for_the_sets_company`、`test_a_followup_that_points_at_records_must_still_summarise_them`、`..._and_is_no_write_must_still_summarise`、`test_a_non_write_sentence_the_intent_stage_calls_an_action_is_still_a_reference_mismatch`；**未在线重验**（聊天轮次已用完） |

### 既有测试的改动（7 个函数；需求确实变了）

| 测试 | 改动 | 原因 |
|---|---|---|
| `test_pronoun_completion_from_recent_dialog_stays_allowed` | 改写 `AAPL latest news` → `AAPL 的最新新闻呢` | 任务书指定：代词只能被公司名替换，翻译不再被信任 |
| `test_result_set_scope_is_evidence_for_a_pronoun_completion` | 改写 `NVDA latest news` → `NVDA 的最新新闻呢` | 同上 |
| `test_a_pronoun_resolved_to_a_company_question_is_not_answered_from_memory` | 改写 `苹果的营收呢` → `苹果呢` | 改写不能在代词处加入原话没有的"营收"；前提（代词补成公司）不变 |
| `test_a_form_the_user_wrote_earlier_may_complete_a_pronoun` → 改名 `..._is_not_carried_into_a_pronoun_completion` | 带 10-Q 的改写现在澄清；只带公司的补全通过 | 改写只能替换代词处的公司；表单从来不靠改写生效（`_filing_scope` 只读原话），所以没有功能损失 |
| `test_a_form_only_the_rewrite_names_does_not_scope_the_saved_filing_read` | 先断言带 8-K 的改写被拒，再用只含公司的补全断言原来的 `financial_only/limit` | 同上 |
| `test_a_task_scope_value_the_rewrite_holds...` → 改名 `..._only_an_earlier_turn_holds_is_not_accepted` | 改写改为只含公司；planner 仍声明 8-K（只在更早的用户发言里）→ `plan_scope_unsupported` | 改写已不可能带 8-K，被测规则（scope 值只来自本轮原话/所选集合）不变 |
| `test_the_period_check_reads_the_users_words_of_this_turn_not_the_rewrite` | 改写不再带更早的期间 | 改写不能加期间；被测规则（期间检查读本轮原话）不变 |

G2-A 的 `test_a_target_only_the_resolver_wrote_is_clarified_not_executed`（12 例）等写目标测试**未改**：改写不再被用作问题，但 `semantic_conflict` 保留了"改写指向原话没有的写目标就先澄清"。

### 行为变化（用户需知）

1. 改写不再是问题：无代词的请求按原话处理；含代词/回指词时改写只在"原话 + 公司名"时被采用，翻译、改述、追加、换问法、带表单/期间的补全一律澄清"请直接写明公司名称"（原话已点名公司时直接用原话，不澄清）。
2. 跟进问题是原话；planner 从所选集合的 scope 得到公司。**没有代词的省略问句**（"最新营收呢？"）不再被补全公司。
3. "第N条/前N条/最后一条/the second one…" 由服务端按集合顺序取记录，模型选的不同也忽略；集合没有那一条就澄清（不猜、不截断）；回答模型会被告知"用户按位置指代的是第 n/N 条"。
4. 关键词类任务的 planner 查询（前 80 字符）必须保留期间、数字、否定。
5. 点击"结果集选项"现在能工作。
6. 被 resolver 标成"跟进"的显式写请求不再通用失败，而是追问目标；只含代词的"它的…"搜索请求按新搜索（公司取自所选集合）处理。
7. 总结之后的任务会执行；总结的复用闸只要求该任务自己子句里的公司/指标/期间。

### 真实模型复核 C5（影子 API 127.0.0.1:9030，RAG 关闭，游客身份，`deepseek` / `deepseek-flash` + Tavily；12 个聊天轮次，上限 12）

原始输出：`/tmp/market-b2-acceptance/raw/*.json`（`api_key` 已剔除；对输出目录与 worktree 的密钥值扫描 0 命中）。构建：1、2a–2c、3、5b 首次是 C1–C4 完成后的构建 A；5b 重跑与 4a/4b 是放宽"写请求被标成跟进"之后的构建 B；5a、6 是再放宽"只含代词的跟进"之后的构建 C。随后又加了三处细化（`个` 的后随白名单、原话点名公司时回退原话、句末助词容忍），**它们之后没有在线轮次**。

| # | 项 | 结果 | 流状态 / 证据 |
|---|---|---|---|
| 1 | "帮我找一下跟苹果营收相关的新闻报道" | **PASS** | `succeeded`/`heuristic_coverage_pass`；1 次 `web_research`（`苹果 营收 新闻报道`，2 行）；第 2 张引用卡被既有的 `_restore_citations` 复核成"来源已失效"（本轮未碰该路径） |
| 2a | 新会话"帮我找两条…给我标题和来源链接" | **PASS** | `succeeded`/`heuristic_coverage_pass`；`Apple revenue news`，3 行 3 张卡；答案列出 AP、CNBC 的标题与链接 |
| 2b | "总结一下你刚刚找到的两条新闻的内容吧" | **PASS** | `succeeded`/`verified_result_reuse`；`tool_calls=0`，`reused_sources=true`，AP + CNBC 两条 |
| 2c | "把第二条新闻详细解释一下" | **PASS** | `succeeded`/`verified_result_reuse`；`tool_calls=0`；只引用 CNBC（上面列表的第二条）；答案**没有**说"只有一条记录"，明确说"position 2 / of 3 … 该结果集共有 3 条，我手上只有第 2 条"并逐点解释该文。措辞瑕疵：回答复述了字段名 `position 2 / of 3` |
| 3 | "苹果最新股价和英伟达新闻" | **PASS** | `succeeded`/`heuristic_coverage_pass`；`query_stock_prices{AAPL}`（5 行）+ `web_research`（`英伟达新闻`，2 行） |
| 4a | "苹果最近的营收情况"（铺垫） | succeeded | `query_sec_filings{AAPL}` 5 行；答案如实说保存片段里没有营收数字 |
| 4b | "它的最新新闻呢" | **FAIL（被测构建）** | `failed`/`semantic_intent_reference_mismatch`，0 次工具：resolver 把它标成对苹果已存文件集的跟进，意图阶段是网页搜索，"跟进必须总结"检查拒绝。**既不是成功也不是澄清**；离线修复见上，**未在线重验**（12 个轮次已用完） |
| 5a | 游客"导入 AAPL 的资料" → 回复已存 AAPL 10-Q 的 source UUID | **PASS** | 第一轮 `waiting_for_input`/`missing_action_source`；第二轮 `import{symbol:AAPL, source_id:…}` → `owner_login_required`，`status=failed`；`agent_action_requests` 0 行（只读核对） |
| 5b | 英伟达新闻之后"帮我创建预测" | 首次 **FAIL**，修复后 **PASS** | 首次 `failed`/`semantic_intent_reference_mismatch`、0 次工具（安全但没追问）；修复后同一会话重跑：`waiting_for_input`，clarification `missing_action_symbol`（"请提供要创建任务的股票代码。"），0 次工具 |
| 6 | 新会话"总结刚刚的内容" | **PASS** | `waiting_for_input`/`reference_unavailable`，0 次工具 |

重试在线上**没有被触发**（没有观察到提供商超时），仍只有单元与图级测试。C1(c) 的"代词补全被采用/被拒"路径在线上**没有被任何一轮走到**（4b 的 resolver 选了跟进）。

**变异探针（不入库，在草稿副本里改源码后跑 4 个测试文件）**：81 个，全部被抓（首轮 75 个被抓；6 个存活——`前0条` 与正常位置并存、点击选项被指向另一个集合、错误路径丢掉原问题、关键词保留读整句、关键词保留读续接后的问题、写放宽用于任何原话——补 6 个用例后重跑全被抓；更早两轮不完整的探针里另有 5 个存活，已在最终轮之前补测试：句子大小写、NFKC、URL 里的序数、`前0条`、位置排序去重）。覆盖：回指词检测（整体/URL/强列表/弱列表/额外词）、残余各步（ticker/别名/代词/的与句末助词/所有格/后缀/大小写/NFKC/标点/弱指示词）、问题规则（不比较残余/总用原话/写请求不豁免/跟进取改写/new 取改写/slot_reply 未检/slot_reply 取模型文本/无公司提示/原话点名公司的回退）、序数读法（6 种模式各一、`个` 的 4 种后随条件、时间单位、URL、越界上下界、0 个、排序去重、十位数、两）、图（选项原问题/选项校验/拿回复校验/改写当问题/写不豁免/冲突标志/原问题取回复/位置不应用/标志不存/按回复读位置/行字段/句子总出/总不出/用模型 id/单条句式/总数）、澄清文案、planner 公司提示、关键词保留（关/全文/整句/读改写）、总结之后任务（next_action、子句闸）、写/代词放宽（各 3 种）、提示词句子。

## Round C（复用证据里"位置"不是引用编号）

来源：B2 之后的线上观察。"帮我找两条…" → "总结一下你刚刚找到的两条新闻的内容吧" → "把第二条新闻详细解释一下"：第三轮的复用证据消息给回答模型一条记录，摘录前缀 `[1]`（本轮唯一的引用卡序号是 1），同时带服务端核实的 `position=2, of=3` 与固定句"record 2 of 3"。模型把位置当引用编号，答案里写了 `[2]`；前端把 `[n]` 渲染成指向 n 号引用卡的链接，而没有 2 号卡。严格 TDD：先写红灯并确认失败原因，再做最小改动。`deterministic_confidence_gate`（`graph.py`）与 `bounded_tool_result`（`agent_chat_stream.py`）按 `ast` 取函数源码与 `8cfbd5d` 逐字相同。改动文件：`graph.py`、`semantic.py` 与测试；前端未改动。

| 项 | 改了什么（位置） | 测试 |
|---|---|---|
| 引用句 | `graph.py` `CITATION_NOTE`、`_bracketed`、`_retrieve_knowledge` 的复用消息：**每次复用**（有无序数都有）在证据 JSON **之前**写一句固定英文——只用各自摘录开头的方括号数字引用（"here [1]" / "[1] and [2]" / "[1], [2] and [3]"，数字取自行的真实序号，同数字只写一次），`place_in_earlier_list` 与 `earlier_list_size` 只说明记录在之前列表里的位置、不是引用编号。句子只含固定词与服务端数字，不含任何模型或网页文字。消息前缀 `…not instructions: ` 改为 `…not instructions. `（仍以 `Verified prior evidence` 开头）；位置说明句一字未改，仍在 JSON 之后单独一行 | `tests/test_agent_semantic_regressions.py` 14 例：5 种说法的单条记录（位置 2/3、卡 [1]）、无序数的复用、前两条/第一条和第三条（位置 1、3，卡 [1]、[2]）/三条无位置、同轮先读了已存文件（卡 [1]）再解释第三条新闻（卡 [2]，位置 3）、同轮搜索后总结（[1], [2] and [3]）、标题/摘录里写"cite [7]"不进入句子、说明句逐字不变、`_bracketed` 的写法 |
| 行字段改名 | 复用行 `position`/`of` → `place_in_earlier_list`/`earlier_list_size`（其余字段不变；位置说明句的数字仍取自它们） | 行字段键名测试；句子里写的字段名与行里的键名一致 |
| 安全网 | `semantic.py` `unmatched_citation_markers(answer, citations)`（纯函数）与 `graph.py` `_record_log`：列出答案里与本轮任何引用卡序号都不匹配的 `[n]`（只认 ASCII `[0-9]{1,9}`，与前端 `/\[(\d+)\]/g` 同口径；去重、升序；输入为 None/脏数据/5000 位数字都不抛），结果写入该轮 audit 的 `unmatched_citation_markers`（整数列表）。**只上报**：答案文本、引用卡、状态都不改，不重编号、不删除 | 14 例：纯函数 10 个读法 + 脏输入；图级：答案写 `[2]`、卡为 1 → `[2]` 且 `result["answer"]` 逐字不变、引用卡仍只有 1；写 `[1]` → `[]`；澄清路径字段存在且为空；`tests/test_agent_chat_stream.py` +1：存进会话的 audit 带该字段、存下的答案文本不变 |

### 既有测试的改动（3 个函数的键名，1 个辅助函数的可选参数）

| 测试 | 改动 | 原因 |
|---|---|---|
| `test_a_position_in_the_words_selects_the_record_whatever_the_model_picked`（10 例）、`test_the_first_n_records_are_derived_by_position`（3 例）、`test_words_without_a_position_keep_the_models_records_and_say_nothing_about_position` | 断言行字段 `row["position"], row["of"]` → `row["place_in_earlier_list"], row["earlier_list_size"]` | 字段改名；被测要求（行说明自己在所选集合里的位置、`tail` 的位置说明句不变）没有变 |
| 辅助函数 `_ordinal_turn` | 多一个可选关键字参数 `says`（默认仍是 `"Explanation [1]."`） | 新测试需要让回答模型写别的文字 |

### 真实复核（影子 API 127.0.0.1:9030，RAG 关闭，游客身份，`deepseek` / `deepseek-flash` + Tavily；3 个聊天轮次，上限 4）

原始输出：`/tmp/market-round-c/raw/*.json` 与 `*.state.json`（`api_key` 已剔除；对输出目录与 worktree 的密钥值扫描 0 命中）。检查点只读取，用来核对本轮真正发给回答模型的证据消息。已停止影子并确认 9030 关闭。

| # | 问句 | 结果 | 证据 |
|---|---|---|---|
| C1 | 新会话"帮我找两条跟苹果营收相关的新闻报道，给我标题和来源链接" | 通过 | `succeeded`/`heuristic_coverage_pass`；1 次 `web_research`（3 行，3 张卡）；答案 `[1][2][3]`，`unmatched_citation_markers=[]` |
| C2 | "总结一下你刚刚找到的两条新闻的内容吧" | 通过 | `succeeded`/`verified_result_reuse`，0 次工具；证据消息 JSON 之前是 `…(here [1] and [2]); place_in_earlier_list and earlier_list_size say where a record stood in an earlier list and are not citation numbers.`；答案 `[1]`×6、`[2]`×6，卡 [1][2]，`[]` |
| C3 | "把第二条新闻详细解释一下" | **通过（缺陷现象消失）** | `succeeded`/`verified_result_reuse`，0 次工具；`semantic_positions={positions:[2], of:3}`；该记录行 `place_in_earlier_list=2, earlier_list_size=3`，摘录以 `[1]` 开头；JSON 之前的句子为 `…(here [1]); …`，JSON 之后一行位置说明句 `record 2 of 3`；答案共 13 个内联标记，**全部是 `[1]`**，返回的引用卡序号 `[1]`，本地比对无无卡标记，audit `unmatched_citation_markers=[]` |

观察（未改）：C3 的答案把句子的意思复述给了用户（"你所说的第二条对应早前列表中的第 2 条（共 3 条）；该位置标记只是排序信息，不是引用编号"）——与 B2 里复述字段名同类的措辞瑕疵，不影响引用；没有再改句子，因为本轮 4 个在线轮次的上限不够重建这一场景再验一次。

### 变异探针（副本里改源码，不入库；`/tmp/market-round-c/probes/`）

34 个，最终代码上全部被抓。句子：整句去掉、仅在有序数时才写、写位置而不是卡号、写常量 `[1]`、只写第一行、放到 JSON 与说明句之后、否定改弱（"may also be"）、"only"改"preferably"、去掉字段名、句子里带第一行标题、同号写两次、两个号用逗号；说明句：措辞改动、整句去掉；改名撤回（4 处同改）。辅助函数：永远 `[]`、不减去引用卡、降序、不去重、宽松匹配 `[ 1 ]`、不限位数、全角/其他数字也算、非文本答案抛错、脏卡抛错、布尔值当卡、浮点当卡、忽略数字字符串序号、超长数字字符串序号抛错。audit：字段缺失、读成问题而不是答案、不看引用卡；答案被删除未匹配标记、被重编号；会话存储丢掉该字段。首轮 33 个全抓；随后把标记从 `\d` 改成 ASCII `[0-9]`（与前端同口径）并补全角数字用例，涉及的 4 个重跑后全抓，再整套在最终代码上重跑：34/34。

### 变化与局限（只记事实）

- audit 字段只出现在经过 `record_log` 的路径上；轮次超时收尾（`stream_workflow` 的 `TimeoutError` 分支）的最小 audit 没有该字段。
- 只认 `[n]`（与前端同口径）；`[1, 2]`、`[1][2]` 中的各个 `[n]` 照常识别，`【1】`、`[^1]` 不是标记。
- 该字段只是上报，没有任何界面或重试逻辑使用它；回答模型仍可能写出无卡标记（只是现在会留下记录）。
- 在线只验证了 `deepseek-flash` 一次三轮会话；其他模型/提供商、其他复用形态（两条无位置、同轮摘要）在线没有再跑，只有离线测试。
- 复用消息的前缀标点由冒号改成句号；没有其他读取该前缀的代码或文档（只读取 `startswith("Verified prior evidence")`）。

## G3（2026-10-14：用户四点决定 + 对比审查里属于本草稿的缺陷）

来源：（1）用户的四点决定；（2）`2026-10-14-codex-comparison-review.zh-CN.md`（Codex 对比审查：发现 1、3、4、5 部分、6，以及"不归为新增缺陷"里的英文回指）。严格 TDD：先写红灯（新文件 `tests/test_agent_semantic_deixis.py`、`tests/test_agent_semantic_review_fixes.py`），再最小改动；每道新增保险都做了变异探针（见下）。**实时模型（真实 DeepSeek/Tavily）本轮没有重跑**。

### 用户决定 → 改动

| 决定 | 改动 | 位置 |
|---|---|---|
| 1 不接受过严的代词规则：允许有上下文依据的同义改写；校验公司/材料/时间有没有依据，不要求句式不变；真有歧义才追问 | 取消"改写必须等于原话 + 公司名"。`validate_reference` 仍检查依据（公司、source id、accession、form、期间、URL、数字、否定；依据 = 用户原话 / 用户本人早先发言 / 服务端待办 / 结果集 scope，不含标题与助手文本）。通过后，**改写补全了原话**（原话有代词且没点名公司，或改写带出了有依据的公司/编号/form/期间）且**忠实**时直接使用；否则用原话把代词换成改写里有依据的那一家公司（`AAPL的最新新闻呢`）；只有"公司说不出或不止一家"才追问。忠实 = 长度有界（≤ 3×原话+80，≤ 400）、不带原话没有的"另外/此外/并且…"、不带对流水线说话的措辞（规划阶段/检索查询/写成…地址/q 参数/忽略以上…）、不带原话没有的写操作、不含从助手文本/结果标题/选项标签原样抄来的长串（CJK 14 字、其他 30 字，用户自己写过的不算） | `semantic.py`：`downstream_question`、`faithful_rewrite`、`scope_added`、`complete_with_company`、`_borrowed`；`graph.py`：`_resolve_reference`；resolver 提示词改为"可改述/翻译，只能补用户早先发言或目录 scope 里有的公司/文件/来源/期间；两家都合理就返回 clarify" |
| 2 保留安全方向，但"一律用原话"改义：slot_reply 只有真答了待补信息才续接，新问题退出旧待办；写操作的动作与授权来自用户，但"把刚刚那两条导入"也要支持（LLM 消解对象，服务端核实真实记录、归属、权限） | (a) **新问题退出旧待办**：回复不回答服务端待办时，模型改写不被采用（`rewrite_allowed=False`，用原话；原话有代词又没公司就追问），待办内容不会漏进新问题。(b) **写操作的对象可以是指代**：`requested_action` 认"导入/import + 指代"和"把/将…指代…导入"（刚刚那两条、第二份、这份财报、those two；"这个月""刚刚那条新闻说的导入政策"不算）；对象由服务端从**本人本会话已存结果集**核实：条目 ⊆ 所选集、序号由服务端按原话定、原话说"两条"就必须正好两条、一次最多 3 条、只认 `saved_filing` 且 id 形如来源 id（网页新闻与 AI 分析摘要明确回答"不能导入"，不执行）；每条各自走原有的所有者校验、来源校验、幂等台账（键 = 会话 + 动作 + 来源 id）。"给它创建预测/导入那份资料"：原话有指代，改写补进的公司/来源必须等于**用户本人最近一条点名过它的发言**里唯一的那个（或所选集 scope 的唯一公司），多于一个就追问。没有指代的写请求仍不接受模型补的目标（G2 阻断 2 不变） | `actions.py`：`requested_action`（`_POINTER`）；`semantic.py`：`stated_count`；`graph.py`：`_deictic_write`、`_recent_targets`、`_system_action_records`、`_route_intent`、`_classify_intent` |
| 3 澄清标题按缺失信息显示 | 新原因码 `reference_company_required`（缺公司）→ 标题"请告诉我是哪家公司"；`reference_item_unavailable` → "请指明是哪一条结果"；`reference_unavailable`（缺来源/要选结果集）仍是"请选择来源或原网页"；改写类原因保持通用"请补充信息"。公司卡的回复（公司名/代码/"苹果公司"）**不再问模型**，服务端把保存的原问题与回复接起来（`它的最新新闻呢 苹果`）；这张待办带 owner/conversation/30 分钟有效期 | `graph.py`：`_clarify`、`_company_reply`；`request_resolution.py`：`is_pending_reply`、`_standalone_symbol`；前端 `chat-clarification.mjs` |
| 4 四个测试会话先保留 | 没有删、没有改库。它们不在任何普通历史列表里（历史按 `owner_hash` 过滤，这些是一次性游客身份）。见下"保留的会话" | — |

### 对比审查发现 → 改动

| 发现 | 改动 | 测试 |
|---|---|---|
| 1 P1 复用时丢季度 | 复用闸不再把期间写进合成问题（基线闸按字面匹配，"2025," 后面不是空格就失败，反过来"2025 "后有空格的年度材料会通过）；改为每个出现的年份和季度/半年（`第三季度`、`Q3`、`third quarter` 视为同一个）都必须出现在**同一行**里；`period_terms` 补英文序数季度/半年 | `test_a_quarter_the_user_asked_for_is_not_met_by_other_material`、`test_the_quarter_in_other_words_still_counts_as_the_quarter` |
| 4 P2 持久 scope 漏 form | `required_sources[].scope` 带 `form`，结果集沿用 | `test_the_form_of_a_task_is_kept_in_the_scope_of_its_saved_result_set` |
| 3 P1 旧结果跟进 + 新公司被提前拒 | 跟进的公司一致性只检查**指向旧结果的分句**里的公司（分句按，。；然后/并且/再找…切）；别的分句是它自己的任务 | `test_a_summary_of_the_old_set_and_a_search_for_another_company_are_two_tasks` |
| 6 P2 短网页不过滤 | `focus_excerpt`：放得下的页面在"查询同时问了公司和指标"时也只丢"旁边没有公司的指标句"（其余原样）；页面标题算第一句之前的一句 | 7 个新用例 + 属性测试 |
| 5 P2 general 守卫过严 | 仅对"什么是…/…是什么意思/的定义/define/what is"这类**不含公司、代码、来源、期间**的定义问题放行；含公司、编号、期间、"…是多少"的仍拒 | 7 + 6 个用例 |
| 英文回指 | `BACK_REFERENCE_CUES` 增补 `the first/second/…/last one/article/story/…` | 3 个用例 |

### 既有测试的改动（8 个函数，需求确实变了）

- `test_a_pronoun_case_that_was_translated_or_paraphrased_asks_for_the_company`（4 个参数）：**删除**，被 `test_agent_semantic_deixis.py` 的"改述被采用 / 不该有的内容不进后续阶段"取代。
- `test_a_write_request_with_a_pronoun_is_worked_on_in_the_users_words`：期望反转——唯一的公司被采用，写操作执行一次。
- `test_a_form_the_user_wrote_earlier_is_not_carried_into_a_pronoun_completion` → `…_may_complete_a_pronoun`；`test_a_form_only_the_rewrite_names_does_not_scope_the_saved_filing_read` 的前半：改写被采用，**读取仍按本轮原话**（`financial_only`、`limit=1`、无 `form`）。
- `test_the_question_rule_for_each_relationship`（纯规则）、`test_resolver_prompt_keeps_a_followup_in_the_users_words`、`test_resolver_prompt_keeps_a_pronoun_completion_unchanged_apart_from_the_company`（→ `…allows_a_grounded_paraphrase_and_forbids_borrowed_text`）：对应新规则与新提示词。
- `tests/test_agent_web_research.py::test_focus_excerpt_metric_sentences_always_come_with_the_company_beside_them`：放得下的页面也适用同一规则。

### 变异探针（副本里改源码，不入库；脚本 `/tmp/market-g3/probes/`）

脚本 `/tmp/market-g3/probes/run_all.py`：23 个，每个都先在未改动的副本里跑对照（必须全绿）再改源码，**23/23 被抓**。覆盖：借用文本、新请求/指令、新写操作、长度、指代依据、数量、条数上限、残留待办、公司卡续接、最近发言歧义、单数代词歧义、只认已存来源、记录 id 形状、复用期间检查、英文季度、只比年、form 保留、整句公司规则、短页面、标题、定义放行、放行无视公司、英文回指。过程中发现两处存活并补了用例：记录 id 形状检查、AI 分析集合被当成文件导入。

### 保留的会话（用户决定：先不删）

本轮没有碰库。可以归到本草稿的在线验证会话有 **7 个**（B-2 验收 6 个、Round C 1 个；不是之前说的 4 个），均为一次性游客身份、状态 `active`：`ecf54b8b-db73-485f-ae72-cc262e860750`、`685004fc-1540-4e09-b949-0d3a625de7ea`、`ba5823c0-3fc6-4e80-a886-bceae4273c20`、`22c5257a-e207-45f4-a147-92115216f685`、`54a6ee6d-cfae-47fc-ba34-153ae127bc34`、`c3f27e6c-f8b2-4c1c-863d-cfa98962c753`、`de7e4f0f-d09c-41ed-82cd-16fc8b95e768`。更早的 S5/S6 验收也可能留有会话，但原始输出里没有 id，**无法归属，不要按时间段删**。将来清理的范围写法：`agent_conversations.id IN (<上面的 id>)`（消息级联删除）+ 检查点行 `thread_id = checkpoint_thread_id(owner_hash, conversation_id)`（owner_hash 在库里按 id 查）；清理前由用户确认清单。

### 本轮后的行为变化（用户需知）

1. 带代词的问题，改写是翻译/改述/换问法也会被用；被拒时多数情况下直接用"原话 + 那家公司"继续，不再动不动问"是哪家公司"。
2. 省略主语的问句（"营收呢？"）会按用户早先发言补公司（改写有依据且忠实时）。
3. "把刚刚那两条导入"、"对刚刚那两份生成 AI 分析"、"给它创建预测"可用；最多 3 条；只认已保存的 SEC 申报文件；结果不明确就问。
4. 问季度不会再被年度材料回答。
5. "总结苹果刚刚的新闻，再找英伟达最新新闻"可用。
6. "什么是营收？"可直接回答（不查证据）。

### 仍然成立的限制 / 新的限制

- **忠实检查是词面的**：`ADDED_REQUEST`、`PIPELINE_TALK` 是短名单；一条不含这些词、也不是从上下文原样抄来的改述注入，词面检查拦不住。仍有的兜底：依据校验（公司/来源/期间/URL/数字/否定）、planner 输出的再校验、写操作与读操作分开授权。
- 写操作指代只认"本人最近一条点名公司/来源的发言"和所选集合的 scope；两家公司同在最近一条发言里就追问（偏保守）。
- `has_anaphora` 很宽（任何 这/那/此/该/其/它 都算）；写操作指代是否成立还要过依据与唯一性。
- 复用的期间检查是"至少有一行同时含全部年份和季度"，不是逐行；半年只补了英文。
- general 守卫仍会拦：含别名的闲聊（"apple"）、全大写英文句。
- 既有未改：网页搜索只返回 3 条；`unmatched_citation_markers` 仅审计；真实 RAG、预算耗尽、真实超时重试未在线验证。

## G3-Sol（独立审计报告的处理，2026-10-14）

来源：`/tmp/market-g3/sol-audit-report.md`（GPT-6.1-Sol：只读审计、离线探针、影子服务 10 轮游客聊天；结论"不可发布"）。按"一次集中修、跑一遍全量、交给发布"处理，没有再做审计轮。

### 处理结果

| 报告 | 问题 | 处理 |
|---|---|---|
| B1 P1 | "请不要导入这两条"、"What does import those two mean?"、"导入这些资料需要什么权限？"、"不要创建预测"被当成写授权 | `requested_action` 先看极性：动作词前（同一分句内）有否定，或整句是对该动作的询问/解释，一律不是请求（失败封闭）；"别急，把那两条导入"、"能把…导入吗？"仍是请求 |
| B2 P1 | "这个月帮我创建预测"里的"这"授权了模型补的公司 | 写对象的指代分两类：指向记录/来源（`POINTER_AT_RECORDS`）、指向公司（`COMPANY_POINTER`）；改写补的公司需要公司类指代，补的来源需要记录类指代；日历词不算 |
| B3 P1 | "第二份"/"the second filing"序号不识别；"那0条"执行；"那份"配多条 | 序号单位补份/项、英文记录名补 filing/document/file；识别到服务端读不了的序号→追问；数量 0 或不一致→追问；单数指代必须正好一条；集合指代（那些/全部）取整个小集（≤ 3） |
| B4 P1 | 自由改述带入别名表外公司（三星/Samsung）、没人写的数字、第二个请求、期间配对互换、否定对象互换 | 忠实检查增加：数字（含 100亿 = 10 billion 的量级归一与中文数字）必须是用户写过的；别名表外的常见公司名必须是用户写过的；不能比用户多分句；（年, 季度）配对必须一致；被否定的指标集合必须一致；"接着/随后/然后"算新增请求 |
| B5 P1 | "What is Samsung revenue in 2025?"、"What is revenue in 2025?"、"…for last year?"被放行无证据回答 | 定义豁免只认**整句**是"什么是X/X是什么意思/What is X?/Define X"，且 X 不含数字、相对期间/新鲜度词、已知或别名表外公司、报告期间 |
| R1 P2 | "2025年第三季度"↔"Q3 2025"、"100亿美元"↔"10 billion dollars"被当成范围/数量改变 | 期间按归一化项比较（含全角 NFKC 与英文序数季度）；数量按带量级的数值比较；期间数字不再被当成"数量" |
| R2 P1/P2 | 单数代词的歧义被"去掉公司后残差相等"的快路径绕过；模型选两家之一不被检查；模型 clarify 一律发"来源卡" | 歧义检查先于快路径：用户最近一条点名公司的发言里有两家、改写列出两家、或改写取了不在其中的另一家，都追问；模型 clarify 时服务端读原话：代词指公司且没点名公司→公司卡（`reference_company_required`），否则→来源卡 |
| R3 P2 | 公司卡的旧任务借"reference_ 类待办不算残留"混进新问题 | 待办只在"这条消息确实回答它"（公司回复/点了选项/有界槽位值）时才续接，其余都是新问题，改写不被采用 |
| R4 P1 | 复用时期间来自一行、指标来自另一行；"去年"被过滤；FY/全角 | 同一行必须同时含：所问指标、每个年份/季度、每个季度配对自己的年份；"去年/今年/明年"按服务端时钟换成具体年份；全角数字归一 |
| R5 P1 | 标题把别家公司的指标句变成被问公司的 | 指标句自己点名了别家公司（别名表内或常见公司名）就不可用，邻句/标题都不能改变这一点 |
| R6 P2 | "总结苹果刚刚的新闻和找英伟达最新新闻"被整句拒 | `TASK_BREAK` 增加"和/与/跟/及 + 动作词"的切分，`pointing_companies` 只看指向旧结果的分句 |
| R7 P2 | 英文界面里澄清卡标题是中文硬编码 | **未做**（既有问题；需要把标题映射到界面语言包） |
| 可选 1–3 | "import it/that report"、"导入以前找到的两份"漏判；用户可见的"沿原问题继续"；混合集里 skipped 行不影响 terminal 状态 | **未做**（产品决定；现状是漏判时不执行、追问） |

### 测试与验证

- 新文件 `tests/test_agent_semantic_audit_fixes.py`（54 例）逐条对应报告的复现；既有 `test_text_the_user_wrote_earlier_is_not_borrowed_text` 改为单分句（新增"不能多分句"检查后原写法不再成立）。
- 全量后端 `1549 passed, 7 skipped`；前端 72 passed；两个受保护函数逐字相同；`git diff --check` 干净。
- **没有为本批新增守卫重做变异探针**（`/tmp/market-g3/probes/run_all.py` 是上一批的，部分锚点已变）；没有重跑实时模型或 Sol 的 `audit_probes.py`。

### 仍然成立的限制

- 忠实检查仍是确定性的词面/结构检查：别名表外且不在常见公司名单里的公司、用文字写出的金额（"一百亿"以外的说法）、报告里的 FY 与日历年的区别、非常规的改述注入，仍可能通过；兜底是依据校验、planner 输出再校验、写读分开授权。
- 公司名单是静态的（约 50 个常见公司）。
- 写指代只认已保存的 SEC 申报文件（≤ 3 条）与用户最近一条发言里唯一的公司/来源。
- 英文界面澄清卡标题仍是中文（R7）。

## 函数索引（行号为当前草稿，改动后会漂移）

- `semantic.py`（S4 后）：`READ_TOOLS`/`PURPOSE_TOOLS` 19/22；`SCOPE_KEYS`/`SCOPE_TOOLS` 32/33；`_planner_prompt` 42；`STAGE_PROMPTS` 66；`call_stage` 74（预算：调用前 `used+estimate+5000>32000` 或 ≥60s；调用后 >85s）；`validate_task_query` 156；`task_clause` 182；`negation_count` 224；`period_terms` 229；`NON_TICKERS` 248；`scope_ids`/`named_urls` 256/261；`validate_reference` 265；`validate_plan` 331；`_task_scope` 369；`protected_queries` 397；`save_result_set` 417；`select_result_items` 465；`hydrate_legacy_web_sets` 476。
- `graph.py`（S4 后）：`PENDING_ACTION_TTL` 205；`_current_pending` 219；`_resumed_action` 229；`_semantic_enabled` 242；`_resolve_reference` 251；`_classify_intent` 363；`_required`（旧 planner）472；`_route_intent` 555；`_filing_scope` 731；`_task_scopes` 762；`_saved_owners` 818；`_retrieve_knowledge` 889；`_execute_tools` 1035；`_confidence_gate` 1205；`_main_agent` 1268（有 `semantic_plan` 且有成功任务直接 final，不走 ReAct）；`_clarify` 1359；`_system_action` 1419；`_fallback` 1467；`_final_answer` 1490。
- `request_resolution.py`：`extract_slots` 44；`resume_request` 83；`is_pending_reply` 124。`actions.py`：`requested_action` 28；`execute_action` 151。
- S5 新增：`semantic.py` `BACK_REFERENCE_CUES` 273、`STRONG_BACK_REFERENCE` 275、`standalone_request` 282（提示词在 `_planner_prompt` 42 与 `STAGE_PROMPTS` 70）；`graph.py` `_resolve_reference` 285 行调用 `standalone_request`，`_route_intent` 626 行 `dependent_summary`；`agent_web_research.py` `_search(include_raw_content)` 187、`_research` 的大响应重试 246。
- S6 新增：`agent_web_research.py` `METRIC_GROUPS` 131 / `ENTITY_ALIASES` 138（与门源码逐字一致，有测试）、`_query_groups` 171、`_passage_spans` 205、`focus_excerpt` 238、`_focus_source` 293，`_research` 416 在三处行构造后调用；`semantic.py` `STAGE_RETRY_BEFORE_SECONDS`/`NO_RETRY_CODES` 81–82、`_within_stage_budget` 85、`call_stage` 89（重试循环；入口预算检查改为同一个函数）、解析器提示词末句（`STAGE_PROMPTS["resolver"]`）；`agent_tools.py::query_stock_prices` 147（SELECT 含 `symbol`）。

- G2-B1 新增：`semantic.py` `NO_RETRY_CODES` 86、`TIMEOUT_OUTPUT_ALLOWANCE`/`STAGE_MAX_REQUESTS` 88–89、`call_stage` 96（`attempts`）、`TOPIC_GROUPS` 189（报告类型一组）、`CLAUSE_BREAK`/`CLAUSE_FILLER` 208–209、`task_clause` 227（尾部公司子句并入最后一个点名公司的子句）、`RESULT_COUNT` 299、`PERIOD_TEXT` 306、`_figures` 310、`validate_query_keeps_clause` 320、`_tickers`/`named_companies` 356–362、`named_forms` 380、`validate_web_query_url` 390、`validate_reference` 429（form 保留/依据、followup 公司检查）；`graph.py` `REWRITE_NOTICE` 206、web 分支的 `validate_query_keeps_clause` 调用 682、`_filing_scope` 789、`_named_scope` 809、`_task_scopes` 822、`_reuse_gate` 942、`_retrieve_knowledge` 991（summarize 分支过闸）、`_clarify` 1477、`_fallback` 1585；`agent_web_research.py` `_query_groups` 171（返回角色）、`focus_excerpt` 240、`_focus_source` 314、`_search` 391（`raw_content_truncated`）、`_research` 440。
- G2-B2 新增：`semantic.py` `SemanticError.hint` 15、`STAGE_PROMPTS["resolver"]` 末句 74、`ANAPHORA`/`COMPANY_PRONOUNS` 438/443、`has_anaphora` 453、`reference_residue` 459、`downstream_question` 473、`_UNIT`（`个` 的后随条件）509、`_zh_number` 523、`ordinal_positions` 534；`graph.py` `COMPANY_NOTICE`/`ITEM_NOTICE` 210/212、`_resolve_reference` 279（`words`/`choice_set`、`downstream_question` 340、`semantic_conflict` 345、位置推导 352）、`_classify_intent` 的 reference 检查放宽 464、`_route_intent` 的 `planner_symbols` 695 与关键词保留检查 736、`_retrieve_knowledge` 的复用行 `position`/`of` 与位置句 1124、总结闸子句、`next_action="retrieve"` 1174、`_clarify` 的 `direct` 1554；`state.py` `semantic_hint`/`semantic_conflict`/`semantic_positions` 51–53。
- Round C 新增：`semantic.py` `_CITATION_MARKER`/`_CARD_ORDINAL`/`unmatched_citation_markers`（紧接 `ordinal_positions` 之后）；`graph.py` `CITATION_NOTE`（`ITEM_NOTICE` 之后、`PENDING_ACTION_TTL` 之前）、`_bracketed`（`_reuse_gate` 之后）、`_retrieve_knowledge` 复用分支的行字段/`markers`/消息拼接、`_record_log` 的 `unmatched_citation_markers`。
- G2-A 新增：`semantic.py` `CHAT_ACRONYMS` 283、`CLOCK_TIME` 290、`METRIC_WORDS` 291、`REPORT_PERIOD_WORDS` 293、`names_company_or_metric` 297、`validate_web_query_url` 316；`graph.py` `_user_targets` 243、`_names_more_than_user` 251、`_classify_intent` 的 `_user_targets` 调用约 423、`_route_intent` 的 general 分支约 590、提前澄清约 610、web 分支 URL 检查约 669、`_fallback` 1515；`actions.py` `_word` 28、对象表 36–40、`requested_action` 45。

## 改代码前必须先补红灯的点（均已补齐并转绿）

1. 任务 `scope` 里的值不在用户文本/所选结果集中 → 拒绝且不调用工具：G1 补 NET，S4 实现（`plan_scope_unsupported`）。
2. scope 的 source_id 与 symbol 归属；同一 source_id 不得跨两只股票：S4 补测试并实现（`plan_scope_owner_mismatch`/`plan_scope_conflict`）。
3. 扩写：总检索数、合并去重、变体被拒降级：S3 完成。
4. pending 续接：owner/conversation 绑定（含检查点重开）、过期、被新问题取代、游客不能借此写（PostgreSQL 回归）：S4 完成。
5. `blocked` 审计字段：S2 完成；前端按消息级 `partially_succeeded` 显示，S4 用跨端 GUARD 锁定（不新增枚举）。

## 修复后需要同步修改的既有测试

- `tests/test_agent_semantic_workflow.py::test_selected_model_receives_four_separate_semantic_calls`：现在断言 web 任务触发第 4 次"查询扩写"调用；缺陷 5 修复后 web 不扩写，应改用一个知识任务（如 `search_material_analysis`）来保持"四个阶段各一次独立调用"的契约。
- 其余注入 `query_expander` 的既有测试不受影响（web 变体被忽略）。

## 观察（未验证，低优先）

- `validate_reference` 的否定词已改为按词计数（S4）；`protected_queries`（扩写变体过滤）仍用子串，可能多丢变体（只会降级为原问题，不会放宽）。
- 关键词类工具（`query_materials`/`search_material_analysis`）只能接 `source_id`：用户写了 accession/form 而 planner 选了这类工具时，accession/form 仍只作为关键词文本（S4 前已如此）；显式写进这类任务 `scope` 会被 `plan_scope_invalid` 拒绝。
- 新澄清原因 `reference_scope_changed`/`reference_negation_changed` 在前端用默认标题"请补充信息"（`chat-clarification.mjs` 未加专门标题）。
- `NON_TICKERS` 是证据检查专用的缩写停用表；`extract_slots` 仍把 EPS/GAAP 等当代码，planner 的 web 查询符号检查可能因此报 `plan_scope_mismatch`（S4 前已如此）。
- 语义分支不维护 `symbol_focus`/`material_focus`；代词补全只靠 `recent`（最近 8 条×800 字符）。
- `state.py` 的 `AgentRuntimeContext` 仍保留 legacy `classifier/decider`；`_semantic_enabled` 在测试注入它们时会走旧路径（这正是上次全绿却真模型失败的原因之一）。

## 发布前状态（G2-B2 之后）

只写事实；"通过测试"与"线上观察到"分开，没观察到的不记为通过。

Round C 之后的变化（复用证据多一句固定英文、行字段改名、audit 多 `unmatched_citation_markers`；回归文件 546 例、全量后端 `1398 passed, 7 skipped`）只记在"Round C"一节与顶部"当前结果（Round C 后）"，下面各小节仍是 G2-B2 之后的快照。

### 已由测试验证（不是线上证据）

- 后端全量 `PYTHONPATH=. .venv/bin/python -m pytest -q`：`1368 passed, 7 skipped`（基线 `8cfbd5d` 之上的草稿：G2-B1 后 1189）；回归文件 517 例，网页工具文件 52 例；前端 `npm run test:i18n` 71 passed、`npm run build` 通过（>500 kB 警告为既有）；`git diff --check` 干净；`deterministic_confidence_gate`、`bounded_tool_result` 与基线逐字相同。
- 变异探针 81 个，全部被抓（首轮 75 个被抓；6 个存活——`前0条` 与正常位置并存、点击选项被指向另一个集合、错误路径丢掉原问题、关键词保留读整句、关键词保留读续接后的问题、写放宽用于任何原话——补 6 个用例后重跑全被抓；更早两轮不完整的探针里另有 5 个存活，已在最终轮之前补测试：句子大小写、NFKC、URL 里的序数、`前0条`、位置排序去重）（副本里改源码，不入库）；另有 20,000 个随机输入的性质检查（序数读法只抛 `reference_item_unavailable`、位置有序去重且在范围内；问题规则只返回原话/改写/待办原话+回复）无违例。
- 安全属性（均有测试）：写操作目标只来自本轮原话或服务端待办（S4/G2-A）；planner 只能读用户原话里的 URL；网页与关键词查询保留期间/数字/否定；复用的行过基线闸；跟进只能用本人本会话、公司一致的结果集；**模型文本不再成为后续阶段的问题**（C1）；位置由服务端核实（C2）；游客写操作以 `owner_login_required` 结束；阶段重试按成本计入预算且每阶段最多 3 个请求。

### 已在线上观察到（真实 DeepSeek `deepseek-flash` + Tavily，影子 API，RAG 关闭，游客）

- 本轮 12 个聊天轮次（三处细化——`个` 的后随白名单、原话点名公司时回退原话、句末助词容忍——是在这些轮次之后加入的，未在线重验）：1、2a–2c（两条新闻→总结→解释第二条，无新搜索，答案没有"只有一条"）、3（价格 + 新闻分块）、5a（游客导入以 `owner_login_required` 结束，台账 0 行）、5b（修复后追问公司）、6（新会话"总结刚刚的内容"澄清）通过；4b 在被测构建上失败（见下）。
- Round C（影子 API，3 个聊天轮次，`deepseek-flash`）：两条新闻 → 总结 → 解释第二条；第三轮答案的 13 个内联标记全是 `[1]`，与唯一的引用卡一致，audit `unmatched_citation_markers=[]`；答案仍会复述句子的含义（措辞瑕疵）。见"Round C"。
- 更早（S5/S6，仍适用但构建不同）：新会话自成一体的问题不被误拒、规划/意图提示词、Tavily 大响应重试、摘录选句、阶段瞬时失败重试的单元与图级验证、检查点跨进程重启读取、取消后的复用隔离、无效 key 的错误。

### 未验证（NOT VERIFIED）

- 真实 RAG 下的来源范围内多变体扩写（影子库禁用 RAG；只有单元测试）。
- 回合预算耗尽、父任务失败 → 子任务 `blocked`：线上没有诱发，只有测试。
- 阶段提供商超时重试：线上没有出现超时，只有单元与图级测试。
- C1(c) 的代词补全被采用/被拒绝路径：线上没有一轮走到（4b 的 resolver 选了跟进）；"公司名称"澄清文案没有在线上出现过。真实模型的改写有多少会被拒（见下）未知。
- 4b 的放宽（只含代词的跟进 + 检索意图 = 新搜索）：只有离线测试；该修复之后没有在线轮次。
- NVDA 最新 8-K 比 10-Q 新时的财报区分（S5 起一直未在线做）。
- 前端对新澄清文案的呈现：公司名称澄清使用既有标题"请选择来源或原网页"（前端按 `reference_unavailable` 取标题，未改前端）。

### 已知局限（过度拒绝与其他）

- **（G3 已取代，见上）代词补全很严**：含代词/回指词的 `new` 请求，只有"原话 + 公司名"的改写被采用；翻译、改述（"苹果的营收情况"对"它的营收"）、加词（"关于苹果的…"）、换问法（"…是什么"对"…呢"）一律被拒并追问公司名。已容忍：的/所有格/句末助词/公司后缀/大小写/全半角/标点。弱指示字（这/那/其/它/该/此）很常见，"苹果这个季度…"这类原话已点名公司的请求不会被澄清，但会让改写失效（用原话）。
- **（G3 已取代）没有代词的省略问句不再补全公司**（"最新营收呢？"）：原话直接交给后续阶段，规划可能没有公司。
- **跟进用原话**：planner 只通过所选结果集的 scope 得到公司；带代词的跟进依赖该集合有 symbol。
- **位置读法是白名单**：`第N条/篇/则/个`（`个` 只在后跟记录名词、功能词或句尾时算）、`前N条`、`最后一条`、first…sixth/1st–6th + one/article/…、first two/N、last one；"第二个公司"这类不算位置（回到模型选择并照旧校验）。位置是已存集合内的下标，不一定等于上一条答案列表里的序号（未保存的记录不占位）。回答模型会复述字段名（"position 2 / of 3"；Round C 起字段改名，线上改为复述句子的含义，见"Round C"）。
- **关键词类任务**：前 80 个字符之后的期间/数字不算保留；主题词（最新/营收…）对这类任务仍只由闸判断（B1 记录的 (b) 只补了期间/数字/否定）。
- **G2-A/B1 已记录的**：`general` 意图的词面拒绝（定义类问题含指标词、含别名的闲聊、全大写英文句）；两个中文否定需要查询里也有两个否定词；跟进里点名的公司必须都在所选集合 scope 内；URL 以 `http` 开头的短语不再被拒（只看 `://`）。
- **既有、未改**：网页搜索只返回 3 条（用户要两条时 3 条都入集合）；一张引用卡在恢复时被判"来源已失效"（线上 1 号）；价格行没有引用卡；`semantic_intent_reference_mismatch` 仍是通用失败文案。

### 相对任务书的偏离（等决定）

1. **(d) `slot_reply`**：任务书写"保持不动"。S4 `_resumed_action` 确实未动；但模型自己标 `slot_reply` 的回复原先把改写原样当问题，等于绕开 C1，所以改为"待办原话 + 本次回复"（仅当回复是该待办的有界槽位值）或按 `new` 处理。
2. **(c) 的两处放宽**：显式写请求不走 (c)（永远用原话，否则 G2-A 的"导入那份资料→追问来源"会变成"请写公司名"并丢掉待办写操作）；原话已点名公司时，不可信的改写直接用原话而不是澄清（澄清"哪家公司"没有意义）。残余里额外容忍了的、`'s`、句末助词、公司后缀、NFKC，任务书只写"空白、标点、大小写"。
3. **跟进检查放宽两处**（在线发现 5b、4b）：显式写请求被标成跟进、只含代词的跟进 + 检索意图，不再以 `semantic_intent_reference_mismatch` 失败。
4. **planner 载荷 `symbols` 含所选集合的公司**、**总结的复用闸改用任务自己的子句**（C3 暴露的第二个缺陷）、**点击选项时校验用原问题**（原本从不可用）。
5. 前端未动，所以公司名称澄清沿用"请选择来源或原网页"的标题。（G3 已改：公司卡有自己的标题。）

