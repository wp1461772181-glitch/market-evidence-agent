# 简化站点所有者登录

## 用户修正与设计

用户明确取消独立“操作口令”：个人密码/登录码成功后返回的稳定 `chat_credential` 即站点所有者身份。后端从仅服务端可读的 PBKDF 登录配置读取该凭据，并用常量时间比较校验 `X-Chat-Session`。公共模式要求有效个人登录配置；本地模式检测到有效配置后同样保护业务写入。无登录配置的非公共开发环境保留原有本地行为。旧 `X-App-Access-Key` 仅保留给后端内部兼容调用，不再由浏览器保存或发送。

访客的随机会话凭据不能授权写入。对话仍按会话隔离，知识库文档继续执行原有 owner hash、来源、时间、完整性和引用检查。个人登录失败后前端提示重新登录；成功后关闭提示，由用户显式重试失败操作，不自动重放。

## 实施与验证记录

- TDD red：新增 owner 登录后业务写入测试时，旧 middleware 不接受 `owner_configured` 策略参数，测试失败。
- TDD green：加入 owner credential helper、统一写入 middleware、知识库授权统一校验与登录状态安全接口后，认证定向测试通过。
- 更新旧的公共访问测试合同：没有个人登录配置在公共模式返回 503；有配置时访客/随机身份被拒，owner 身份可进入业务路由校验。
- 前端移除独立操作密钥保存入口、状态提示和相关授权文案；常规 API 请求附带当前 `X-Chat-Session`，明确传入的身份头优先。

完整结果与限制将在本次执行完成后补记。
- 最终后端：`PERSONAL_CHAT_LOGIN_PATH=/tmp/market-rag-ch34-20261011/no-owner-config.json PUBLIC_API_MODE=false .venv-rag/bin/python -m pytest -q --tb=short` → 649 passed、4 skipped。该路径为测试专用缺失配置，不读取真实登录配置。测试数据库为 conftest 创建的临时 `test_` 数据库。
- 最终前端：`npm run test:i18n` → 64 passed；`npm run build` → 成功。Vite 提示当前 JS chunk 大于 500 kB（既有构建体积提示）。
- 最终 `git diff --check` → 通过。一次中间前端 build 曾因新 helper 的 `.d.mts` 声明缺失失败，补齐类型声明后构建成功。
- 剩余验收由主执行者完成：按既定流程重启 loopback demo 并进行 CUA 操作验收；本执行未触碰相关端口或 release pointer。

## 2026-10-11 聚焦回归修正

主执行者独立复现了两项真实回归：有 owner login 配置时，浏览器的个人模型配置 PUT 漏发 `X-Chat-Session`，因此服务端返回 401；Shell 内 owner 登录成功后只更新 Agent 子组件状态，没有通知 `AppEntry`，导致旧 guest provider 继续承载页面。

- 修正模型配置身份与隔离：个人模式 profile GET/PUT 从现有 localStorage 身份读取 owner credential，使用 `Headers` 合并且保留调用者显式 `X-Chat-Session`；owner login 配置存在时，local profile 路由对 GET 和 PUT 都执行服务端 owner 验证。访客模式完全跳过 native profile API，使用独立的 OPFS 文件名 / IndexedDB 数据库名；不会复制 owner 本机文件中的 API key。原 loopback、host/origin 与 forwarded-header 检查、public-mode 404 均保留。
- 修正登录 remount：Shell 登录成功先写入 personal identity，再通知根组件切换 mode；新的 `chatSessionKey` 会使 `AgentChatProviderRoot` remount，模式切换继续沿用各自 conversation identity 和现有 history/archive 存储。失败操作仍要求用户手动重试。恢复对话框初始聚焦、Tab 焦点循环、背景 inert、关闭后返回焦点；只显示个人登录输入，不增加独立操作密码状态。
- 测试隔离：`tests/conftest.py` 在导入 `app.main` 前设置唯一、保证不存在的 pytest-only login config 路径；需要 owner 的测试只读取自己在临时目录生成的配置。新增有效个人登录配置 + 空 `APP_API_ACCESS_KEY` 的 startup 检查，使用 conftest 创建的临时测试数据库。Malformed non-ASCII owner header 返回拒绝，不触发 500。
- 实际验收：`PUBLIC_API_MODE=false .venv-rag/bin/python -m pytest -q --tb=short` → **652 passed, 4 skipped**；`npm run test:i18n` → **67 passed**；`npm run build` → 成功；`git diff --check` → 通过。前端构建保留 Vite 对 689.71 kB 主 JS chunk 的体积提示；Python 有依赖弃用提示和既有重复 OpenAPI operation ID 提示。此前一次前端测试曾因旧测试把所有 `localStorage.getItem` 调用都计作迁移读取而失败；断言现已限定只统计旧 settings key，完整套件通过。
- 待主执行者验收：真实 UI 登录/relogin 后确认 owner 立即使用个人 conversation、访客切换回来立即恢复 guest conversation；检查对话框键盘焦点与背景不可交互，并验证资料保存/选择/导入。此次执行未检查真实 profile 内容、未操作 DB/server/端口或 release symlink；测试 profile 和 owner 配置均为生成假数据。


