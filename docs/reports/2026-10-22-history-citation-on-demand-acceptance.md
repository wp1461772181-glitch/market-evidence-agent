# 历史引用按需回查验收记录

日期：2026-10-22。工作区分支：`g9-history-citations`，基线 `1d43acb17bb03ca9940d486f1e7b34e7ab439a04`。本报告记录实现和已取得的证据；未通过的自动检查和真实模型前置不足继续保留。

## 主模型最终独立验收结论：Gate B 未通过

本分支是可审查的实现草稿，**不能按本报告直接合并或发布**。主模型在 worker 完成一轮集中修正后，继续用实际文件、真实命令和额外反例复核，发现以下剩余问题：

本轮窄范围收尾后，以下第 1、4 项已修复并复验；第 2、3 项仍未解决，Gate B 仍未通过。提示文本未改，因此本轮没有新增真实模型受控复验。

1. **（已修复）目录未实际发送时仍可能多计 443 字节。** 旧引用所在轮已经被 L3 摘要覆盖，最新无引用长回答落到 L2 时，`catalogue` 为空，但 `layer_rows` 的 L1 头部预留仍为 443。原有 10 项探针通过，新增摘要覆盖反例后是 **10/11**。本轮在最终目录为空时移除未发送的固定头部，usage card 使用同一 `l1_bytes`；11/11 探针通过，指定 432 组合不变量为 432/432 通过，并新增仓库回归测试。复现脚本 `/tmp/market-g9-window/review-probes.py`，原始问题数据 `reviewer-budget-property-results.json` 保留。
2. **裸编号歧义规则未可靠执行。** 真实模型在上一条没有 `[3]`、较早回答有 `[3]` 时，直接读取较早来源并回答，没有生成设计要求的澄清卡。owner、guest 均观察到失败；来源绑定本身未越过服务器允许清单。不能把允许清单校验通过等同于语义选择正确。
3. **引述句的标点可能使有效原文被答案检查删去。** 实际审计拒绝了形如 `原文为：“测试夹具毛利率为 46.2%。” [1]` 的句子，错误为 `missing_citation`；回答留下孤立右引号或变成部分完成。近期与 L2 的历史句柄、46.2% 快照和本轮新 `[1]` 绑定已验证正确，但不等于最终回答完整可用。未改动原答案/数字核验器来放行这些句子。
4. **（已修复）存在额外修改边界偏离。** 原 diff 曾显示 `direct.py` 除约定的 `prior_turns()`、`accept()`，还为保留派生错误 parents 修改了 `execute_read()`、`publish_result()`。本轮已将 `execute_read()` 和 `publish_result()` 恢复为 `1d43acb` 原样；当前相对该基线的 `direct.py` diff 仅包含 `prior_turns()` 与 `accept()`。派生提示从运行期目录句柄定位父来源，错误事实仍由 owner load 后的快照判断。保护列表中的 `__init__`、`run`、`agent_loop`、`finish_answer`、`run_direct_turn` 以及 graph `_direct_agent_turn`、历史 `get_messages`、原 dialogue runner 未改。

本次窄范围复验：相关 backend 定向组 **97 passed**；11 项独立探针 **11/11**；预算组合不变量 **432/432**。`direct.py` 的派生错误反馈新增当前轮同号来源 `parents=[99]`、历史目录对应 `parents=[1]` 的回归断言，反馈取 `[1]`。本轮没有改提示文本，因此没有新增受控真实模型运行；此前记录的 recent/ambiguous 与 owner/guest 语义失败仍有效。最终全量 backend pytest 使用 30 秒 faulthandler 与 180 秒外层上限，实际在 54.19 秒结束：**1614 passed、6 个 forecast baseline failures、7 skipped**，不是全绿。日志 `/tmp/market-g9-window/logs/final-scope-full.log`。

