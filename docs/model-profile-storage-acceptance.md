# 模型配置持久化：独立验收（2026-10-11）

状态：**主要保存路径通过；整轮验收部分通过，尚有两项未完成。未提交、推送或部署。**

## 已验证的能力

- 本地运行模式：启用 `LOCAL_MODEL_PROFILES_ENABLED=true` 后，由本地 FastAPI 服务保存普通 JSON 文件，默认 `data/model-profiles.json`。测试使用单独的 `/tmp/market-model-files-20261011/live-profiles.json`，不读取实际用户配置或 `.env`。
- 实际 API 进程从 PID 30216 停止后，由新进程 PID 32298 重新启动。文件 SHA-256 不变，浏览器刷新恢复两个模型、显示名称、所选模型、key 和既有测试状态。
- 真实页面导入两份配置后，导入的模型均未验证；通过测试并保存其中一个模型后，文件正常更新。hello 使用明确标记的本地 fixture，未调用真实模型服务或付费 API。
- 页面导出的实际下载文件与本地文件的 JSON 完全一致。配置文件采用 0600 权限。对话归档仍使用独立格式，不包含模型配置。
- 对话输入框快捷选择另一个未验证的模型后，所选 ID 写入普通文件，聊天输入被禁用；切回已验证模型后恢复就绪状态。
- 公开模式 fixture 即使设置本地文件功能开关，也拒绝访问本地配置接口。页面采用 OPFS；从导出文件恢复后，刷新仍保留配置。公开模式的配置文件未被创建。
- 导入损坏 JSON 时，页面显示错误并保留原有两个模型及选择。
- 独立 API 检查验证公开来源、代理头和非本地主机被拒绝；错误不回显测试 key。无效结构、时间、模型和超限分块请求不修改原文件。
- 本轮开始前的 1,010 个中文短语和 1,010 个英文翻译完整保留；Git HEAD 未改变。

## 命令结果

从仓库根目录运行：

```sh
.venv/bin/python -m pytest tests/test_local_model_profiles.py tests/test_agent_chat_api.py tests/test_agent_chat_stream.py -q
```

结果：**32 passed**，5 条既有 FastAPI 生命周期弃用警告。

从 `frontend` 运行：

```sh
npm run test:i18n
npm run build
```

结果：**43 passed**；TypeScript 与 Vite 构建通过。自动测试通过不能替代下方两项独立检查。

独立 API 脚本 `/tmp/market-model-files-20261011/primary_acceptance.py`：PASS。证据文件：`primary-backend-final.txt`、`primary-frontend-final.txt`、`primary-build-final.txt`、`primary-final-api.txt`（同一临时目录）。页面截图：`model-settings-acceptance.png`。

## 尚未通过的检查

1. **未填写 API key 的旧配置恢复**：`parseProfileBackup` 接受 `api_key: ""`、`tested_at: null` 的旧配置，但是 `mergeImportedProfiles` 仍调用 `validateProfileDocument(imported, true)`，因此实际导入流程拒绝它。含完整 key 的配置已经通过实际导入导出验收；旧配置恢复不得宣称完整通过。复现：先解析一个空 key 的合法旧配置，再合并到空文档，会抛出“模型配置文件包含无效 endpoint、Model ID 或 API key。”。
2. **中文模式说明**：页面调用 `t("本地文件模式说明")` / `t("浏览器模式说明")`，英文词典含完整解释，但当前中文翻译函数直接返回输入文本，实际中文页面只显示这两个标题。聊天页的本机文件/浏览器保存说明已正确显示；设置页的完整中文解释尚未完成。

已按仓库 `AGENTS.md` 执行规划、worker 实现和一轮集中修复；修复期间发现误恢复旧版翻译文件，已中断并在同一修复阶段从本轮起始快照恢复。随后主代理独立验证上述未通过项，未接手实现或扩大修复范围。开发过程保存在 `dev-notes/model-profile-storage.md`。

## 使用与范围

启动命令及操作步骤见 `docs/model-profile-storage.md`。备份文件包含 API key，应作为私人配置保管。单进程本地保存；本轮未做高并发、真实供应商鉴权、线上 GitHub Pages 更新、生产服务重启或 RAG 验收。
