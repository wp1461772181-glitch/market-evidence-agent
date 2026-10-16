# Agent routing verification / 路由改造验收

状态：**代码已推送并发布，固定评测门槛通过；最终真实线上场景 4/5，尚未全部验收。此前线上冒烟的失败全部保留**。验证日期 2026-10-16；后端与 Pages 应用快照为 `fc58db3c5e2b8ed2ffafd4384ed49a8a3c0a13c6`，后续报告提交只更新文档。

起点为 `81f62be32fbcf9dbbd329e34673aed705567da4b`（相对设计指定的 `490a176` 多一个 README 提交）。C1–C14 主实现为 `cd6e0c6`，`56b0566` 补充提案前去重，`3cab33a` 修正合成探针的 token 记账，`03221b0` 修正真实网页金额的 B/M/K 换算；`608287b` + `961bc37` 增加范围冲突澄清和一次无工具数字回答修复（中间的关键词冲突判断已移除，最终不靠词语选择任务）；`58ad20f` 将数字重写改为结构化逐句绑定，修正离任/上任的事件翻译与万亿数值校验；`fc58db3` 补充繁体金额单位的比较规范化。原工作目录的 56 个学习者文件发布前后哈希全部不变。

## 实现与独立复核

C1–C14 使用独立指代消解、分类和共享直接工具循环；A 固定先读取证据，空结果仍能继续检索；B/C 不调用工具。34 个工具由同一权限与 schema 目录声明，队列不决定公司是否可查询。所有者确认卡冻结目标、正文快照、日期和参数，并绑定所有者/会话/随机能力令牌；并行提案合并为一张卡，无效批次不出部分卡。相同 URL 或相同正文已入库时不再出新卡。

独立复核曾复现并修复：价格来源支撑无关事件、事件否定极性反转、引用与前句脱离、被拒正文泄漏至 SSE、确认后重抓正文、公司为空代码的指代替换、URL 末尾标点误拦截、并行写目标遗漏、预算异常丢失部分结果。网页保留 web_news 来源、私有范围与检索恢复校验。复核覆盖真实临时 PostgreSQL、模型原生协议和浏览器事件适配；不只依赖实现者汇报。

## 测试结果

