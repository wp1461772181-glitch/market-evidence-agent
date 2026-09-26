# AI 研究工作台 V3：Luna Implementation Plan

> 执行方式已由用户指定：GPT 6 Luna 负责实现与自测，主代理负责规划、范围控制和独立验收。按任务逐项执行，不再次要求 yes/no。Luna 是最终执行者，不启动嵌套执行者。

**Goal:** 材料可查看 AI 分析与原文，DeepSeek 简报经 Jev 产生可追溯概率，支持手动/自动修订和系统式工作台。

**Architecture:** 原 FastAPI/PostgreSQL/worker/React 上增量改造。先材料分析资产与独立 Jev provider，再接预测处理器，最后页面与闭环验收。

**Tech Stack:** 现有 Python/Pydantic/SQLAlchemy/PostgreSQL/httpx（如项目已有）、React/TypeScript/Vite。优先现有依赖；原 SDK 不支持 Decisions 时使用普通 HTTP，不加框架。

**Spec:** [必须先读实施规格](agent-research-v3-spec.md)。**Progress:** [v3-progress.md](v3-progress.md)。本计划接口若因已存在代码而需要小幅调整，先写明替代映射再改，禁止不同任务各造同名不同义字段。

## 全局约束

- 工作目录 `/Users/wupeng/monash-ai/projects/market-evidence-agent`；先读适用 AGENTS 与规范记忆。保护已有改动与数据。
- 旧训练门槛不是 V3 开发阻塞条件。不得重训模型、扩大语料、替换历史数值或谎称 Jev 已校准。
- 所有 SQL 迁移可重复、追加式；先 `--check` 再 `--apply`，不删除表、不重置开发库。修改 CHECK 时事务内明确替换并保留旧枚举。
- 外部模型调用前限制范围，自动测试不访问付费 API。真实试点不在开发库创建虚构材料。Key 仅从 .env 载入，不输出值或上游原始错误。
- 相同材料分析引用版本固定；模型失败不伪造概率。GET 只读。
- 每阶段更新 V3 进度：实现、自测、主代理验收、真实验证四项分开。
- 本机系统 git/Python 可能触发 Xcode license。Python 用 `.venv/bin/python`；git 用 `/Users/wupeng/.cache/codex-runtimes/codex-primary-runtime/dependencies/bin/fallback/git`。不要为了此任务接受系统许可/安装 Xcode。

## 重点复核的五类输入

1. 同一材料“重新分析”失败后，仍能查看旧成功版本（L1/L4）。
2. 资料发布时间很早但今天才发现，不能倒灌历史预测；未解决旧风险不能仅因旧而丢弃（L3）。
3. 模型响应概率格式正确却类别/数值异常，或伪造引文，必须拒绝（L1/L2）。
4. 用户切换股票/根时慢请求返回，不能展示上一个股票的材料或提交到错误根（L4）。
5. 自动修订与人工修订竞态、任务重复及失租约，不能双发布或续期 72 小时窗口（L1/L5）。

## 文件与责任地图

| 任务 | 新文件 | 主要接入点 |
|---|---|---|
| L1 材料资产 | `app/material_analysis_schema.py`, `app/material_analysis_models.py`, `app/material_analysis.py`, `app/material_analysis_api.py`, `scripts/migrate_material_analysis.py` | `app/event_provider.py`, `app/evidence_context.py`, `app/forecast_worker.py`, `app/main.py` |
| L2 Jev | `app/jev_provider.py`, `tests/test_jev_provider.py`, `scripts/probe_jev.py` | `.env.example`、现有本地 env loader |
| L3 简报与预测 | `app/research_brief.py`, `app/agent_forecast_processor.py`, `scripts/migrate_agent_forecast.py` | `app/forecast_v2.py`, `app/forecast_v2_models.py`, `app/forecast_v2_api.py`, `app/forecast_worker.py` |
| L4 页面 | `frontend/src/MaterialLibrary.tsx`, `frontend/src/MaterialAnalysisDetail.tsx` | `App.tsx`, `V2ForecastWorkspace.tsx`, `api.ts`, `types.ts`, `styles.css` |
| L5 修订/评估 | 针对现有模块增加测试 | `official_monitor_v2.py`, `forecast_evaluation_v2.py`, 版本 API/页面 |
| L6 交付 | `docs/v3-acceptance.md` | README、旧计划顶部导航、进度、真实试点 |