主模型收尾后的相关定向检查为 **134 passed（2.26 秒，退出码 0）**，日志 `/tmp/market-g9-window/reviewer-delivery-targeted.log`；并独立重跑全部 **11/11** 探针和 **432/432** 组合，结果为 `reviewer-final-probes.json`、`reviewer-final-budget-property-results.json`。AST 比对确认 `direct.py` 仅 `Turn.prior_turns`、`Turn.accept` 发生变化，graph `_direct_agent_turn`、历史 `get_messages` 与原 dialogue runner 未变。

前端原始命令 `test:i18n` 为 **90 passed**，原始 `npm --prefix frontend run build` 也通过。主模型完成的 Gate A 全量是 1591 passed / 6 failed / 7 skipped，Gate B 较早全量是 1612 passed / 6 failed / 7 skipped。上一轮 worker 全量终端输出提取到 `/tmp/market-g9-window/logs/worker-final-full-raw.log`：1613 passed / 6 failed / 7 skipped；本次收尾完整日志 `/tmp/market-g9-window/logs/final-scope-full.log` 已再次独立读取确认 **1614 passed / 6 failed / 7 skipped，退出码 1**。六项均为基线已有的固定日期 forecast 测试。

另一次主模型最终全量尝试在 180 秒诊断期限后超时，测试期间 app/tests 文件哈希未变；堆栈定位在未修改的 `TestClient` 启动路径 `ensure_conversation_archive_column` 的 DDL 执行处。单独复跑对应测试 1.16 秒通过。阻塞原因尚未完整核定，不能把该次超时记为通过，也不因此修改本任务以外的代码。日志 `reviewer-final-full.log`、`reviewer-final-full-exit.json`。

默认 64 KiB、同工作区和同影子库的独立真实回归使用临时端口 9036，避免中断 9034 的小预算专项测试。最终结果：**canonical-time 三次共 11/12 组、14/15 轮通过**；T01 第二次运行把“前年”改写为 2023 而非 2024。**suite-v2 为 18/21 组、55/58 轮通过**，总轮数通过率与历史记录相同，但失败分别是 S02.2 应澄清却直接回答、S07.1 数字核验、S14.2 引用检查，不能据总数宣称所有语义无退步。完整结果在 `reviewer-canonical-configured/`、`reviewer-suite-configured/`；原未加载配置的诊断尝试单独保留。

受控快照的首轮独立运行在 `reviewer-controlled.log` 留存 8 个状态，完整通过 2/8（两侧失效来源），近期和 L2 的精确重绑定断言均通过，但答案完整性/歧义卡失败。worker 随后使用同一脚本重跑并覆盖 `reviewer-controlled-results.json`，该次结果须与首轮区分，不能挑选各轮最好结果拼成通过。额外 FAQ 删除失效测试不等于 KnowledgeDocument 的软撤回全链路，后者仍未取得本计划要求的真实模型证据。

## A/B 语义与实现

- Gate A 过渡规则：`history_message_payload` 排除 audit、citations、trace、status、turn_id 等侧车，但保留真实工具协议、推理字段和 tool_call_id；`row_bytes` 与 store 的 `_message_bytes` 共用这一规则。A 的旧测试 `test_content_and_real_tool_calls_still_count` 在 Gate A 独立源码快照通过。
- Gate B 最终规则：上下文按用户/最终 assistant 的 role/content 问答及目录计量；tool 行与含真实 calls 的 assistant 行计 0。A helper 保留供协议投影使用，但不能用作 B 的行计量。B 回归测试明确验证 40 KB tool 行和 real-call assistant 行均为 0。
- 目录与实际 JSON 使用同一序列化计费辅助函数，覆盖嵌套字符串转义、逗号、数组括号和真实 `answers_back` 位数；目录固定头部预留在 L1，L2 条目只计实际增量。目录消息完整发给工具循环，摘要保留原来的 6,000 字符策略。
- 历史引用按 allowlist → owned manifest load → 依据持久引用快照识别派生/calculation → restore → quote 检查；派生错误父句柄从实际 manifest 读取。模型只收到受字节限制的 quote，当前轮保存和以后的恢复仍使用经验证的完整 snapshot。
- 成功、失败和取消路径保存原始 manifest；SSE/浏览器展示投影仍经现有恢复逻辑生成。前端工具标签为“取回历史引用 / Earlier citation”。无数据库迁移或新引用表。