| 检查 | 实际结果 |
| --- | --- |
| 独立安装依赖的测试解释器，完整后端 | 1270 passed / 7 skipped |
| `fc58db3` 提交导出树，逐 blob 验证后使用生产解释器 | 1270 passed / 7 skipped |
| 前端行为与翻译 | 86 passed |
| TypeScript / Vite 生产构建 | 通过；既有大于 500 kB chunk 提示保留 |
| GitHub CI | [通过](https://github.com/wp1461772181-glitch/market-evidence-agent/actions/runs/38846203880)，同一应用 SHA |
| 主工作目录 56 个学习者文件 | 发布前后哈希全部不变 |

测试数量下降的原因、旧测试保留/替换与覆盖边界见 [迁移说明](agent-routing-test-migration.md) 和 [函数清单](agent-routing-test-migration.json)。不以新测试总数宣称逐条等价。权限、服务映射与遗留词表见 [工具清单](agent-routing-inventory.md)。

## 官方 DeepSeek-Flash 配对评测

按用户指定使用官方 DeepSeek API 的 `deepseek-flash`，GET /models 已确认支持，统一启用思考、reasoning_effort=low；基线只适配相同推理参数，路由不变。此前 OpenRouter 调试不作为指定模型验收。依据：[官方思考模式](https://api-docs.deepseek.com/guides/thinking_mode/)。

固定 [50 条 JSONL](../../scripts/evaluation/cases/agent-routing-v3.jsonl) 覆盖全部 33 个必需场景。模型真实完成指代、分类和工具选择；资料与外部 I/O 为合成 fixture，数据库为独立临时 PostgreSQL，禁止生产业务写入。三条原生决策注入探针 R21/R23/R28 不计入模型质量和延迟；其余 47 条全部计入，包括失败回合。基线与改造后串行执行，模型、参数、用例相同；**evaluator digest 不同**：唯一 runner 差异是给没有真实 provider 请求的合成响应记实际 0 token。该修正不改变 47 条自然模型样本的执行分支。

| 指标 | 改造前 | 最终改造后 `fc58db3` |
| --- | --- | --- |
| 完整 50 条严格契约 | 9/50 | 50/50 |
| 47 条模型决策样本 | 8/47 | 47/47 |
| 全体 47 条 p50 | 6478 ms | 4722 ms（下降 27.11%） |
| 单源子集模型调用中位数 | 4 | 3 |
| 自然限额触发，扣除构造故障探针 | — | 0/46 |

R21/R22/R23 的停止码分别为 too_many_steps / tool_failures / no_progress，R28 参数修正通过；10 条权限/注入/条件写不变量全部满足，没有未确认业务写入，也没有 plan_* 失败。逐例结果、来源与评测摘要哈希见 [评测汇总](agent-routing-evaluation-summary.json)。

最终完整评测未通过的场景（不重试覆盖）：[]。自然限额比例按完整自然样本计算，不排除这些失败。

基线严格分数包含旧版缺少的新版工具/确认卡契约，不能解释为旧版日常使用只有 18% 成功。六条旧版从查询推导窗口但缺少新版 days_back；按旧版 freshness.window_days 做敏感性复算为 15/50，原始分数保留。延迟使用完整 47 条，未筛选成功回合；含模型服务和网络波动，是本次配对观测，不是线上 SLA。模拟 I/O 不能证明真实网页过滤质量。

### 历史失败与修正保留

较早完整评测 `deepseek-flash-after-50-v5.json` 为 47/50，其中自然模型样本 45/47、p50 4331 ms。R12 的事件陈述与 R19 的数字项目没有邻近引用绑定，分别被 unsupported_claim / unsupported_numeric_claim 拦截；原始失败保留。R21 因注入响应缺少 usage，被估算了虚构 token，先触发 turn_token_budget；修正后控制组 4/4 通过。最终在 `fc58db3` 上重新完整运行 50 条，没有挑选单例重试替换成绩；本次结果不代表以后每次模型调用都成功。

金额补丁的完整评测 `deepseek-flash-after-50-v6.json` 曾达到 50/50、p50 4125 ms，但随后真实网页冒烟仍有失败，因此最终成绩采用最新代码重新完整运行的结果，不挑选最好的一轮。

首次线上冒烟的原始脚本为 3/5：#2 正确的 $54.2B / $68.3B 与中文亿元换算被护栏误拒；金额补丁增加 B/M/K 与千分位规范化，9 条回归覆盖正确换算和错误数量级拒绝。#4 应用如实表示没有可核验资料，却被脚本强制要求引用；原设计明确允许无结果时如实回答，已修正验收脚本，无需放宽网页过滤。原始冒烟与最终重跑均保留。

金额补丁 `03221b0` 上线后两轮完整冒烟均为 **4/5**：第一轮 #1 被 unsupported_numeric_claim 拦截；第二轮 #2 在新闻时间追问中额外调用了 query_stock_prices，数字回答也被拦截。原始模型草稿没有保存，不能判定每次数字拒绝的具体原因。相同公开来源的单次收尾诊断可通过校验，该诊断不替代线上分数。随后增加结构化 refinement 范围边界及最多一次无工具数字回答修复，`961bc37` 的固定评测为 48/50、p50 4502 ms，但真实公网冒烟为 3/5：#1 数字拒绝、#5 事件拒绝。使用同一批公开引用的 native 模型诊断复现了 departure/takes charge 对应离任/上任被误拒，基线还发现 7万亿美元未被数值正则识别。最新 `58ad20f` + `fc58db3` 修正这些问题并使用严格 JSON claim/citation 重写，重新发布后的五场景为 4/5，仍有失败；所有旧失败都保留。最终失败详情：[{"spec_case": 1, "reason": "unsupported_numeric_claim", "failed_checks": ["successful_status"]}]。线上 SSE 没有保留被拒模型草稿，不能仅据停止码判定剩余失败是错误数字还是护栏误拒；不以重跑覆盖，也不据此继续猜测修改。小样本观测不证明可靠率或线上 SLA。

官方评测累计保守费用上界为 **0.748990 美元**，包含中断请求未核销的预留；最终重跑上限 0.85 美元。无 cost 时按 usage 和 [官方峰时单价](https://api-docs.deepseek.com/quick_start/pricing/) 计算，不是实际账单，不含线上冒烟；此前 OpenRouter 另有账本。key 只读入内存，不输出、不入库、不提交。

## 发布记录

- [x] 后端两环境、提交导出树、前端检查与最终模型门槛通过。
- [x] 推送并验证 [CI](https://github.com/wp1461772181-glitch/market-evidence-agent/actions/runs/38846203880)。
- [x] release.py 校验所有 Git blob 并切换快照，最新范围/回答修复补丁发布手动重启一次，launchd runs 增量 1。
- [x] 重启后预热至少 60 秒，三个健康端点在本机和公网均返回 200，checkpoint_ready / knowledge.ready 为 true。
- [x] [Pages 手动部署](https://github.com/wp1461772181-glitch/market-evidence-agent/actions/runs/38846838192) 成功，同一 SHA。
- [x] 真实公网 SSE、官方 DeepSeek-Flash 的游客 #1/#2/#4/#5/#8 均覆盖；金额补丁后两轮各 4/5，范围/回答修复补丁后 3/5，最终事件/引用绑定补丁后 4/5，#1/#2 为同一会话；执行已完成，验收未全通过。
- [x] 浏览器实际显示研究总览、预测/材料数量、监控状态及 Agent 对话入口；首次读取异常刷新后恢复，接口核对均 200。
- [x] 发布后复核 56 个学习者文件，零变化。

本次任务实际有四次发布：先发布 `00b98d3`，其 [CI](https://github.com/wp1461772181-glitch/market-evidence-agent/actions/runs/38838763461) 和 [Pages](https://github.com/wp1461772181-glitch/market-evidence-agent/actions/runs/38839172909) 通过；真实冒烟发现金额问题后发布 `03221b0`；继续复核并修复范围和回答格式后发布 `961bc37`；真实冒烟再复现事件翻译误拒后发布 `fc58db3`。每次发布各手动重启一次，**任务累计四次**，并非整个任务只重启一次。当前软链为 data/releases/fc58db3c5e2b8ed2ffafd4384ed49a8a3c0a13c6-verified；回滚目标仍为 data/releases/490a1761f3e1e205af3bc84cfed7713ee1c29fc6-verified。

| 发布 | 应用 SHA | CI | Pages | 真实游客冒烟 |
| --- | --- | --- | --- | --- |
| 1 | `00b98d3` | [通过](https://github.com/wp1461772181-glitch/market-evidence-agent/actions/runs/38838763461) | [通过](https://github.com/wp1461772181-glitch/market-evidence-agent/actions/runs/38839172909) | 原脚本 3/5，含 #4 评分条件过严 |
| 2 | `03221b0` | [通过](https://github.com/wp1461772181-glitch/market-evidence-agent/actions/runs/38841675386) | [通过](https://github.com/wp1461772181-glitch/market-evidence-agent/actions/runs/38842091137) | 两轮均 4/5 |
| 3 | `961bc37` | [通过](https://github.com/wp1461772181-glitch/market-evidence-agent/actions/runs/38843778062) | [通过](https://github.com/wp1461772181-glitch/market-evidence-agent/actions/runs/38844259207) | 3/5 |
| 4 | `fc58db3` | [通过](https://github.com/wp1461772181-glitch/market-evidence-agent/actions/runs/38846203880) | [通过](https://github.com/wp1461772181-glitch/market-evidence-agent/actions/runs/38846838192) | 4/5 |

没有 ALTER 既有生产表或执行真实所有者付费写入；网页入库/索引链路使用临时 PostgreSQL 与控制向量回归，线上游客冒烟不冒充所有者写链路验收。数值、事件及否定护栏是有限校验，不能证明任意自然语言结论都被来源完整蕴含。模型、网页可用性和引用格式仍会影响可用性。

数字回答修复只在已有证据、无待确认卡、剩余预算充足时触发一次，空工具目录、严格 JSON claims/citations、服务端逐句附引用，仍校验数字、引用和事件断言；失败保持拒绝。独立官方原生协议探针将 999 美元修复为来源中的 100 美元。departure 不再被强行等同于辞职，任命也不能证明已经上任；否定与事件强度反例均保留。refinement 与分类范围冲突时，在任何工具执行前出 missing_information 澄清卡；正常显式任务切换继续由语义阶段处理。新增 37 项回归及独立只读复核覆盖否定、元语言、明确切换、越界工具、预算与二次坏答案。

原始证据保存在本机 `/Users/wupeng/Documents/Codex/2026-10-16/market-routing-redesign-evidence`，含基线、四轮完整改造后评测、控制组、完整测试日志、四次发布/健康记录、五轮真实冒烟、一次公开来源收尾诊断和 SHA-256 清单。

English: C1–C14 is pushed and deployed at `fc58db3c5e2b8ed2ffafd4384ed49a8a3c0a13c6`. Backend checks pass 1270 tests with 7 skips in both environments; 86 frontend checks and the production build pass. The final official deepseek-flash run passes 50/50 cases (47/47 natural model-decision cases), with p50 6478→4722 ms and median single-source calls 4→3.0. CI, Pages and local/public health pass. The currency-only patch scored 4/5 in each of two real guest runs. The first scope/answer-repair patch scored 3/5 in real guest smoke despite passing fixed evaluation gates. The final event/claim-binding patch passes 4/5 guest scenarios and is not fully accepted; the remaining stop reasons and earlier failures remain documented. Earlier citation-format failures, a synthetic accounting defect and the initial currency-suffix smoke failure remain visible. Four releases required one manual restart each. Controlled fixtures and finite evidence guards do not establish universal answer faithfulness or real owner-write production coverage.
