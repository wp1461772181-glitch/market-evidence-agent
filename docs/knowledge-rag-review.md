# Dense RAG 主代理最终独立验收

> 历史阶段14记录保留。聚焦修正后的 worker 证据见文末；当前 browser API launcher 和 primary 最终复核尚未完成。

日期：2026-10-11。状态：**阶段14历史结论已由阶段15 worker 修复证据更新；仍未通过整章验收，未发布。** 下方原结果保留为历史证据；当前结论见本文末尾和 [round 15 report](knowledge-rag-round-15-report.md)。

## 已验证

- 执行助手最终全量命令 `PUBLIC_API_MODE=false .venv-rag/bin/python -m pytest -q`：563 passed、1 skipped；独立检查其实际进程日志，前端构建与 diff 格式检查通过。
- 主代理重新运行真实演示：`PUBLIC_API_MODE=false KNOWLEDGE_REAL_INTEGRATION=1 KNOWLEDGE_DEMO_SSE=1 .venv-rag/bin/python -m scripts.demo_knowledge_chat`，1 passed（36.09s）。真实本地 BGE-M3、Milvus、一次性 PostgreSQL 检索及两种中断恢复；聊天模型、会话创建/保存/历史函数使用测试替身，SSE演示证明工具协议与回灌，不证明该演示实际写入聊天历史。原聊天持久化由已有回归测试单独覆盖。
- 合成数据原20个正例召回20/20；单独裁定口径的21个正例21/21，跨owner返回泄漏0、最大工具JSON1458字节。原严格失败h14/h15/h18仍记录，裁定理由在 fixtures 的 adjudications.json，不能说原36题全部通过。
- 独立数据库探针6项：4 passed、2 failed。通过项为公开FAQ权限撤回、撤回源拒绝ack、规定元数据列存在、抽取CLI两次调用跨过前50条且无重复operator请求（52条）。失败项见下表。
- 60项原工作区基线逐项SHA-256重核；只有本轮允许修改的 main.py、规格状态和开发记录改变，没有额外用户文件变化。HEAD仍为任务开始提交。没有删除原数据/卷、没有push/deploy。

## 阻断整章验收的问题

| 问题 | 当前实现与可复现结果 | 后续修正及验收 |
|---|---|---|
| SEC引用来源版本未复核 | `adapt_sec`将正文/URL等指纹存为source_version，source_hash仍是正文hash。`retrieval._source_hash`只比source_hash，忽略source_version。在PG更改content_source_url、正文不变后，旧chunk仍被返回并引用旧地址。独立测试失败。 | 比对实际当前正文hash和确定性完整快照版本，元数据变更后旧引用必须被拦截，重新入库才可返回新地址。 |
| 人工公开发布FAQ无法召回 | `review_candidate(...scope='public')`保留候选原private scope/owner；公开文档scope=public、owner=None。检索再核对候选scope/owner时把该公开问答丢弃。独立测试失败。 | 区分私有原证据归属与人工发布范围；根据审核发布记录验证公共文档，保留原owner溯源。公开问答需真实检索正例；私有问答仍需owner正反例。 |

对应代码：`app/knowledge/retrieval.py` 的 `_source_hash`；`app/knowledge/sources.py` 的 `adapt_sec`；`app/knowledge/extraction.py` 的 `review_candidate`。

## 质量与实施限制

- 本次校准阈值 -0.9999999999999999，非常宽松，几乎不具备语义拒答筛选能力。合成小样本的高召回不能外推到真实股票材料；正式启用前需代表性语料与无关问题校准。未运行付费聊天/抽取模型，未做浏览器或生产并发验收。
- 历史cutoff检索明确返回historical_knowledge_unsupported，尚未实现设计中完整历史版本选择；默认RAG与operator调度关闭。
- queries与原标注快照语义一致。corpus与初始here-doc解码快照的差别为财报文本将字面反斜杠n恢复成实际Markdown换行；内容/数字/问题保持，不能描述为字节完全相同。
- 临时独立探针：`/tmp/market-rag-build-20261011/acceptance/test_rag_review.py`；日志 `independent-final-acceptance.txt`、`independent-real-demo.txt` 同目录。探针注册已有tests/conftest.py插件，只写独立test_*库并自动清理，未写开发库。

## 重跑演示