## Rulings

1. 目录的 JSON 精确字节计量取代原计划的“每回答目录项 +20 字节”。该余量在中文嵌套 JSON 转义、数组结构和十进制 `answers_back` 增长时曾低估或高估真实请求；现在以实际渲染消息的序列化长度及条目边际差计量，选层与用量卡复用同一规则。固定头部归 L1，即使目录项只在 L2。
2. 离线评测 filler 答案在 Gate A 以 2 倍长度加入合成文本，保持分层前的“历史超过大预算”压力，同时使 40 轮预埋事实仍可在 64 KiB 目标中验证。Gate A 独立评测没有新增失败。此改动只触及合成数据，不调整线上策略。
3. `test_the_request_guard_is_the_budget_not_a_fixed_forty_kilobytes` 将不完整 user-only 伪轮改为十个完整问答轮次。B 按规则排除不完整轮，修正夹具以保留对 128 KiB 接受与 40 KiB 拒绝的原意。
4. 真实套件的首轮财报问题要求 SEC 身份配置。影子脚本初次仅读取不存在的 worktree `.env`，造成自然查询未生成引用；脚本改为进程内只读读取主仓库现存配置，不复制或输出内容。第一次 run 只作为配置前置失败保留，不能算引用功能失败或成功。
5. 预算 8 KiB 会被现有策略夹到 16 KiB；真实服务用 16 KiB。独立 controlled-history 测试通过 DB 选层确认指定回答实际进入 L2，不改变产品预算。

## 自动验收

| 检查 | 结果 |
| --- | --- |
| 独立 Gate A 定向组（基线叠加 A 源码，不含 B） | 74 passed。日志：`/tmp/market-g9-window/logs/gate-a-independent-target.log`；源码快照：`/tmp/market-g9-window/gate-a-source.tar.gz` |
| Gate A 可应用补丁 | `/tmp/market-g9-window/gate-a.patch`；`git apply --cached --check` 通过 |
| B 最终独立定向组（上下文、store/restore、真实 Turn、stream、context eval、引用/预测/结果集回归） | 134 passed。包括真实工具 dispatch、46.2% 最终回答、未串入旧 40.0%、origin、新编号、最新回答无编号不回退、长目录完整到达、长中文 quote 与异常 manifest 负例 |
| 最终独立验收探针与组合 | 11/11 探针、432/432 预算组合通过；此前 10/11 和 63 个失败组合是修复前证据，单独保留 |
| 后端全量基线 | 1,587 passed，7 skipped，7 failed；6 个固定日期 forecast 失败及 1 个 SEC sync 波动失败 |
| 后端全量 Gate B 最终复跑 | 1,614 passed，7 skipped，6 failed。与基线相同的六个 forecast 测试失败；基线另有 SEC sync 测试失败，本轮通过。失败仍存在，全量未全绿 |
| 前端 i18n | 90 passed，0 failed |
| 前端生产 build | worker 的 `--configLoader runner` 和主模型原始 `npm --prefix frontend run build` 均成功；存在既有大 chunk (>500 kB) 提示 |
| 离线 context eval | 构造对话 40 轮、692 KB；64/128 KiB 为 10/10 facts，32 KiB 为 0/10。不是线上或真实模型结果 |

最终全量六个失败 ID 与基线完全一致，逐项见 `/tmp/market-g9-window/logs/final-scope-full.log`（前一轮摘要在 `final-corrected-full-result.txt`）。基线另失败的 SEC sync cycle 测试在最终运行通过；因此不能只用失败总数解释比较。

## 影子数据库与真实模型

影子 API 9034 使用当前 worktree，最后一次重启绑定 PID 63355，`GET /docs` 返回 HTTP 200，`/agent-chat/context` 报告预算 16 KiB。验收结束后已停止本任务服务。影子脚本在导入 app 前断言并重绑到 `test_g6_shadow_20261019`；测量 SQL 使用只读事务和会话绑定 connection，未连接 `market_evidence`。