上表是预定映射，不要求把无改动必要的接入点都改一遍。

## L1：单份材料分析、持久版本与 API

**前置：** 无。与 L2 可并行，L2 不修改这里的文件。

**接口：**

- `MaterialAnalysisPayload`：实施规格第 4 节。
- `validate_material_analysis(payload: dict, text: str) -> MaterialAnalysisPayload`：schema 与引用/事实 ID 检查。
- `request_material_analysis(*, db, source_type: str, source_id: UUID, idempotency_key: str, force: bool=False) -> dict`：返回 job_id/status/analysis_id（nullable）/cache_hit。
- `run_material_analysis_once(*, session_factory, provider=None, job_id=None) -> dict`：生产默认 DeepSeek，测试可注入；独立于预测任务领取但复用同一 worker 进程。
- `get_material_analysis(*, db, analysis_id: UUID) -> dict`：读取不可变结果与冻结来源。
- `GET /v3/materials?symbol=AAPL&source_type=...&analysis_status=...&review_status=...&limit=10&offset=0`：`{items,total,limit,offset}`，source_type 可省略，limit 1–50。
- `POST /v3/materials/{source_type}/{source_id}/analysis-jobs`：body `{idempotency_key,force}`，202；冲突键不同参数409，未知404，无正文422或可行动 blocked 状态。
- `GET /v3/material-analysis-jobs/{job_id}`，`GET /v3/materials/{source_type}/{source_id}/analyses`，`GET /v3/material-analyses/{analysis_id}`。
- 列表 item 至少含 symbol/source_type/source_id/title/published_at/observed_at/source_url/analysis_status/latest_analysis_id/latest_job/review_status/coverage/can_view_original/can_download_original。分析历史按 version_no 降序，正文只在详情返回。

- [ ] 读现有 `UploadedEvidence`、`SecFilingInventory`、冻结源函数和 DeepSeek provider，记录复用位置；不把旧 research_report 无条件当本模板。
- [ ] 在 `tests/test_material_analysis.py` 写 schema 引文越界、未知 fact_id、零正反项、同材料缓存、force 幂等、force失败保留旧版、跨股票隔离、租约过期无法发布的行为测试。
- [ ] 运行 `.venv/bin/python -m pytest tests/test_material_analysis.py -q` 确认测试能捕获缺失实现。
- [ ] 实现两张表、事务化发布与可重复迁移，校验缓存对应正文/配置指纹，引用使用规范文本坐标。
- [ ] 用已有 DeepSeek provider 的 `ProviderResult`，一次请求产出模板 JSON，记录实际模型/用量，校验失败留下安全错误。不要先跑旧事件抽取再重复跑两次正反分析。
- [ ] 接材料 API 与持久 worker；在 `tests/test_material_analysis_api.py` 覆盖筛选、分页、GET零副作用、未知ID、幂等冲突。
- [ ] `scripts/migrate_material_analysis.py --check/--apply` 风格沿用 V2；隔离测试证明 apply 两次不损失数据。开发库 apply 由主代理验收后执行。
- [ ] 跑本任务测试和旧 worker/冻结上下文测试；报告变更文件、测试数与未接入项；主代理验收后单独提交。

**完成：** 从现有材料 ID 可以排队、后台分析、查询历史并复用。此时前端尚未改也可通过 API 验收，不可称整个材料库功能完成。

## L2：OpenRouter Jev 适配器与受限试点

**前置：** 无，不依赖 L1。独立完成后交主代理检查，不能顺手接生产默认。

**接口：**

- `JevDecisionResult` frozen dataclass：probabilities(dict[str,float])、choice、confidence(float|None)、requested_model、actual_model、request_id(str|None)、usage(dict)、latency_ms、question_version、input_sha256。
- `JevProviderError`：safe code/status_code/retryable，不含 key/原始响应正文。
- `JevDecisionProvider(*, api_key=None, model=None, client=None).evaluate(brief: dict) -> JevDecisionResult`。
- `create_jev_provider_from_env()`；只从项目忽略的 .env 或进程环境取值，默认 model=`typesafe/jev-1.13`。

