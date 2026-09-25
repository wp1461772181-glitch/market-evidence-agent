# V2 手动媒体样本试点记录

日期：2026-09-25。这个试点只验证一条具备来源、时间、署名和文字定位信息的媒体材料能否作为 **人工上传材料** 冻结进 V2 证据上下文。它不是自动新闻抓取、事实确认、模型训练或预测效果验证。

## 受限样本

| 项目 | 记录 |
|---|---|
| 股票 | `NVDA` |
| 媒体材料 | [Can Australia build one of the world's largest data centres?](https://stories.theconversation.com/can-australia-build-one-of-the-worlds-largest-data-centres/) |
| 作者 / 发布者 | Bronwyn Cumbo 与 The Conversation Digital Storytelling Team / The Conversation |
| 页面标示的发布时间 | 2026-02-10（页面未提供精确时刻） |
| 相关性 | “Chips”部分讨论 NVIDIA 高端芯片的需求与供应约束，属于与 NVDA 直接相关的行业供需背景，不能单独证明股价方向。 |
| 原文定位 | `Chips` 小节中，以 “Just sourcing the chips is a considerable challenge.” 开头的段落。 |
| 本次本机材料 | 只保存该段 278 个字符的带出处摘录到一次性测试数据库；完整网页、图片和附件均未下载到项目数据目录，也不进入 Git。 |
| 来源许可核对 | The Conversation 的[转载指引](https://theconversation.com/us/republishing-guidelines)声明文章采用 CC BY-ND 4.0，并要求保留作者、The Conversation 和原文链接；本试点不公开转载文本。其指引还提示商业、非新闻使用可能需要额外许可，因此后续若产品对外提供或商业化，必须重新核对许可/取得授权。 |

## 实际链路验证

在独立的、名称以 `test_media_sample_` 开头的 PostgreSQL 数据库中执行，完成后已销毁该数据库：

1. 以 `uploaded_media` 创建 NVDA 手动材料，保留来源 URL、测试用 `published_at=2026-02-10T00:00:00Z`、测试用 `observed_at=2026-09-25T00:00:00Z`、4 星用户自评和原始字节 SHA-256。
2. 以相同截止时间冻结 V2 证据上下文，结果状态为 `pending_review`，不会冒充已确认事实。
3. 冻结事件保留 `analysis_locator={kind: extracted_text_char_range, start: 0, end: 278}`；预测输入清单只引用事件 ID、哈希、来源 URL 和覆盖状态，不复制正文。

本次材料的原始字节 SHA-256：`f3a586eadfe8b724aea3c59732809d3f98311aeb6e9b71c4e72150bcfcfa14cc`。

`published_at=2026-02-10T00:00:00Z` 只是为现有数据库合同提供的 **date-only 测试代理**，不是来源证实的公开时刻；`observed_at=2026-09-25T00:00:00Z` 同样是隔离测试注入的时间，不是本次网页请求的精确观测时刻。因此这条样本只能证明历史研究模式的上传与冻结路径可用，不能用于当天盘中 point-in-time 判断，也不能成为自动修订的触发材料。

## 代码补齐与边界

此前上传媒体能冻结文本，但缺少与 SEC 摘录一致的 `analysis_locator` 和 `coverage_incomplete` 字段，无法给后续引文校验提供统一的文字范围。本次只补齐这两个快照字段，并由 `tests/test_evidence_context_v2.py` 覆盖。

验证结果：`./.venv/bin/pytest -q tests/test_evidence_context_v2.py` 通过（13 passed）。

仍未完成：

- 没有自动抓取/入库任何媒体源；人工上传仍是唯一入口。
- 这条材料是待人工核验的媒体背景，不会改变联合概率，也没有进入训练语料。
- 此次验证只覆盖“上传 → 冻结事件 → 文字定位”；尚未对媒体正文运行真实研究工作流或生成媒体事实引文。
- 单一材料不能满足计划中媒体渠道至少 50 个独立事件的封存验证要求；媒体数值通道仍为 `unsupported_evidence_channel`。
