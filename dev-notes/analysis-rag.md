# 已保存 AI 分析接入知识库执行记录

## 阶段 1：适配器与同步
- 已读取设计与执行计划，并检查现有通用 SourceDocument、来源同步、材料分析严格 schema 和 disposable PostgreSQL fixture。
- Context7 官方 SQLAlchemy/FastAPI/LangChain receipts 已存在；LangChain 已解析官方 `/websites/langchain`。FastAPI/LangChain 引用在本阶段按现存接口使用。
- RED：待记录。
- GREEN：待记录。


## 阶段 1：适配器与同步（已完成）
- RED：新增适配器测试初次运行 `2 failed`，分别发现测试 fixture 的引文 end index 偏移及 future timestamp 仅依赖 SQL 过滤、无法在单元层 fail closed；修正坐标并增加行级时间校验。
- GREEN：`tests/test_knowledge_analysis.py` 的适配器用例通过；分析 sync 使用独立 keyset scope、只选择每个材料最新 UUID，旧版本手动重放仍由适配器拒绝。

## 阶段 2：检索、权限与引用（已完成）
- RED：现有自动同步周期断言因新增第四来源失败，调整契约断言至 FAQ/SEC/upload/analysis；既有 FAQ 引用测试还发现新增 source URL 访问应兼容旧 fixture，对普通来源保持原行为。
- GREEN：focused backend 43 passed（分析适配器、同步、管理 API、hybrid retrieval、Agent tool schema/timeout、chat citations）。之后增加两项分析检索断言并单独通过，当前合计 45 个目标测试通过。
- 分析 ANN 表达式使用 `source_type == "ai_analysis"`；普通材料和 FAQ 原过滤保持不变。历史读取遇到非当前分析版本时 fail closed。

## 阶段 3：管理界面与验收边界
- 前端增加“已保存 AI 分析”来源和检索类型；抽屉及聊天引用按派生分析显示标签、版本、模型、原文引文与来源链接。
- `npm run build` 成功。尚未运行真实 AMD BGE/Milvus acceptance：当前 worker 没有被指定分析 fixture 导出路径/该测试的专用环境配置；没有读取或修改生产数据，也未启动/停止服务。
- 没有新付费 LLM 调用、依赖升级或新数据库迁移。

## Primary review correction round (worker follow-up)
- 修正 citation readback：`KnowledgeDocument` 没有 `category` 列；详情从逻辑 source ID、冻结 source text 和 analysis UUID 派生原始来源类型、模型、analysis UUID 与人类可读 vN。API 将派生 excerpt 与 `original_quotes` 分开返回，前端明确标注派生内容并单独列出精确原文引文。
- 适配器复算 `material_analysis._digest` 的精确默认 JSON 序列化指纹（含 model fingerprint），拒绝被篡改的输入指纹。source hash 加入当前来源状态、URL、时间、截断/coverage 标记；SEC/upload 当前观察时间必须不晚于 now/cutoff。历史读取对当前最新版本失效时继续 fail closed，不回退旧版。
- 上传 PDF 使用 raw bytes SHA-256 校验原文件，冻结 `content_text` 与当前解析文本单独比较；真实 disposable PostgreSQL 测试覆盖 raw bytes 与解析文本不同、owner mapping 删除、parsed text 原哈希不变时的失效、版本 2 激活后旧版本不可回放、sync 周期重放幂等，以及 citation HTTP 200/来源变化后 404。
- CLI 增加 `--type/--source-id/--index` 别名；`index_pending` 和 `claim_task` 接受可选 chunk ID 白名单，先过滤再 claim/limit。空白名单不认领任务，未指定时保留原全队列行为。
- URL helper 拒绝 userinfo/无效端口，去除敏感 query 参数；前端引用类型和英中 locale 补齐。
- 验收：focused backend 59 passed；新增 opt-in `KNOWLEDGE_ANALYSIS_REAL=1` 的 disposable PostgreSQL + 本地 BGE-M3 + 独立 Milvus generation gate 通过（真实 embedding/search、analysis/original 隔离、错误 ticker、历史 cutoff、citation readback、`_restore_citations`、同版重导幂等）。FakeProvider 只用于测试分析版本生成，没有 embedding/search 或付费 LLM mock。
- 证据范围更正：上述 gate 的**检索 embedding/search 确实使用本地 BGE-M3 和独立 Milvus generation**；AMD 内容是测试内合成财报，不是真实 AMD 数据。FakeProvider 只合成保存的分析结果，不代表真实模型输出或真实公司证据。
- 前端 `test:i18n` 67 passed，`npm run build` 通过（Vite 给出既有 bundle >500 kB 提示）。组合后端运行用进程级 `PUBLIC_API_MODE=false` 绕开仓库测试环境个人登录文件要求；未读取或修改该个人文件，也未改写 `.env`。
- 仍未激活生产导入/索引或重启服务；未提交/推送。整库 clean-archive suite 仍留给主代理/后续机械门，当前根工作区包含此前多处无关 dirty files。