- [ ] 读官方 Decisions 参考（规格第13节），核对实际 criteria 字段；输出 schema 如有差异记录到接入说明。
- [ ] 写 `tests/test_jev_provider.py`：成功三分布、并列最大、NaN/inf/bool、和不为1、缺/额外类别、choice不在最大值、错误类型、401/402不重试、429退避有界、超时、错误脱敏、请求只发官方 host。
- [ ] 运行测试确认缺失 provider 被发现，再实现 HTTP adapter。不要安装 TypeSafe SDK 或使用 chat completions。
- [ ] 新增 `scripts/probe_jev.py`：默认只检查配置/打印脱敏计划；显式 `--live` 才调用，最多一次应用层请求。内置无敏感的短业务分类状态或读取指定公开简报，不写开发库。只输出成功/错误、实际模型、用量、概率、耗时，报告不含 key。
- [ ] 增加 `.env.example` 空值示例，写 `docs/jev-integration.md` 说明如何试点和当前限制。
- [ ] 运行本任务测试；真实 `--live` 已被本轮用户授权，可做一次并记录真实结果；失败不可换模型冒充成功。
- [ ] 交主代理验收并单独提交。此时只能称“接口已接入/已探针验证”，不是股票预测已验证。

## L3：综合简报与 Jev 预测发布

**前置：** L1/L2 均验收；主要依赖 L1 的 payload 与 L2 evaluate。

**接口：**

- `ResearchBrief` Pydantic 按规格第7节，字段长度和引用数量有界。
- `build_research_brief(*, market_summary: dict, analyses: list[dict], target_contract: dict, decision_at, parent_brief: dict|None, provider) -> ResearchBrief`。
- `AgentForecastProcessor` 对外与现有 Processor(job)->ForecastDraft 相同。构造支持市场/DeepSeek/Jev provider 注入。
- ForecastDraft 新 nullable 字段放尾部有默认值；旧调用无需修改。发布/序列化/指纹支持 decision_probabilities/research_brief/experimental_jev。

- [ ] 写 `tests/test_research_brief.py`：引用不存在的分析拒绝；新/背景/迟发现分组、无新财报、冲突、星级原样保留、拒绝来源排除、超过显式选择上限422、未来观测不可见。
- [ ] 写 `tests/test_agent_forecast_processor.py`：两次预测复用材料分析、Jev失败保留分析但不发布数值、无新材料仍刷新行情、有新增材料重建简报、insufficient不调用Jev。
- [ ] 实现简报和选材，按规格 8 份/5新/3背景上限，保存 omitted 原因。只重跑需要的材料，不批量补整个库。
- [ ] 追加迁移：新 JSONB nullable 字段与 CHECK 枚举；旧行原样可读。修改发布函数和 API 保留旧字段合同。
- [ ] 接显式 `FORECAST_DECISION_MODE=research_only|jev`，默认保持 research_only，失效配置启动报安全错误，不静默降级。
- [ ] 处理器获取依赖分析任务结果要有界：复用 L1 服务，不在单worker里等待自己才能领取的子任务；同进程可直接执行指定材料任务或将预测延期释放租约。选择其中一种并测试不会死锁。
- [ ] 验证同一根/固定目标、可追溯缓存与不可变旧版，跑 V2 publication/worker/API 回归；主代理验收后提交。

## L4：材料库与工作台界面

**前置：** L1 API 稳定后材料库可开始；预测页待 L3。先读适用 frontend-design 技能，风格是紧凑的研究系统，不是营销落地页。