## 2026-10-11 deployment candidate preparation

- Primary UI acceptance completed: guest write returned 401; personal login succeeded; the root provider remounted and restored private owner documents; retrying the knowledge write returned 200 without a second operation key; local model-profile hello/save returned 200 using fake-only local profile values. No paid provider calls were made.
- A clean candidate was assembled from `b65beacf7fa16362c6e3f94de13b05622c01fe00` at `/tmp/market-owner-login-20261011/release-candidate`; semantic app changes were included and unrelated comment-only app files were excluded by AST comparison. See the preparation report for the explicit manifest.
- Candidate checks: RAG runtime full suite **654 passed, 4 skipped**; ordinary `.venv` full suite **648 passed, 10 skipped**; pymilvus-specific hybrid tests are individually marked as optional-dependency skips; frontend **67 passed** and build succeeded. The base app import does not require FlagEmbedding. Candidate `scripts/run_api_server.sh --print` supports `MARKET_EVIDENCE_API_PYTHON` and reports the chosen executable without starting services.
- Read-only production inspection found the knowledge schema tables and expected columns present, all knowledge row counts zero, no pending index tasks, and no running forecast/material jobs. Loopback Milvus answered on 19530 and local BGE-M3/reranker snapshot directories exist. RAG flags are unset; the latest held-out quality report remains below target, so runtime stays disabled. No migration, commit, push, release switch, service restart, or credential change was performed.


## 2026-10-11 deployment-candidate correction

Primary independent review found the launcher dry-run had started requiring the default `.venv`, which is absent in a fresh CI checkout. Restored default `--print` to report configuration without checking that default path; explicit `MARKET_EVIDENCE_API_PYTHON` overrides still must be executable, and normal startup always checks executability. The regression test builds a fresh temporary checkout with neither dependency directory and uses `sys.executable` for the override case.

Added pinned `pymilvus==2.6.13` to the optional `test` extra so CI's `pip install -e '.[test]'` exercises Milvus contract tests without installing embedding models. Installed that exact package from PyPI into the candidate's isolated ordinary `.venv`; no global Python or pip configuration was changed. Both full candidate backend runs used `PUBLIC_API_MODE=false CORS_ALLOWED_ORIGINS=https://pages.example.test`; ordinary `.venv`: **657 passed, 4 skipped**; `.venv-rag`: **657 passed, 4 skipped**. The four skips are existing environment-gated tests. Dedicated launcher checks: **7 passed**.

Frontend: `npm run test:i18n` **67 passed**; `npm run build` succeeded (Vite reports the existing >500 kB JS chunk advisory). `git diff --check` equivalent whitespace scan passed. Added the accepted `evaluate_rag.py`, `evaluate_rag_ch04.py`, `run_ch34_local.sh`, `seed_ch34_demo.py`, Chapter 3/4 notes, reports, spec/plan, and API notes. The documented local review/evaluation script paths exist in the candidate. A credential-shape scan of added text reported no real credential patterns; no model weights, corpus data, or task-only browser fake-provider adapter were included.

No production commands, local RAG demo, migration, database, port, launch agent, release pointer, commit, or push were touched. RAG remains disabled for deployment until its real-corpus quality gate is met.

## 2026-10-11 publication started

- Primary acceptance for the clean candidate: **657 passed, 4 skipped** with the CI CORS environment configured. Publication proceeds using only the reviewed candidate manifest; no production migration or RAG enablement is in scope.
