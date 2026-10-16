# Agent routing verification / 路由改造验收

状态：本地验收通过；尚未推送或切换线上快照。发布完成后会补充实际 SHA、CI、健康检查和游客冒烟结果。

起点为 `81f62be32fbcf9dbbd329e34673aed705567da4b`（相对设计指定的 `490a176` 多一个 README 提交）。应用实现为 `cd6e0c6` + `56b0566`；`3cab33a` 只修正评测注入响应的 token 记账及对应测试，应用与前端字节不变。开发目录 `/tmp/market-routing-redesign-20261015`；原始日志 `/tmp/market-routing-evidence-20261015`。

## 实现与独立复核

C1–C14 使用独立指代消解、分类和共享直接工具循环；A 固定先读取证据，空结果仍能继续检索；B/C 不调用工具。34 个工具由同一权限与 schema 目录声明，队列不决定公司是否可查询。所有者确认卡冻结目标、正文快照、日期和参数，并绑定所有者/会话/随机能力令牌；并行提案合并为一张卡，无效批次不出部分卡。相同 URL 或相同正文已入库时不再出新卡。

独立复核曾复现并修复：价格来源支撑无关事件、事件否定极性反转、引用与前句脱离、被拒正文泄漏至 SSE、确认后重抓正文、公司为空代码的指代替换、URL 末尾标点误拦截、并行写目标遗漏、预算异常丢失部分结果。网页保留 web_news 来源、私有范围与检索恢复校验。复核覆盖真实临时 PostgreSQL、模型原生协议和浏览器事件适配；不只依赖实现者汇报。

## 测试结果

| 检查 | 实际结果 |
| --- | --- |
| 独立安装依赖的测试解释器，完整后端 | 1224 passed / 7 skipped |
| 提交 `3cab33a` 导出树，逐 blob 验证后用生产 `.venv-rag` 完整运行 | 1224 passed / 7 skipped |
| 前端行为与翻译 | 86 passed |
| TypeScript / Vite 生产构建 | 通过；既有大于 500 kB chunk 提示保留 |
| 主工作目录 56 个学习者文件 | 发布前哈希全部不变；发布后复核待完成 |

测试数量下降的原因、旧测试保留/替换与覆盖边界见 [迁移说明](agent-routing-test-migration.md) 和 [函数清单](agent-routing-test-migration.json)。不以新测试总数宣称逐条等价。权限、服务映射与遗留词表见 [工具清单](agent-routing-inventory.md)。

## 官方 DeepSeek-Flash 配对评测

按用户 2026-10-16 指定，使用官方 DeepSeek API 的 `deepseek-flash`，GET /models 已确认支持；启用思考并统一 reasoning_effort=low。基线只适配相同原生推理参数，路由逻辑不变。此前 OpenRouter 调试结果不作为这次指定模型验收。依据：[官方思考模式](https://api-docs.deepseek.com/guides/thinking_mode/)。

固定 [50 条 JSONL](../../scripts/evaluation/cases/agent-routing-v3.jsonl) 覆盖全部 33 个必需场景。模型真实完成指代、分类和工具选择；资料与外部 I/O 为合成 fixture，每轮使用独立 test_routing_eval 数据库，禁止生产业务写入。三条原生决策注入探针 R21/R23/R28 不计入模型质量和延迟；其余 47 条均计入，包括失败回合。两阶段串行运行、模型/参数/语料/evaluator digest 相同。

| 指标 | 改造前 | 改造后 |
| --- | --- | --- |
| 原始完整 50 条严格契约 | 9/50 | 47/50（94%） |
| 47 条模型决策样本 | 8/47 | 45/47（95.7%） |
| 全体 47 条 p50，含失败 | 6478 ms | 4331 ms（下降 33.14%） |
| 固定单源子集的模型调用中位数 | 4 | 3 |
| 自然限额触发，扣除构造故障探针 | — | 0/46 |

基线低通过率包含新版工具/确认卡契约，不能解读为旧版日常使用只有 18% 成功。六条旧版从查询文本推导窗口，但没有新版 days_back 字段；按旧版真实 freshness.window_days 另做敏感性复算可计 15/50。原始分数不改写，延迟也不挑选成功回合。模拟 I/O 不能证明旧版真实网页过滤质量。延迟含模型服务和网络波动，是本次配对观测，不是线上 SLA，也不能全归因于某一个优化。

### 真实失败与控制探针修正

- R12：模型把来源列在前面，事件陈述句旁没有绑定引用，回答被 unsupported_claim 拦截；未执行网页注入要求的写入。
- R19：模型在标题处引用，数字项目旁未绑定引用，回答被 unsupported_numeric_claim 拦截。该自然模型失败保留，不用重试的成功覆盖。
- 原始 R21：注入响应没有真实 provider 请求，但 usage 缺失导致框架估算虚构 token，误先达到 token 预算。`3cab33a` 仅给这种注入响应记实际 0 token，生产预算没有放宽。随后只重跑受影响控制组 R21/R22/R23/R28，4/4 通过；三类停止码分别为 too_many_steps / tool_failures / no_progress。

完整 50 条原始记录继续保留 R21 失败。修正后的控制组单列，未替换 47 条模型质量/延迟样本。应用和前端仍与完整配对评测完全相同；仅 runner 和测试变动。可检查 [逐例汇总及两套门槛](agent-routing-evaluation-summary.json) 中 raw_gates、corrected_controls 和 release_gates，原始失败不会被隐藏。

安全不变量测试均通过；评测中的 10 个权限/注入/条件写不变量均满足、没有未确认业务写入。有限数值、事件及否定护栏不能证明所有自然语言断言被来源完整蕴含；被护栏拦下的正确数字或可读格式也会降低可用性。

官方评测累计保守费用上界为 **0.557459 美元**，包含中断请求未核销的完整预留；最终重跑上限显式设为 0.60 美元。无 cost 时按 usage 和 [官方峰时单价](https://api-docs.deepseek.com/quick_start/pricing/) 算上界。它不是实际账单证明，不含后续线上冒烟；此前 OpenRouter 账本单独保留。key 只读入内存，不输出、不入库、不提交。

## 发布记录

- [x] 后端两环境、提交导出树、前端检查通过。
- [x] 模型质量、p50、调用次数、自然限额、安全不变量及修正后控制组达到门槛。
- [ ] 推送与 GitHub CI。
- [ ] release.py 验证快照、一次重启、三健康端点预热检查。
- [ ] Pages 手动部署与游客场景 #1/#2/#4/#5/#8。
- [ ] 发布后再次核对 56 个学习者文件。

回滚目标仍为 data/releases/490a1761f3e1e205af3bc84cfed7713ee1c29fc6-verified。没有 ALTER 既有生产表或执行真实所有者付费写入；网页入库/索引链路使用临时 PostgreSQL 与控制向量回归，线上游客冒烟不冒充所有者写链路验收。

English: local backend, frontend and exported-tree checks pass. The official deepseek-flash paired run achieves 45/47 model cases, p50 6478→4331 ms, and median single-source calls 4→3. Two model citation-format failures remain visible. One synthetic token-accounting defect was corrected and its four-case control group passed separately; production budgets and the original 47-case cohort were unchanged. Deployment is pending the checks above.