- [ ] TypeScript 类型与 API client 对齐真实字段；API 失败给可行动错误。禁止硬编码成功状态或示例概率。
- [ ] 实现材料表格、筛选/分页和三页签详情；分析按钮可恢复任务状态，轮询仅在任务活跃时运行；切股取消/忽略过期请求。
- [ ] 接原文冻结文本与来源网页；核对当前存储是否真有原始字节，再增加受限下载与新上传保存。旧无字节材料显示“仅保存提取文本”。
- [ ] 实现重新分析、历史版本切换和引用定位；运行中/失败不隐藏旧成功分析，截断提示不折叠藏掉。
- [ ] 研究页结果优先：三项 Jev 概率、固定目标、蜡烛图、正反摘要、缺口、版本变化。Jev输出与DeepSeek证据说明分别署名。
- [ ] 保留上传1–5星、人工核验、手动选日期修订、监控、旧档案入口；导航改名不丢功能。
- [ ] `cd frontend && npm run build`；浏览器在1440/390px验收创建任务→重开→看原文→切分析历史→切股票→预测版本。保存实际截图和失败限制，不以build替代浏览器验证。
- [ ] 主代理检查可见结果后提交。

## L5：同一路径的手动/自动修订与评估

**前置：** L3；复用现有 root/lease/monitor，不另建调度。

- [ ] `tests/test_agent_revision.py` 覆盖9月1日根/9月2日新证据修订保持原目标与锚点；多个材料同轮一个任务；重复扫描无新版本；72h边界前可入队、边界按现有严格合同测试；手动分支与自动主线不相互覆盖。
- [ ] 来源核验/星级更新作为新输入快照，旧forecast manifest不变。资料虽老但今天新发现仍显示准确双时间。
- [ ] 监控自动任务复用 AgentForecastProcessor；只在L6切换配置后生效。扫描失败/空结果/下载失败分别保留。
- [ ] 评估从 decision_probabilities 读取 Jev实验概率，research_only不评分；模型版本与time_mode分组，根作为独立样本单位，复用安全价格复核。
- [ ] 跑 `test_forecast_evaluation_v2.py` 与新评估测试：旧joint/新jev不混分母、未来根pending、拆股blocked、重复评估无重复行。
- [ ] 受控官方事件只在隔离库/fixture测试，真实无公告必须报告无公告；主代理验收后提交。

## L6：真实验收、切换与交付

**前置：** L1–L5 自测与主代理复核。

- [ ] 全量 `.venv/bin/python -m pytest -q`、前端build、全部迁移check、git diff --check；通过后不无故反复重跑。
- [ ] 选一份现有官方材料、一份已存在且明确合适的媒体材料，最多3次DeepSeek应用调用/3次Jev应用调用，在隔离库完成可审计试点；没有合适媒体时不造假，单独注明未验。
- [ ] 验证分析落库→再次打开不调用→引文对原文→简报引用分析ID→Jev→初版→新证据修订→旧版不变。改变证据不保证概率一定变；验证输入确实变、模型确实被调用且返回被保存，不伪造“变好了”。
- [ ] 先保存已有进程/worker配置，apply追加迁移，再本地设FORECAST_DECISION_MODE=jev，平滑重启相关服务，检查真实worker模式与界面。若失败，仅恢复旧模式，不删新数据。
- [ ] 写 `docs/v3-acceptance.md`：每项分别 implementation/test/live/browser，真实调用模型/用量/耗时、材料与分析ID、限制；没有到期样本保留待验，不宣称全系统效果达标。
- [ ] README、旧V2计划头部只增加新路线入口，历史记录保留；更新v3-progress和规范项目记忆，记忆不存密钥。
- [ ] 主代理独立抽查及git提交，推送已有GitHub，读回远端SHA；最终说明完成阶段、未完成项、文档入口。

## 可直接交给新 Luna 的执行指令

> 在 `/Users/wupeng/monash-ai/projects/market-evidence-agent` 工作。先读 `docs/agent-research-v3-spec.md`、本计划和 `docs/v3-progress.md`，从主代理指定的首个未完成任务执行。目标是 DeepSeek 材料分析与综合简报、OpenRouter Jev 概率、材料库分析/原文/历史及可追溯修订。不要重训模型或重写整套系统。每次只交付一个可验收阶段，保护旧数据和他人改动，测试使用隔离库，Key只从忽略的.env读取。报告实际改动、测试、真实验证、未完成项。不要再委派；不要擅自提交其他代理文件或扩大真实调用预算。