## Final independent-review correction gate (2026-10-12)
- RED (disposable PostgreSQL, private uploaded PDF): after indexing analysis v2, changing only `uploaded_evidence.published_at` to a timestamp after an earlier cutoff but still before current time left the old `source_hash` unchanged. This reproduced the reviewer's concrete stale-publication case.
- GREEN: adapter now hashes both the current source publication time and frozen evidence publication time; upload uses current `published_at`, SEC uses `filed_at` normalized to UTC midnight. Current publication is returned as `SourceDocument.published_at`, participates in `available_at`, and is cutoff/current-time checked. No historical-version fallback was added. Re-running the same real disposable-PG test confirms the hash changes, the old cutoff rejects the document, `_restore_citations` marks it unavailable, and citation detail returns 404; restoring the original publication makes the untouched guards usable again.
- Scoped indexing correction: the documented `sync_knowledge_sources.py --type analysis --source-id ... --index` path now derives chunk IDs only from the just-accepted source documents in the selected generation and passes that whitelist to the indexer. Empty/invalid selection returns before constructing embedding/Milvus resources; `index_pending(..., chunk_ids=set())` returns before `ensure_collection`. Unfiltered background indexing remains unchanged and the CLI ceiling remains 200.
- RED/GREEN check command: `PUBLIC_API_MODE=false .venv/bin/python -m pytest tests/test_material_analysis.py::test_saved_analysis_adapter_accepts_owner_mapped_pdf_raw_bytes_distinct_from_parsed_text tests/test_knowledge_analysis.py tests/test_knowledge_indexing.py tests/test_sync_knowledge_sources.py -q` — initial publication assertion failed because the digest was unchanged; after the fix, **20 passed**. This includes the disposable PostgreSQL publication/citation regression, selected CLI chunk capture, and empty-scope no-resource assertions.
- Minor markup correction: derived citation detail now renders its paragraph and original-quotes list as siblings instead of placing `<ul>` inside `<p>`; labels and reply layout are unchanged.
- Final focused batch: 72 passed, 1 skipped, 1 failed. The one failure is the existing `test_saved_analysis_source_url_drops_private_credentials`, which expects a URL containing userinfo to be accepted after stripping it. The current fail-closed contract rejects userinfo and is also asserted by the analysis adapter tests; this stale contradictory test was left intact rather than weakening validation or editing the test to bless the implementation.
- Clean archive full backend candidate: 698 passed, 7 skipped, 1 failed (same single stale URL assertion). This is recorded as a candidate gate result, not a passing full suite.

## Final URL compatibility correction (2026-10-12)
- Restored `_safe_source_url` legacy behavior: userinfo is stripped from the rebuilt URL; malformed URLs, invalid ports, and sensitive query-key redaction remain checked.
- `analysis_sources` independently parses the original URL with `urllib.parse.urlsplit` and rejects username/password before invoking the shared sanitizer. The strict regression now exercises `load_analysis_document` with userinfo in the source URL; the legacy helper expectation remains untouched.
- Focused final gate: legacy URL, adapter URL/port/query handling, disposable-PostgreSQL publication/citation integration, selected-source CLI and indexing tests: **11 passed** (5 existing deprecation warnings).
- Full clean-archive backend re-run pending after this correction. Previous failure receipt is preserved at `/private/tmp/market-analysis-rag-20261012/full-backend.log` (698 passed, 7 skipped, 1 legacy URL failure).
- Clean-archive gate after URL correction: **700 passed, 7 skipped, 41 warnings**; isolated environment, no `.env` copied. Previous one-failure receipt retained unchanged. Candidate frontend files (`AgentChatPanel.tsx`, `KnowledgeAdminPage.tsx`, `api.ts`, `en-US.json`, `zh-CN.json`) match the prior successfully tested candidate byte-for-byte; reused its 67-pass i18n and successful production build receipts.

## Publication gate (2026-10-12)
- Source gate: the explicitly scoped 19 tracked paths in `/private/tmp/market-analysis-rag-20261012/task-files-urlfix.txt` and 7 listed new task files were compared byte-for-byte with `/private/tmp/market-analysis-rag-20261012/clean-archive-candidate-urlfix`; see `/private/tmp/market-analysis-rag-20261012/source-hash-comparison.txt`.
- Backend: isolated clean archive command from `/private/tmp/market-analysis-rag-20261012/publication-brief.txt` completed **700 passed, 7 skipped, 41 warnings** (`full-backend-urlfix.log`). Frontend source hashes match the previously verified candidate; reused 67 passing i18n tests and successful `npm run build` recorded in `frontend-reuse-hashes.txt`.
- Focused PG/URL/CLI gate: **11 passed, 5 warnings**, recorded in `final-receipt.txt`.
- Publication commands: `git diff --cached --check`; `git diff --cached --stat`; commit `feat: index saved AI analysis in knowledge RAG`; normal push to `origin main`; `gh workflow run deploy-pages.yml --ref main`.
- Release preparation is an immutable `git archive` of the pushed commit at `data/releases/<full-SHA>-verified`; this gate does not change `data/releases/current`, restart LaunchAgents, or execute production indexing.
- Live acceptance CLI is prepared separately at `/tmp/market-analysis-rag-20261012/index-selected-live.py`; production indexing awaits root activation and is not claimed here.
