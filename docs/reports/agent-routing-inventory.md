# 工具与词表清单 / Tool and vocabulary inventory

目录由 `app/agent_tool_catalog.py` 集中声明；A、D 共用，游客按权限裁剪。

| Tool | Level | Guest | Confirmation | Description |
| --- | --- | --- | --- | --- |
| `query_stock_prices` | read | yes | no | Read saved daily stock prices; this is not a live quote. |
| `query_latest_forecast` | read | yes | no | Read the latest saved research forecast snapshot. |
| `query_materials` | read | yes | no | Find saved uploaded and official research material using a natural language question. |
| `query_sec_filings` | read | yes | no | Read saved SEC filing inventory without contacting SEC. |
| `query_research_faq` | read | yes | no | Answer a natural language question from the approved research FAQ. |
| `query_material_analysis` | read | yes | no | Read the latest saved AI analysis of a material; this is an AI summary, not original filing text or an official fact record. |
| `search_material_analysis` | read | yes | no | Search saved AI analysis as derived material; verify claims against original sources when asked. |
| `web_research` | read | yes | no | Search public web sources or read a public HTTPS page. ALWAYS set days_back for news freshness: recent/latest=7, half a month=15; preserve the interpreted request window. Supply company as named in page text. Search snippets are leads, not evidence. |
| `read_workspace` | read | yes | no | Read a saved forecast/version timeline, analysis history/original, learning status, knowledge chunks/candidates or importable catalogue. Private records remain owner-scoped. |
| `start_historical_replay` | costly | no | yes | Propose a retrospective replay for a specific past decision date; this is not a forward prediction. Confirmation required. |
| `revise_forecast` | costly | no | yes | Propose a manual revision of an exact saved forecast version with exact saved source references. Confirmation required. |
| `retry_document_index` | costly | no | yes | Propose retrying embeddings for an exact failed knowledge document. Confirmation required. |
| `translate_content` | costly | no | yes | Propose paid English translation of one saved analysis or forecast brief. Confirmation required. |
| `review_knowledge_candidate` | costly | no | yes | Propose approving or rejecting one saved knowledge candidate with the named reviewer. Confirmation required. |
| `import_sources` | costly | no | yes | Propose importing exact FAQ/upload/SEC/analysis IDs from a returned catalogue. Confirmation required. |
| `search_knowledge` | read | yes | no | Search saved knowledge and filings before public research. Empty results do not stop web research. |
| `query_system_status` | read | yes | no | Read existing jobs, monitor, evaluation, replay or indexing status. Never starts work. |
| `query_research_queue` | read | yes | no | Read the owner queue, or ask the guest browser to display its local queue. |
| `add_to_research_queue` | reversible | yes | no | Add a symbol explicitly requested by the user; owner database or guest browser only. No confirmation. |
| `remove_from_research_queue` | reversible | yes | no | Remove a requested symbol from the queue, keeping all materials and history. No confirmation. |
| `select_stock` | client | yes | no | Select the current stock in the browser without fetching new data. |
| `open_page` | client | yes | no | Open a named existing workspace page without changing data. |
| `ask_user` | card | yes | no | Ask for a missing entity or let the user choose between options. Use when the reference is ambiguous. |
| `use_result_set` | card | yes | no | Select exact one-based positions from a returned result set; summarize or propose a confirmed import/analyze/index. |
| `fetch_sec` | costly | no | yes | Propose fetching a saved SEC filing original. Server confirmation required; optional accession or latest form. |
| `refresh_sec` | costly | no | yes | Propose refreshing the original SEC document. Confirmation required. |
| `analyze` | costly | no | yes | Propose paid AI analysis of an exact saved source. Confirmation required. |
| `import` | costly | no | yes | Propose importing one exact saved source into the knowledge library. Confirmation required. |
| `index` | costly | no | yes | Propose building pending knowledge embeddings for an exact source or a bounded batch. Confirmation required. |
| `forecast` | costly | no | yes | Propose a new research forecast, using only user-requested saved source IDs. Confirmation required. |
| `ingest_web_source` | costly | no | yes | Propose saving a returned public URL as web_news. Only if the USER requested saving, including conditional saving. newer_than refers to the saved-evidence ordinal; server compares publication dates. Confirmation required. |
| `initialize_prices` | costly | no | yes | Propose fetching historical daily prices for a symbol and explicit days. Confirmation required. |
| `scan_official` | costly | no | yes | Propose scanning official SEC filings for one symbol; may enqueue eligible existing automatic research. Confirmation required. |
| `review_filing` | reversible | no | no | Record the owner requested accepted/rejected relevance review for a saved SEC filing. Never delete data. |

