# Agent 单轮 Function Calling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking. User has approved the design and explicitly authorized implementation; execute continuously using the existing planner-worker-review terminal-worker route.

**Goal:** 在现有股票研究聊天中加入五个只读工具、真正的 SSE 增量回答、私有会话持久化与工具轨迹。

**Architecture:** LangChain @tool 固定注册表使用现有 PostgreSQL 数据。独立工具执行器校验参数、限制期限并只重试一次临时错误；流编排器最多两次模型请求和一个工具执行，独立会话存储校验浏览器凭证归属。前端沿用快捷模型选择器，以 POST fetch 接收状态及增量，恢复数据库中的会话。

**Tech Stack:** Existing FastAPI / SQLAlchemy / PostgreSQL / httpx / React / TypeScript; LangChain core @tool and OpenAI-compatible tool schemas. No database migration to MySQL, no real credentials in artifacts.

**Spec:** docs/superpowers/specs/2026-10-10-agent-function-calling-design.md

## Global Constraints

- 保留旧 Hello 和 JSON 路由及研究写操作口令；API key 仅本次请求使用，不写入聊天、日志、浏览器持久存储。
- 一轮最多执行一个工具，决策和最终流各至多一次模型请求；最终禁止工具调用。未知/多工具/非法参数闭合协议后收敛。
- 参数 Schema 严格验证；查询绑定参数，LIKE 转义；工具 5 秒，DB 独立超时；仅临时只读失败一次重试，取消不遗留执行。
- 新增 agent_faq / agent_conversations / agent_messages；幂等固定 FAQ，不改删研究数据；浏览器身份哈希，恢复/读取必须校验归属，无全局聊天列表。
- SSE conversation/status/tool_start/tool_result/delta/done/error，真实上游逐段转发；错误/取消保存明确状态；保留 32 KB 输入、8 请求槽、25 秒服务方超时和有界上下文/回答。
- 前端按用户例外直接 Vibe Coding，不加 frontend brainstorm/TDD/code-review；需可见浏览器验收。其余后端 TDD，FAQ/Prompt 标注评估。
- 隔离副本实现，主仓库原有注释和未跟踪文档不纳入产品提交；主模型独立验收合回。

## Review Focus

1. DeepSeek reasoning_content / OpenRouter reasoning_details 在工具回灌与后续历史中保留需要的协议字段，且不泄漏到 UI。
2. 断流、取消和错误释放模型连接/容量，存储 partial/cancelled/failed，不输出成功 done。
3. 完整轮次裁剪上下文，不截断 assistant/tool 配对，不带失败轮次的未闭合申请。
4. 同一会话并发请求不交错写入；未知归属返回 404，包括跨浏览器 POST 和 GET。
5. SQL LIKE 通配符、空结果、超长工具响应及 provider 错误体不会误导回答或泄漏凭据。

### Task 1: 数据、工具与执行边界

**Files:** Create app/agent_chat_models.py, app/agent_chat_store.py, app/agent_faq_seed.py, app/agent_tools.py, app/agent_tool_executor.py; modify pyproject.toml and app/main.py startup; tests/test_agent_tools.py and tests/test_agent_chat_store.py.
**Interfaces:** tools registry exposing name -> LangChain StructuredTool, OpenAI-compatible tool_schemas(); async execute_tool(name: str, arguments: dict) -> dict with ok/data/error_code/source/as_of. Store exposes create/verify conversation, append ordered turn messages, bounded complete history, finish turn, owned messages. Exact internal store signatures may follow existing SQLAlchemy patterns; lock them in notes before Task 2.

- [ ] Write failing tests for all five tools, normalization AMD/BRK.B, bound limits, escaped LIKE, idempotent FAQ, workspace forecast selection, owned retrieval, ordered call/result history and absence of credentials.
- [ ] Observe RED and record actual command/result in dev-notes/ch02.md. If child sandbox cannot reach PostgreSQL, run isolated mock-based unit RED checks and explicitly record DB tests blocked for parent fresh acceptance; never claim blocked checks passed.
- [ ] Implement three tables, seed, read-only @tool functions and executor. Prefer SQLAlchemy AsyncSession for cancellable database work; async psycopg is available. SET LOCAL statement_timeout for PostgreSQL queries; do not use orphaned to_thread work after wait_for.
- [ ] Verify GREEN: known/unknown tools, bad args, multi-call rejection delegated to orchestration, one transient retry only, cancellation cleanup. Commit only owned files; record outcome.