只读聚合测量：639 个会话、1,724 轮；旧协议总字节 22,829,437，新策略实际历史总字节 2,145,459，目录 655,529 字节。旧/新 L1 轮数分别为 1,294/1,716，新 L2 轮数 8。每会话的轮次中位数字节再取中位数：旧 8,816、新 610；会话内最大轮字节再取最大值：旧 241,435、新 56,298。聚合输出仅有计数与字节数，不含身份或原文。测量脚本和日志分别为 `/tmp/market-g9-window/measure.py` 和 `/tmp/market-g9-window/logs/shadow-measure-final.log`。

自然 suite-v2 JSON 首次运行（owner/guest 8 case）分别 0/8 通过、0/8 通过，受初始化 .env 缺失、首轮未形成引用及 HC04 没有中途撤回动作影响；不能用它们宣称功能失败或通过。修正配置后的近期自然检索各 2 轮：两侧首轮均 succeeded 并调用 query_financials，后续追问因工具阶段超过上下文 guard 而以 `chat_context_too_large` 部分结束，没有成功执行 `read_history_citation`。

独立 controlled-history 真实追问共 8 个 owner/guest 场景：2 个完整通过（owner L2 回查、owner 内容变更后的失效快照）；recent 两侧均在最终答案阶段 `nothing dropped`，guest L2 与 guest 内容变更场景也以该状态结束；两侧歧义均未生成要求的 clarification card。owner/guest L2 指定回答均经 DB 确认实际位于 `half_turns`。随后运行专用撤回 harness：分别为 owner 和 guest 创建唯一合成 FAQ fixture、写入带冻结引用的两轮隔离历史，再只删除该 FAQ fixture 后发起真实追问；两侧均 succeeded 且 6/6 检查通过，确认 `history_citation_unavailable`，没有回退到新内容。该用例是构造历史 + 真实追问，不是自然首轮检索；它补足了 suite JSON 缺少的中途撤回动作。日志与结果：`/tmp/market-g9-window/logs/controlled-withdrawal.log`、`/tmp/market-g9-window/e2e/withdrawal-results.json`。

Provider recorder 共收集本任务临时服务请求 70 条，阶段统计 resolver 20、classifier 14、tool_decision 26、answer 10；单次请求峰值 42,396 字节，已记录 turn 请求累计峰值 115,959 字节，turn 单次请求峰值 42,396 字节。响应未提供可解析 usage，因此 token usage 未测。日志只含匿名 turn id、阶段、字节计数和 usage 字段，不含消息、请求体、请求头或凭据。

Canonical-time default-budget 真实复测 3 次；reviewer 日志汇总 11/12 组、14/15 轮通过，对照历史 12/12 组。suite-v2 在 worker 交付后已完整结束，最终 18/21 组、55/58 轮通过，逐项失败见上方主模型结论；历史基线为 55/58 轮。日志：`/tmp/market-g9-window/reviewer-canonical-configured.log`、`/tmp/market-g9-window/reviewer-suite-configured.log`。本次真实模型验收仍有上述缺口。

## 提交交付

主模型已将 Gate A patch 应用于共享分支并提交；提交 `46df02498655348d9b29be1c9d106ccef7c4a365`。Gate B 作为未验收通过的本地审查检查点保留，本报告随该提交保存，提交 SHA 以当前分支 git log 为准。worker 还生成了两个阶段文件：

- A：`/tmp/market-g9-window/gate-a.patch`，从原始 `1d43acb` 基线生成；独立快照和定向日志如上。
- B：`/tmp/market-g9-window/gate-b.patch`，相对于 Gate A 源码快照的剩余差异，已在临时 A 快照仓库执行 `git apply --check`。

A patch 已由主模型提交。worker 的 `gate-b.patch` 在 A 快照仓库通过 `git apply --check`，但最终独立验收补充说明在本报告中，以分支提交中的文件为准。B 提交信息为 `feat: restore historical citations on demand`；它是审查检查点，不代表 Gate B 已通过。`frontend/node_modules` symlink 不纳入提交；主仓库工作区、原 dialogue runner 未改，未推送、部署或合并。