## Service mapping / 实际服务映射

原有八个读工具使用 `agent_tools.TOOL_REGISTRY`。新增工具在 `agent_workflow/tools.py` 和 `workspace_tools.py` 调用既有行情、SEC、材料分析、预测、回放、知识管理和翻译服务；不重写预测、Jev 或 RAG 算法。所有写级操作只从 `consent.execute` 进入业务适配器。

## Vocabulary audit / 词表审计

| Location | Purpose | Used for routing? |
| --- | --- | --- |
| `routing.py`, `direct.py` classifier/reference prompts | Model structured interpretation | Model only |
| `authorization.py` | Check that the user actually requested the proposed operation and did not negate it | no |
| `evidence.py` period/entity/metric checks | Validate a retrieved claim against typed requested scope | no |
| `agent_web_research.METRIC_GROUPS` | One shared metric synonym table for excerpt selection and validation | no |
| `freshness.py` | Explicit named-span compatibility fallback only | no |
| `semantic.py` ordinal/count/UUID/number parsers | Ground returned IDs, positions and numeric scope | no |
| `company_directory.py` | Legacy display suggestions; not imported by the production direct workflow, web filter or evidence gate | no |
| `actions.requested_action` | Retained compatibility guard/parser; not called by the direct production graph | no |

The former planner graph and `plan_*` runtime reasons are removed. A queue snapshot is not an authority for company recognition. No fixed company alias table participates in production route, filter or evidence validation.


### 词表审计补充边界

- `semantic.py` 的中英数量、序号、金额量级、FY/季度、UUID/accession/URL 与公司名称语法：仅做范围解析/校验。
- `direct.py` 的禁止操作、否定、协议标记与 transient 原因码：安全、协议和失败控制；不选择 A/B/C/D 或搜索时间。
- `evidence.py` 的事件词与局部否定：拒绝引用不支持的事件及极性反转；不选择工具。
- `agent_web_research.py` 的实体分词、句子边界、通用停用词、链接及 challenge/login/boilerplate 标记：正文提取、相关性和抓取护栏。
- `actions.py` 的 `_ANALYZE_PHRASE`、`_NEGATED_ACTION`、`_MENTIONED_ACTION`：保留旧 API 的兼容解析，新的 direct 图不调用 `requested_action`。
- `agent_context_summary.py` 的时钟格式、`ai_content_localization.py` 的数字占位符：摘要范围校验和翻译保数。
- `knowledge/extraction.py` 的隐私模式、`sec_filings.py` 的安全文件名/编号/附件格式：既有不可信输入护栏。
- 模型/预测/训练模块里的枚举、列名、样本股票集合，以及 `agent_faq_seed.py` 的 FAQ：类型校验与固定数据，均不参与 agent 路由。
- `knowledge/query_understanding.py` 仍有旧 RAG 查询翻译同义词；它不是 agent 的指标闸门。RAG 内部属于原规范明确不改的范围。
- `news_discovery.py` 的旧 CLI-only 公司/产品/标签映射也已移除，改为显式 `DiscoveryScope` 入参。默认仅按 ticker 字面量查询；RSS 标签未配置时返回 unsupported。AST 防漂移测试覆盖整个 app/，旧探针测试通过显式合成配置保留其过滤不变量。