### Task 2: 单轮模型编排、SSE 和持久化

**Files:** Create app/agent_chat_provider.py, app/agent_chat_stream.py; extend app/agent_chat_api.py and app/main.py; tests/test_agent_chat_stream.py (plus existing agent_chat tests).
**Interfaces:** POST /agent-chat/stream body provider/model/api_key/message/conversation_id optional; X-Chat-Session unpredictable browser credential required. GET /agent-chat/conversations/{id}/messages also credential-gated. Response events defined in spec; tool_start includes call_id/name/arguments; tool_result includes call_id/name/result; delta text; done turn/conversation status. Align frontend with exact payload in notes.

- [ ] Write failing tests for event ordering, incremental provider SSE, no-tool path still streamed, all tools disabled on second request, malformed/multiple/unknown calls not executing, complete call/result persistence, reasoner field passthrough, private ownership and same-conversation concurrency.
- [ ] Run RED; implement fixed provider endpoints with bounded response parsing, real httpx upstream stream, safe error mapping and two requests maximum. Do not expose user-selectable URLs. Preserve existing legacy routes and their mocks.
- [ ] Implement cancellation/failure terminal persistence and cleanup, server-side complete history budget, scoped conversation exclusion, early HTTP errors / midstream SSE errors, safe validation/body cap/CORS/public-chat allowlist.
- [ ] Verify GREEN with negative failures, cancellation, UTF-8 limits, no-store, public chat permitted but research writes 401. Commit and update notes.

### Task 3: 聊天页接入、标注样例与交付

**Files:** frontend/src/AgentChatPanel.tsx, frontend/src/api.ts, necessary helper/types/style/i18n files; scripts/demo_agent_chat.py (or clearly named demo equivalent), tests/fixtures/agent_chat_cases.json; README.md, dev-notes/ch02.md.
**Interfaces:** consume Task 2 events using POST fetch and sessionStorage credential + conversation pointer. API key stays memory; restored transcript must not require a model key for ownership retrieval. Preserve model verification lifecycle and picker.

- [ ] Directly implement chat pending phases, real delta display, per-assistant tool badge and expandable bounded args/results, truthful cancelled/failed states, safe restore/new chat/stale event guards, new research suggestions, visible server-storage notice.
- [ ] Create a local demo with explicitly labelled mock provider outside production routes, fixed disposable PostgreSQL dataset; never insert mock rows into developer/production DB. Exercise AAPL prices/SEC/FAQ, fixed keyword 费用 hit vs 价钱 expected miss, AMD empty with labelled evaluation output. Paid provider not required and must be reported unverified.
- [ ] Run backend full suite and frontend existing helper checks/build; parent reruns fresh if child sandbox blocks DB or dependencies. Parent performs desktop/mobile browser acceptance, incremental display, badges, restore/cancel/newchat/modelpicker; frontend exempt from code-review gate.
- [ ] Update notes with actual RED/GREEN, docs sources, commands, counts and limits. Return files/diffs, demo command, results, any spec deviations; no claimed deployment or paid-provider success without evidence.

## Self-review and authorization

All eight spec sections map to the three tasks. Review Focus cases have explicit backend negative checks. Latest user instruction approves implementation of the design; their no repeated yes/no confirmation rule overrides the skill's redundant plan review prompt. Database and dependency selection remain fixed; if truly impossible, stop and report rather than substitute. Native managed worktree cannot resolve the current /Users/wupeng task repository, so a clean local clone is the isolated workspace; root changes are preserved in an external hash/backups manifest.

## Final acceptance status (2026-10-10)

Implementation of Tasks 1–3 and independent local acceptance completed. The checklist above preserves the original proposed TDD order; it is not a claim that every added acceptance test was individually observed RED. Initial module RED and later six real PostgreSQL/protocol acceptance failures were recorded, then corrected and verified GREEN. Actual final results: backend 489 passed, frontend 10 passed plus build, labelled mock endpoint evaluation 5/5; desktop/mobile/cancel/restore/model picker observed. Integration and deployment evidence is appended to dev-notes/ch02.md only after completion.
