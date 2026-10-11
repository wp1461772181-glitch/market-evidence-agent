# 股票研究 RAG 第三、四章 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans，按任务执行，不再次委派。用户已选择直接执行；终端 worker 负责开发，primary 独立验收。

**Goal:** 完成可管理的研究知识库、混合检索、重排、可点击引用、拒答记录与四策略评估。
**Architecture:** 在现有 app/knowledge 增量开发，保留工具契约；新 hybrid Milvus collection 并行保留旧索引；聊天 citation sidecar 和权限 API。
**Tech Stack:** PostgreSQL / BGE-M3 / Milvus 2.6.13 / PyMilvus 2.6.13 / FlagEmbedding 1.3.5 / bge-reranker-v2-m3 / FastAPI / React。
**Spec:** docs/superpowers/specs/2026-10-11-stock-research-rag-ch34-design.md

## Global Constraints
固定选型、第三章 source/owner/time/hash/lease 规则及 8000字节完整事实预算均不放松。新 index schema 另命名/version；不删除旧集合/volume，保留全工作树既有改动。不读 .env 或存储的真实 key，不发布线上、不改 production release、不创建未获准 cloud 资源。公开依赖/模型可下载，固定revision后记录。每阶段留痕；非代码以eval验证，前端Vibe Coding。

## Review Focus
- FAQ无业务发布时间、旧chunk availability为空：首次导入时间一致且重跑修复不重复。
- BM25精确词跨owner/股票：两路检索前过滤，不能因字面精确匹配泄漏。
- RRF无cosine意义：策略特定calibration，不能旧阈值全部拒绝。
- 引用猜测UUID、刷新、源撤回：owner检查、编号恢复和权限/源复核。
- 证据缺事实或模型服务故障：拒答入池/明确失败，不能把模型自评分当事实。

### Task 1: 修复第三章基线与资源 gate
Files: app/knowledge/repository.py、tests/test_knowledge_repository.py、tests/test_rag_review_acceptance.py、deployment/milvus-compose.yml（仅如资源gate需要）、docs/knowledge-rag-ch34-api-notes.md。
Interfaces: ingest_documents(session,documents,tokenizer,generation) 保持原签名，增加独立可重复availability修复入口。
- [x] 获取 Context7 文档；确认本机版本/model权重/Milvus启动/健康；读取安全服务错误，技术矛盾先报告。
- [x] 写 FAQ null-time 初次入库/重跑修复/ANN真实非空召回测试，观察失败后修复。
- [x] 检查结构切分、候选抽取/人工审核/cursor、防重复与两个中断恢复，补欠缺测试。
- [x] 跑现有知识与工具测试；阶段日志记录实际结果。

### Task 2: 原生混合索引与重排
Files: app/knowledge/vector_store.py、indexing.py、settings.py、runtime.py；新增 app/knowledge/reranking.py、query_understanding.py；tests/test_knowledge_hybrid.py。
Interfaces: 检索 index 支持 strategy dense/bm25/hybrid/hybrid_rerank；所有 search 调用 filter一致；retrieve_faq/query_materials签名不变。
- [x] TDD：schema missing BM25检测、text双写、filter两路传递、RRF/阈值隔离、reindex重复ID。
- [x] 新 versioned collection text+BM25 sparse schema；按MCP定义 AnnSearchRequest/hybrid_search/RRFRanker；查新/旧集合schema并明确gate。
- [x] 锁定并下载 bge-reranker-v2-m3；CPU单进程有界加载/评分，Top50→10；不把模型运行阻塞event loop。
- [x] 单轮有限规范化与同义扩展，不改股票/年度/数字；首尾证据排序。
- [x] 真实小fixture四路检索，权限/时点/精确词断言；记录资源/延迟。

