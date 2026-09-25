# V2 证据输入试点记录

日期：2026-09-24。此文件记录 P2 小批次真实来源的可用性，不代表联合模型已训练或预测效果已验证。

## 读取范围与预算

- 来源：SEC 官方公司提交目录与同一 accession 下允许的文档，股票 AAPL、MSFT、GOOGL、AMZN、NVDA。
- 候选公开日期：2021-01-01 至 2026-08-31；本次每只股票最多读取 1 份申报目录的历史页，实际目录覆盖均标记完整。
- 先运行 `./.venv/bin/python scripts/build_v2_corpus.py --plan --max-pages 1`；候选数分别为 AAPL 71、MSFT 75、GOOGL 88、AMZN 82、NVDA 80，共 396。`--plan` 未下载正文或写入文件。
- 随后运行 `./.venv/bin/python scripts/build_v2_corpus.py --max-new-documents 2 --max-pages 1`；只保存 2 份新来源快照。脚本限制一次最多 30 份，不调用 DeepSeek。
- 快照及清单保存在被 Git 忽略的 `data/v2/`。清单把实际下载时刻记为 `observed_at`；历史公开时刻单独记为 `published_at`，模式标为 `historical_research`，不会伪装成本项目当年已看到材料。

## 真实结果

| 股票 / 申报 | 内容结果 | 覆盖状态 |
|---|---|---|
| AAPL 10-Q，`0000320193-26-000020` | 保存 80,000 字符官方正文摘录与内容哈希 | 原文更长，`coverage_incomplete=true` |
| GOOGL 8-K，`0001193125-26-342390` | 保存 9,314 字符官方正文与内容哈希 | 本次来源无截断 |
| MSFT 候选，`0001193125-26-323660` | 原文超过当前 5 MB 下载上限，跳过 | 错误明确返回，未保存为已分析 |

语料收集脚本本身的 DeepSeek 调用数为 0。随后对这两份已保存的官方快照做了受限的真实抽取验证：AAPL 用前 24,000 字得到 2 个带原文引文的事件，调用 8,955 tokens；GOOGL 用 9,314 字得到 1 个事件，调用 3,479 tokens。再次抽取同一 GOOGL 输入命中缓存，没有再次请求模型。两次真实新请求合计 12,434 tokens；这些事件类型和引文通过现有本地校验，但没有产生可用于模型的双端财务同比事实。实际返回模型均为本机配置的 `deepseek-flash`。

开发数据库里两份官方材料均已形成 observed 模式的不可变 V2 证据版本：AAPL 保留截断/分析范围标记，GOOGL 保留待人工核验状态。它们是 2026-09-24 实际观测的材料，不能当作 7–8 月当时已被本系统观察。随后 GOOGL 证据版本 `d7230388-67fa-4d8f-820c-d060e024ff1c` 完成一次真实事件抽取及正反研究，研究记录 `d3890f96-6b11-41bf-a8dd-8b73e8145546` 为 succeeded，来源数 1、抽取事件数 1、可核对的正方论点 5 条与反方论点 4 条。重复准备该冻结来源时缓存命中 1、额外抽取 provider 调用 0。该研究仍待人工判断，不能解读为已校准预测或事实因果关系。

后续代码已让冻结上下文和历史语料优先查同 accession 的 99.1 附件：命中时保留实际附件 URL、名称、内容哈希、同 accession acceptance-time 依据及字符范围；无匹配或读取失败会留下 `not_found`/`unavailable` 状态和覆盖不完整标记。限量真实 SEC 检查已验证 AAPL 三份和 MSFT 一份近期 8-K 的目录读取及“无 99.1”结果。

## 2026-09-24 判断与下一步

两份真实材料证明官方目录、受限正文、原文哈希、动态研究入口及真实抽取缓存可用；其中一份存在明确截断，另一份文件因大小限制不可用。8-K 附件已接入冻结上下文，下一步是补一份真实附件命中样本及一份有真实来源的手动媒体样本。当前开发库没有手动媒体记录，不能将媒体 fixture 当成真实样本。抽取质量及训练数据量在本次样本下仍未知。

SEC 的 [Submissions API 文档](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)说明较早申报可能列在附加 JSON 文件；[EDGAR 目录说明](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data)列出 accession 目录的 `index.json`，本项目只从已验证的官方路径读取允许的附件。

## 2026-09-25 补充验证

- 在 1 次 accession 目录请求与 1 次附件正文请求的预算内，通过正规库存路径读取 MSFT 8-K `0000950170-25-100226` 的 [Exhibit 99.1](https://www.sec.gov/Archives/edgar/data/789019/000095017025100226/msft-ex99_1.htm)，并冻结为 observed V2 事件 `3d547c0c-4192-4404-82fa-c2db54e8bf22`。附件实际 URL、主申报 URL、SEC acceptance 公开时间 `2025-07-30T20:08:53+00:00`、本次观测时间 `2026-09-25T03:28:53.065699+00:00`、SHA-256 `ef11460059413497b3dccda4cc265a737ec717f6084fdca9ee199b4d2d4b5f8b` 与字符定位 `[0, 18317)` 均已持久化。摘录 18,317 字符，附件自身 `coverage=8k_related_exhibit`、`coverage_incomplete=false`；同次 context 因其他自动纳入的 MSFT 材料存在覆盖缺口而仍为不完整。冻结步骤本身未调用 LLM、未创建预测、未输出正文；随后按下一条记录单独运行真实研究。
- 对同一冻结事件另建只含该事件的临时研究 context（不重新抓取、不写预测）：研究决策时刻为 `2025-07-30T20:09:00+00:00`，模式为 `historical_research`，保留来源实际观测时间 `2026-09-25T03:28:53.065699+00:00`，因此报告明确标记 `historical_backfill=true`、`observed_after_decision=true`，不将它表述为 2025 年当时系统已看到的材料。bridge 严格只消费该事件。应用层分别执行一次事件抽取、一次正方研究与一次反方研究；DeepSeek `deepseek-flash` 可见用量为 prompt 15,602 tokens、completion 1,060 tokens、合计 16,662 tokens。研究记录 `7325bb82-fa8c-4829-8940-fa59ec1aa15b` 为 succeeded，来源数 1、正反各 5 条主张，10 条引文均通过冻结正文子串校验。事件抽取缓存键 `428e624e45351f9846de15f2d71119b0662f6b9daad2765f2b47b3083c913621` 首次未命中，随后不构造 provider 的重放命中缓存、额外应用层调用 0。HTTP 重试次数未单独观测，不将其表述为精确网络请求计数。研究结论仍是待人工核验的来源绑定推断，不产生概率、训练或预测。
- 一份 NVDA 媒体材料在隔离测试库完成手动上传、冻结与文字定位；详见[媒体样本试点](v2-media-sample-audit.md)。这不是媒体自动抓取或真实研究引文生成，也不能证明媒体概率通道可用。
