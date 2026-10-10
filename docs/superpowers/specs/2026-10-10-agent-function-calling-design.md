# Agent 单轮 Function Calling：设计草案

日期：2026-10-10（Asia/Kuala_Lumpur）。状态：用户已批准；实现与本地独立验收完成（2026-10-10），部署结果见 dev-notes/ch02.md。

## 1. 目标与当前代码

让用户在现有 Agent 对话页直接查询股票研究数据，看到工具执行状态、回答中的工具徽章和可恢复的聊天记录。模型决定是否调用工具；后端执行并将真实结果交回模型。

已核对基线提交 `9c2dbba605387318ddc9c128ee13114e29902b09`：

- 已有 FastAPI、SQLAlchemy、Docker PostgreSQL、股票行情、SEC 库存、上传材料及不可变预测版本。
- `app/agent_chat_api.py` 当前只有 Hello 测试和完整 JSON 聊天返回，没有 Function Calling、SSE 或聊天数据库表。
- `frontend/src/AgentChatPanel.tsx` 已有独立聊天页、模型切换、取消请求和页面内存中的个人 API key。
- 工作目录中原有 58 个修改或未跟踪文件，主要是教学注释和文档；实施必须保留。

电商模板的可应用部分是工具注册与执行、模型回灌、SSE、聊天落库和轨迹展示。数据库沿用现有 PostgreSQL；不迁移 MySQL，不新建订单、商品、物流、工单业务，也不向用户展示随机生成的金融数据。

## 2. 第一版工具集合

所有业务工具用 LangChain `@tool` 定义，显式 Pydantic 参数 Schema，注册到固定白名单。

| 工具名 | 参数 | 数据来源与返回边界 |
| --- | --- | --- |
| `query_stock_prices` | `symbol`, `limit=5`（1–30） | 已保存行情；返回交易日、价格、来源与抓取时间。没有行情返回空数据，不能称为实时股价。 |
| `query_latest_forecast` | `symbol` | 当前预测工作台对应版本；返回版本号、判断时间、模型状态、已有概率与简报摘要。多条独立预测时说明选取依据，不将实验结果写成确定收益。 |
| `query_materials` | `symbol`, `keyword`, `limit=5`（1–10） | 现有材料库存；SQL LIKE 查询标题或已有正文，返回有界摘要、来源、日期及记录标识。没有结果如实返回。 |
| `query_sec_filings` | `symbol`, `form`（可选 10-K/10-Q/8-K）, `limit=5`（1–10） | 已保存 SEC 库存；按提交日期排序，返回表单、日期、官方来源与已有正文摘要。明确这是本地最新记录，不触发外部抓取。 |
| `query_research_faq` | `keyword`（1–80 字） | 新增研究 FAQ 表；SQL LIKE 搜索问题与分类，最多 5 条；保留关键词检索漏召回。 |

股票代码复用已有标准化逻辑，包括 AMD 与 BRK.B/BRK-B。SQL 使用绑定参数，LIKE 中 `%`、`_` 等按普通文本转义；模型不能提供原始 SQL、任意 URL、文件路径或注册新工具。

模型决定工具及参数，不能由后端根据“行情”“财报”等关键词硬编码假装模型选择。

此次先提供查数据能力。同步 SEC、初始化行情、创建预测、保存评级记录等会修改共享研究状态的操作继续通过既有界面及操作口令执行；本版不将其加入聊天工具白名单。

## 3. 单轮调用与真正的流式回答

流程：用户消息 → 模型携带五个工具 Schema 判断 → 至多执行一个业务工具 → 工具结果回灌 → 禁止工具调用的最终回答流。

- 每个用户轮次最多一个业务工具执行；不建立 Agent Loop。
- 无需工具时可以直接回答，但最终回答仍采用服务方真实流式响应，不把完整文本人工拆成假 token 流。
- 工具决策阶段和回答阶段最多各一次模型请求。回答阶段使用禁用工具的协议设置，不继续响应新的工具申请。
- 若模型提交多个工具请求、未知工具或非法参数，不执行不合规申请；返回有界错误信息，并按服务方协议闭合工具调用记录后收敛。
- 工具统一返回 `ok`、`data`、`error_code`、`source/as_of`。只对只读工具的临时超时或连接故障重试一次；参数错误、不存在的工具和空数据不重试。
- 单次工具执行期限 5 秒，数据库查询有独立超时；超时后不会继续重入或开始写操作。外部模型请求保留既有超时、容量与响应大小限制。
- 工具结果是数据而非指令。来源文本要求模型不要覆盖系统约束；白名单中没有任意代码或写入工具。

新增 `POST /agent-chat/stream`，保留 `/hello` 和旧 JSON 路由的兼容性。使用 POST fetch 读取 SSE，避免将 API key 放入 URL；后端使用 FastAPI `StreamingResponse` 与 `text/event-stream`。

SSE 事件：`conversation`、`status`、`tool_start`、`tool_result`、`delta`、`done`、`error`。工具状态先出现，随后助手气泡随 `delta` 更新；只在 `done` 后标记本轮成功。取消、断线和服务方错误均不能伪装成功。

扩展现有请求体大小限制、容量限制及准确 CORS 配置，覆盖新路由。流建立后的错误走 SSE 错误帧，流建立前的错误返回正常 HTTP 状态。所有路径释放请求槽和数据库会话。

## 4. 数据模型与聊天归属

在现有数据库中增加三个独立表，不修改或删除历史研究表：