### Task 3: 证据、拒答与引用权限
Files: app/knowledge/retrieval.py、models.py；新增 citations.py、quality.py、api.py；app/agent_chat_stream.py、scripts/migrate_knowledge.py；tests/test_knowledge_quality.py、test_knowledge_api.py。
Interfaces: citations sidecar 独立于原工具行；SSE citations 帧包含编号映射，history恢复；/knowledge 管理端点复用写操作口令及verified session。
- [x] TDD：零证据/缺事实入low_confidence_questions；不可解析自评 fail closed；故障与empty分开；引用owner/撤回/sourcehash与编号校验。
- [x] 独立新增 PG tables 及显式 additive迁移；知识工具执行期间收集 sidecar，模型prompt仅放当前轮授权证据。
- [x] 模型生成前自评 sufficiency 使用现有provider配置不持久key；结果结构校验、有限超时，零资料直接拒答；不对普通闲聊/价格工具无端拒答。
- [x] 回答引用点击内容API，验证会话owner及资料权限；final SSE/存储/history带可恢复citation编号。
- [x] 验证single tool round + streaming契约、8000bytes与注入边界。

### Task 4: 知识库后台与前端配套
Files: frontend/src/KnowledgeAdminPage.tsx（新增）、App.tsx、AgentChatPanel.tsx、api.ts、styles.css、chat相关存档/types；tests/test_knowledge_api.py。
Interfaces: 管理页列出授权文档/块、上传复用原接口、重试/索引、候选审核、检索测试；chat clickable citations source drawer + local feedback。
- [x] API TDD：缺/错操作口令不可写；不允许用户指定其他owner；状态列表不泄漏私有正文。
- [x] frontend-design skill；已有设置增加知识库页签，loading/empty/error/retry完整状态，软停用不用物理删除。
- [x] citation点击与链接跳原文、反馈 local browser单次锁定；保留用户右对齐≤50%、assistant左对齐无框与居中阅读宽度。
- [x] npm run test:i18n/build；浏览器desktop/390px验收（只能CUA，不用外部Playwright）；若worker不能CUA留primary验收。

### Task 5: 固定评估与四策略报告
Files: 新 tests/fixtures/knowledge_rag_ch04/{corpus,queries}.json、scripts/evaluate_rag_ch04.py、tests/test_knowledge_ch04_real.py、docs/knowledge-rag-ch34-acceptance.md。
Interfaces: CLI输出data/knowledge/ch04评估JSON+markdown：策略、实际版本、split、Recall@K、MRR、分桶、faithfulness方法、失败项。
- [x] 新独立标注集难度梯度，calibration/heldout不混，原第三章fixtures保持hash。
- [x] 真实PG+Milvus+BGE+reranker四策略；synthetic数据明确标注，不伪装AAPL真实财务。
- [ ] 逐claim faithfulness eval（proxy与模型judge分开），使用确定性provider只能写模拟；真实model调用如安全配置不可用标未验，不读取/打印用户存储key。
- [x] 固定验收FAQ真实非空、型号/股票/年度BM25、权限0泄漏、无数据入池、恢复两点。
- [x] 运行 PUBLIC_API_MODE=false .venv-rag/bin/python -m pytest -q 全套及前端build，报告所有失败/skip。

### Task 6: 独立审阅与交付
- [x] worker输出变更文件、逐要求证据、命令、失败与未验证；不要git add/commit/push整棵树。
- [x] primary检查实际diff/相关独立测试、真实four-strategy结果及CUA页面；审阅留docs/knowledge-rag-ch34-review.md。
- [x] 可修复缺陷给同worker一次聚焦修复；未达质量门槛保留未通过，不通过换模型/改标注造通过。
- [x] dev-notes/ch03.md/ch04.md finish追记；本地可运行演示与版本说明，生产RAG仅验收后另行发布。

## Primary final status — 2026-10-11

Local implementation, tests and CUA acceptance passed; final independent report is `docs/knowledge-rag-ch34-review.md`. Final fresh results: backend 648 passed / 4 skipped, frontend 63 passed / build passed. Real fixed-fixture four-strategy retrieval and bounded two-case DeepSeek checks are reported separately. Formal all-claim Faithfulness remains unchecked above; two-case numeric/unit/source checks are only a bounded proxy. Dense heldout Recall@5 .667 did not reach .85, and hybrid negative-candidate count remains 5/6. Broader genuine-material evaluation is still required before production-quality claims. Production release was not switched. The accidental additive developer-DB migration and read-only audit remain disclosed in the independent report.