在项目根目录执行：

```sh
PUBLIC_API_MODE=false .venv-rag/bin/python -m scripts.demo_knowledge_chat
```

环境准备、固定模型下载、迁移、入库和索引见 `docs/knowledge-rag.md`；上述命令只演示和测试，不发布网站。完整开发过程见 `dev-notes/ch03.md`。

## 阶段15 worker 再验收（非主代理最终验收）

- 用户关键原话：“先不考虑高并发，然后先把这一轮除此之外的部分验收一下，然后如果有错误就继续改一下”。没有新增用户纠偏；按授权继续修复，未做并发/负载测试。
- 修复：SEC 检索现比较完整 `source_version`，URL 元数据变化会淘汰旧引用；人工公开发布候选按 approval status/reviewer/reviewed_at 推导公共权限并保留候选 owner 溯源。数据库检索正例、撤回反例均验证。内部历史版本读取按 cutoff、持久观察/可用时间和冻结记录创建时点选版，旧 SEC frozen text 不再与现行正文做 hash 相等判断；cutoff 前后两个版本测试通过，live 仍选最新。无时区/未来 cutoff 拒绝。
- 真实评估：新增单独 `queries-supplement.json`，原 corpus/queries/adjudications 均未改。阈值仅按 calibration 选择并绑定 generation，值为 `0.6206313967704773`、calibration F1 `0.75`。real BGE/Milvus/PG held-out 原裁定召回 `8/21 = 0.381`，补充阳性 `2/8 = 0.25`；补充 held-out 无关拒绝 `5/5`、原严格负例失败0、跨 owner 泄漏0、最大JSON 617字节。目标召回0.85未达，当前单一dense threshold不能满足召回；不得把它写成质量通过或启用RAG。
- 实际聊天验收：TestClient 经过真实 API，真实 session owner、PG conversations/messages、历史重载、工具调用及 BGE/Milvus 查询；只显式替换 provider decision/answer。两轮总计2次工具调用、2次decision+2次answer，另一 session 请求历史为404；SSE工具trace为complete。不是浏览器端验收。
- 检查：聚焦RAG相关测试32 passed；全套 `PUBLIC_API_MODE=false .venv-rag/bin/python -m pytest -q` 为570 passed、1 skipped、40 warnings；真实集成1 passed；前端 `npm run test:i18n` 28 passed；`git diff --check` passed。前端 build 遇到既存未提交聊天归档改动的 TS 错误，未改这些文件。无独立浏览器验收 launcher，无付费provider验证，无生产写入、部署或并发测试。
- 产物与日志：`tests/test_rag_review_acceptance.py`、`tests/fixtures/knowledge_rag/queries-supplement.json`、`tests/test_knowledge_evaluation_real.py`、`app/knowledge/retrieval.py`、`docs/knowledge-rag-round-15-report.md`；真实运行日志在 ignored `data/knowledge/logs/rag-evaluation.json` 与 `knowledge-chat-sse-demo.json`。原工作树用户更改保留。该记录是 worker 结果，不替代 primary 的独立最终验收。

## 聚焦修正执行证据（worker；待 primary 最终复核）

> **Primary 最新独立检查覆盖以下旧结果：** ANN allowlist 改动后的 `data/knowledge/logs/rag-evaluation.json` 记录原正例 15/20、裁定正例16/21（.7619），`quality_targets.heldout_recall_met=false`，并非下文修改前的100%。`live-rag-acceptance.json` 的 FAQ tool data=[]；现有 live test 未要求 FAQ 真正召回，因此“2 passed”不能证明此验收点。独立 `/tmp/market-rag-accept-20261011/test_faq_ann_boundary.py` 实测 FAIL：文档 `available_at` 使用首次导入时间，但 chunk 仍存 `source.available_at=None`，被 `_eligible_ann_ids` 的非空时间条件排除。这是最新入口的回归，尚不能宣称 RAG 验收完整通过。历史 ANN 候选挤占的独立探针已由 FAIL 转为 PASS，详见 `historical-ann-primary-after.txt`。

