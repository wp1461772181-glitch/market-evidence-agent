# 股票研究知识库与混合检索设计（第三、四章）

日期：2026-10-11。用户授权：“基于我们的系统参考这两套提示词写一套专属于我们系统的执行方案，然后直接开始执行吧，我就不看了，直接执行”。本轮无需额外设计批准。

## 目的与范围
为个人股票研究者提供有来源、权限隔离、可恢复的知识库，让现有 Agent 对已保存材料作语义检索，并以真实评估比较 dense、BM25、hybrid、hybrid_rerank 四策略。包含知识库后台、可点引用和回答反馈。保留现有价格/预测接口、JEV、SSE、工具签名和 FAQ/material 数据行契约。
不加入联网新闻发现、多轮 Agent Loop、指代消解、高并发、模型微调或 Oracle 部署；这些不属于本次两章。BGE 现有本地权重可用于验收，云部署另行落地。

## 技术与版本
沿用 PostgreSQL 权威库（用户已决定避免 MySQL）、BGE-M3 1024 维、Milvus Standalone 2.6.13、PyMilvus 2.6.13、FlagEmbedding 1.3.5。增加 BAAI/bge-reranker-v2-m3，必须锁定真实模型 revision。不替换技术选型。具体 API 先用官方 Context7 MCP resolve/query-docs 验证；实际 MCP JSON-RPC 已连通，证据在 /tmp/market-rag-ch34-20261011。编写 API 证据摘要并保留来源/版本。新 API 不以记忆或普通搜索替代 MCP。

## 第三章：来源、处理、索引
1. 复用现有知识模块与上传材料接口：Markdown/TXT/PDF 已提取正文、已保存且已审核 SEC 摘录、产品使用 FAQ。分析模型生成的结论不是权威财报。无正文申报目录不索引，PDF 不新增 OCR。
2. 结构切分：标题路径、完整句、列表与表格行；重叠完整句；长表复制表头及单位/期间。原模型 token 计数含 category/questions/answer 三字段：target512、overlap64、hard768；不可切长行 needs_review。其余 metadata 不加入 embedding 文本。
3. PostgreSQL 为文本、权限、源版本、状态与任务的权威；Milvus 为可重建检索索引。确定性 chunk UUID 在两库一致，auto_id=False，幂等 upsert。新 hybrid 集合独立 schema/version，不删除原 dense 集合，不原地篡改现有集合。
4. 流程：PG pending/task → embedding + Milvus upsert → 读回 hash/generation/ID 验证 → PG ready。两中断点恢复、过期 lease、重复索引不重复向量。修复 FAQ doc/chunk available_at 不一致，显式支持旧 null-time chunk 的非破坏修复；不要把全部未知材料时间填现在。
5. 对话抽取沿用 staged candidates、持久 cursor 与幂等去重；仅已完成对话的稳定产品知识，人工审核才发布。财务数字、投资结论、预测、偏好不能自动成为事实；未获 operator 配置不启用定时模型任务。提供一次执行和定时运行方法，默认关闭。
6. 知识库后台放在现有设置内独立页签：上传/选择股票/导入现有材料、文档列表（股票/来源/观察时点/状态/块数/失败原因）、原文与块详情、重试索引、撤回（软停用）、检索测试；候选审核入口。管理写入沿用操作口令；personal chat identity 不替代写权限。新增文件私有并绑定后端验证的 chat session；不自动把未知 owner 的旧上传发布公开。

## 第四章：检索与生成
1. Milvus 新集合 text 字段 enable_analyzer、chinese analyzer、原生 FunctionType.BM25 → SPARSE_FLOAT_VECTOR；dense COSINE 与 BM25 各 Top50，hybrid_search + RRFRanker(k=60)，融合候选最大50，bge-reranker-v2-m3 精排 Top10。RRF 分数不是 cosine，不复用 dense 阈值。
2. 所有检索支路在 ANN/BM25 前按 verified scope/owner、stock、source_type、可用时间及当前可用源过滤；候选必须回 PG 复核权限、版本、hash、撤回与原文后才能重排/发给 LLM。不是把过滤仅放在 dense 路。
3. Query 理解为单轮有限检索规范化与同义扩展（营收/revenue，经营现金流/operating cash flow 等），不凭改写新增股票、年份、数值或时点，不改变用户实际问题；仅检索侧扩展，不重复入库。价格等结构化工具继续 SQL。
4. 组装证据按相关性首尾放置；受现有8000 UTF8 JSON工具预算约束，完整句/表头/单位，不截数值和否定；必要时少于10块，报告实际块数。工具原 envelope/行键不变，引用走另行 sidecar，不能被 generic truncation 丢失。
5. 来源 sidecar 用 turn_id/conversation_id、编号与 chunk_id 映射，记录章节/页码或offset、源 URL、报告期/观察时点、截断覆盖；SSE 发 citations，聊天刷新可恢复。点击 [1] 打开原文/章节/来源，跳回已有材料详情或原文位置。引用读取复核会话 owner、chunk scope、当前源是否撤回，禁止凭 UUID 查看他人材料；展示失效状态不泄漏正文。
6. 生成前有明确 evidence sufficiency 判断；零证据直接拒答，故障明确 unavailable，不能当无资料；不足/低可信/模型自评不足进入 PG low_confidence_questions（原问题、会话/轮次、入口、原因、时间、owner）。模型自评不是事实概率，拒答不能仅信它；防止 evidence 注入覆盖系统提示。系统禁止宣称实时报价、保证收益、确定涨跌、星级等于上涨概率，以及保存摘录等于全财报。正常回答仍流式输出。
7. 聊天每个最终回答左下角👍/👎，选择后“已反馈”并锁定，仅浏览器采集（conversation/turn 与 feedback），刷新保留。此信号不等于已建数据飞轮，不发送 API keys 或自动训练。

## 评估与验收
固定标注集包含中英改写、公司/股票代码/型号/年度精确词、财务数字单位、跨股票、私有数据、时点、缺证据与截断。保留旧 corpus/queries/adjudications 原始内容，新集另文件；calibration 和 heldout 分离，不用 heldout 调阈值。
真实 Milvus/BGE/reranker/PG 跑四策略，报告 Recall@K、MRR、分桶、样本数、失败项及耗时。Faithfulness 使用实际回答+逐claim来源验证；规则核验须标 proxy，LLM judge 须标真实调用与局限，未运行不得伪造数字。先目标 heldout Recall@5 >=0.85，财务数字/单位/期间引用一致100%，跨owner泄漏0；达不到如实保留未达标，不调整标注凑分。
另验收：真实模型召回换说法 FAQ；两中断恢复；BM25 精确词；引用点击与刷新定位；无知识拒答且PG入池；反馈锁定；管理页上传→ready→检索；390px/桌面无溢出且保留原聊天布局。模拟provider和真实provider测试分别报告。
生产数据库和在线 release 不自动切换：先隔离 test 数据库/本地端口验收，不能把未验收RAG推上线。不删除现有 DB/集合/volume；一次性test资源按已有安全test_* fixture管理。

## 工作留痕与交付
逐阶段追加 dev-notes/ch03.md 与 ch04.md，记录用户原话、产物、真实用户纠偏（本轮无）、错误与返工。后端新增行为 TDD，Prompt/数据用标注集，前端按用户 Vibe Coding 例外直接改并实际看页面。执行 worker 实现，primary 独立验收。交付运行命令、测试/真实评估报告、笔记路径与明确未通过项。