- `agent_faq`：主键、问题、答案、分类。仅为研究产品说明灌入可重复执行的固定 FAQ 数据。
- `agent_conversations`：UUID、浏览器会话标识的哈希、处理状态、创建/更新时间。浏览器身份不是已登录账号，不将其写成真实用户身份。
- `agent_messages`：UUID、会话外键、轮次标识、顺序号、`user/assistant/tool` 角色、内容、工具调用申请 JSON、`tool_call_id`、完成状态、创建时间。

保存用户消息、assistant 工具申请、tool 结果及最终 assistant 内容；不存储 API key、Authorization、完整请求头或服务方凭据。中断保存明确的 cancelled/failed 状态与已有内容，不冒充完整回答。

当前网站公开，聊天正文不能因此成为公开可枚举的数据。浏览器生成不可预测的聊天会话凭证，保存在当前浏览器 sessionStorage；服务端只保存哈希。恢复会话和读取消息同时校验该凭证与会话归属，跨浏览器返回未找到。新建对话创建新会话；没有全局聊天列表接口。

新流式聊天路由只允许写入该浏览器的聊天记录，不借此绕过研究数据的 `X-App-Access-Key` 保护。聊天上下文由已验证归属的数据库记录构造，保留完整工具调用/结果配对，并遵守现有上下文窗口及请求字节上限。

## 5. 页面改造

按照用户的前端例外要求直接迭代，不对聊天页额外套 brainstorm、TDD、code review；仍进行实际浏览器验收。

- 保留当前纸白/深绿色风格、右下角快捷选模型与后台配置。
- 发送后气泡展示“选择工具中 / 查询行情中 / 组织回答中”等明确状态。
- 助手气泡展示工具名称及成功、失败、未命中状态的小徽章，可查看安全裁剪后的参数和结果摘要。
- 建议问题改为真实入口，例如“查看 AAPL 最近行情”“AAPL 已保存的最新财报有哪些”“可信度星级是什么”。
- 模型或 key 切换、取消、刷新及新建对话不能让迟到的数据写到其他会话。刷新仍要求重新输入个人模型 API key。
- 告知用户消息与工具轨迹将保存到服务端；提示在页面对应流程内，不能只写在开发文档中。

## 6. 验收与评估

后端可单测部分按 TDD：先看到真实失败，再实现并验证；纯 Prompt/FAQ 数据用带标签的评估样例。前端直接改造后进行可见浏览器验收。

验收示例：

1. “查看 AAPL 最近五个交易日的行情”：工具申请指向 `query_stock_prices`，气泡有徽章，回答数字/日期与固定测试数据库一致。
2. “AAPL 已保存的最新财报有哪些”：调用 `query_sec_filings`，回答来源及提交日期与库存一致；不能声称刚访问 SEC。
3. “可信度星级是什么”：`query_research_faq` 查到固定 FAQ 并回答。
4. 固定关键词对照：FAQ 使用“费用”，`keyword='费用'` 命中，`keyword='价钱'` 不命中；记录 SQL LIKE 的预期漏召回。实模型可能自己改写关键词，必须另记模型端到端结果，不能预先宣称所有同义问法必然未命中。
5. 没有行情/材料/预测的 AMD：工具返回空结果，模型明确无已有记录，不生成随机行情或虚构简报。

必要回归：非法参数、多个工具、未知工具、工具超时与重试次数、流中断、工具执行失败、错误帧、聊天持久化、会话隔离、凭据不落库、旧 Hello/JSON 接口、原有写接口仍受保护。SSE 必须证明工具状态帧先于回答、连续上游增量被逐步输出，而非最后一次性返回。

浏览器演示将使用显式标注的本地服务方模拟及固定数据库完成无付费验收；真实 DeepSeek/OpenRouter 工具选择和逐段流式能力单列结果，只有使用有效 key 实测后才记为已验证。

## 7. 文件分工与交付

拟新增后端 `agent_chat_models.py`、`agent_tools.py`、`agent_tool_executor.py`、`agent_chat_stream.py` 与对应测试；现有 `agent_chat_api.py` 保留配置校验和兼容入口。实施时按现有代码边界调整具体拆分，避免把整套流程塞进主入口文件。

前端增加 SSE 读取与状态更新，修改 `AgentChatPanel.tsx`、`api.ts`、样式及中英文文案。FAQ seed 与评估样例独立文件；演示命令区分真实服务和模拟模式。

开发过程持续追记 `dev-notes/ch02.md`。最终交付运行/演示命令、测试与样例评估结果、浏览器截图、未验证的外部边界及该文档路径。

本章不做：向量检索、RAG、多轮自动工具循环、多 Agent、重训 Jev、自动学习用户评级、外部站点抓取以及数据库技术迁移。

## 8. 文档核对与后续流程

2026-10-10 已通过 Context7 的公开文档 API 按“先解析 library ID，再查询 docs”核对：

- `/websites/langchain_oss_python_langchain`：`@tool`、Pydantic `args_schema`、模型 tools 绑定与 tool_calls。
- `/websites/fastapi_tiangolo`：`StreamingResponse`、异步生成器、取消与 `Request.is_disconnected()`。

对应原始官方文档：[LangChain tools](https://docs.langchain.com/oss/python/langchain/tools)、[LangChain models](https://docs.langchain.com/oss/python/langchain/models)、[FastAPI StreamingResponse](https://fastapi.tiangolo.com/advanced/custom-response/)。实施前还需针对最终锁定的安装版本及 DeepSeek/OpenRouter 协议核对兼容性。

用户已批准本设计并要求开始开发。后续按 writing-plans、隔离实施、后端 TDD、数据评估与最终验收执行；聊天页面遵循用户明确的流程例外。