- SEC 当前 source hash 绑定观察时间、review/content 状态；candidate publication hash 绑定 reviewed_at。新增 `knowledge_documents.snapshot_integrity` additive 列，新入库快照计算不可变元数据/冻结正文摘要；缺摘要的 legacy snapshots fail closed。历史查询独立检查 owner/publication revocation，冻结文本不与今日正文做错误比较。
- FAQ 无业务发布时间时以首次导入时间作为 `available_at`，避免 Milvus 时间过滤遗漏已存 FAQ。PostgreSQL 先选当前授权或 cutoff 对应最新 eligible chunk ID，再将 UUID allowlist 加进 ANN filter；因此旧/不可用向量不能挤占 top-k。超过 5,000 个候选时以安全错误 fail closed。
- DeepSeek 官方 Chat Completions 文档没有列 `parallel_tool_calls` 参数；未发送未证实字段。若 provider 返回多项，本轮单次 decision 仅执行首项合法工具，最终输出中的 DSML/tool protocol 标记报 provider protocol error。requested model 记录为 `deepseek-chat`；不将返回 model 名称推断为 alias。
- 独立探针：新时间边界 2 passed；全套 `574 passed, 1 skipped`（72.31s），详见 `/tmp/market-rag-accept-20261011/full-pytest-final.log`。
- 真实 BGE/Milvus/PostgreSQL 评估：最终文件 `data/knowledge/logs/rag-evaluation.json`。校准只使用26条 calibration（原始+补充），heldout 不选阈值；threshold 0.4296050072，校准 F1 .9143、recall .941、precision .889；预先构造 ANN eligible-ID filter 后严格 heldout 召回变为 15/20=.75、裁定后16/21=.762、补充 heldout 正例2/4=.50，raw ANN 21/21=1.0。owner leak 0、strict-negative failures 0、JSON ≤8KB。heldout 召回目标未达到，RAG质量验收仍为部分完成；不通过heldout调阈值。
- 真实 DeepSeek 四例：1 passed（59.86s），零 mocked components；每例单决策/单工具/单最终回答且写入 SQL history，见 `/tmp/market-rag-accept-20261011/live-rag-acceptance.json`。涵盖 FAQ paraphrase、准确 synthetic FY2025 revenue、DEMB 缺资料拒答、excerpt 缺 EBITDA 金额拒答。
- 前端未修改；只读 `npm run test:i18n` 28 passed、`npm run build` 通过。独立供浏览器连接的 localhost:8015 API fixture launcher 未交付；无浏览器端到端验收、无并发/负载测试。固定栈开发数据库未写入；新 migration 尚需运维显式运行并重新 ingest/index，以生成带 integrity 的新快照。

### 最新 ANN 预筛与全量结果更新

- 加入 PostgreSQL 预先筛选 current/historical 授权 snapshot UUID，并用 Milvus ID filter 在 ANN 排序前限定候选后，再复跑全量：`574 passed, 1 skipped, 40 warnings`，65.61s；日志 `/tmp/market-rag-accept-20261011/full-pytest-final2.log`。
- 新筛选逻辑下真实 RAG evaluation + live DeepSeek acceptance：2 passed（92.35s），日志 `/tmp/market-rag-accept-20261011/real-gates-final.log`。
- 本机端口实测 localhost:8000 已被 PID 4415 监听，5174 Vite 的 proxy target 为 8000。不能让现有 5174 指向独立 8015 而不改其配置；本轮未停/重配用户的现有服务。故 8015 browser API server 未完成，frontend browser acceptance 仍为未验证状态。
# 2026-10-11 deployment regression caught before cutover

- The primary reviewer caught that `query_materials` and `query_research_faq` had been routed unconditionally through dense retrieval while the production gate was intentionally off pending the corpus quality threshold. This would have broken existing saved-data chat tools if the candidate had been switched live.
- Restored an explicit SQL-backed legacy lane for disabled RAG, with owner mapping checks for private uploads, configured owner credential verification for legacy unmapped uploads, accepted/hash-matching public SEC excerpts, and the existing response bounds. The enabled lane remains fail-closed and keeps semantic retrieval and modern evidence checks.
- Stream processing now keeps legacy results on the original answer path; citation restoration and modern evidence sufficiency gates remain limited to modern retrieval results. Regression coverage uses a disposable PostgreSQL database and mocked provider functions, without corpus indexing, model inference, or provider calls.
- This focused fix was performed after the successful remote publication but before production source-pointer switch. No production switch/restart, push, or commit was performed as part of the fix.
