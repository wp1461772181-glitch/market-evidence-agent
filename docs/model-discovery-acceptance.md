# 模型目录功能验收

验收日期：2026-10-11。状态：本地功能通过，尚未发布到 GitHub Pages 或线上 API。本记录只覆盖模型目录及相关配置回归，不代表整个 RAG 项目验收。

## 已确认行为

- 模型设置提供服务商模型候选下拉框和刷新按钮，筛选后最多返回 5 个对话模型。选择候选只填入 Model ID，保留显示名称、手动输入、Hello 测试和保存步骤。
- 填写 API key 后自动延迟获取目录；打开已保存且带 key 的配置会获取目录。OpenRouter 的公共目录可不填 key 点击刷新。新配置切换到 OpenRouter 后仍需点击刷新。
- 服务商提供有效发布时间时按时间降序排列；部分缺少时间或全部缺少时间会明确提示，不能保证所有服务商都能提供严格的“最新 5 个”。
- 切换服务商清空旧 key 和候选。编辑请求地址取消旧请求及定时任务，保留的 key 只有在明确刷新或再次编辑 key 后才发送至新地址。候选选择不会自动保存或验证配置。
- 不支持目录接口时保留手填 Model ID；列表可见也不代表账户有调用权限，仍需 Hello 测试。
- 旧备份中空 key、未验证的配置可正常解析并合并，仍需测试才能启用。浏览器/本地模式中文说明已完整显示。

## 独立验证

后端命令（仓库根目录）：

```sh
.venv/bin/python -m pytest tests/test_agent_model_discovery.py tests/test_agent_chat_api.py tests/test_agent_chat_stream.py tests/test_local_model_profiles.py tests/test_parent_model_profiles.py -q
```

结果：97 passed，5 条已有 FastAPI 生命周期弃用提示。

前端命令（frontend 目录）：

```sh
npm run test:i18n
npm run build
```

结果：48 项检查通过；TypeScript 和 Vite 构建通过，存在约 500 kB JS 包体积提示。`git diff --check` 通过，原有 1019 项中英文翻译保留，新总数 1042。

真实主应用 `/agent-chat/models` 请求：OpenRouter 无 key 返回 HTTP 200、`Cache-Control: no-store`，得到 5 个筛选后的模型。无效地址返回 422，超出 32 KB 的请求返回 413，错误未回显 key。独立的输出模态、空 key、畸形目录和真实目录探针均通过。

浏览器验证：真实 OpenRouter 公共目录展示 5 个候选，选择后填入 Model ID；其他服务商通过合成目录和合成 Hello 响应验证了自动获取、服务商切换、地址修改后不自动发送旧 key、手填保留、候选选择、保存及刷新恢复。未使用真实付费 key，其他服务商的实际账户权限尚未验证。

验收截图：`/tmp/market-model-discovery-20261011/model-discovery-accepted.png`。
命令输出、真实主应用结果及探针结果保存在 `/tmp/market-model-discovery-20261011/`。演示启动命令见 [功能说明](model-discovery.md#local-ui-walkthrough)。开发过程见 [dev-notes](../dev-notes/model-discovery.md)。

## 修复与范围

独立真实请求发现旧连接层的 SNI 参数为 bytes，安装的 HTTPX/httpcore/anyio 链路要求字符串。修复后真实目录请求通过，增加了类型回归断言。worker 环境的 DNS 失败属于该环境限制，最终主应用和浏览器真实成功覆盖此前未验证状态。

原有端口 8000 服务未重启，未改动真实模型 key 文件，未推送或部署。验收临时服务与浏览器标签在结束时关闭。
