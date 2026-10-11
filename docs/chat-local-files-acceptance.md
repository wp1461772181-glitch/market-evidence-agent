# 本地对话文件独立验收 · 2026-10-11

本地源码验收通过，尚未部署到 GitHub Pages。

## 实际浏览器与磁盘

Chrome 原生“保存”选择器成功新建并连接 `/tmp/market-rag-accept-20261011/primary-chat-archive.json`。通过页面发送两段独立对话，界面分别报告成功保存时间，磁盘文件为有效 UTF-8 JSON，含 **2 个会话、每个 2 条消息、每个 1 条完整工具轨迹**。新建第二段对话未清空第一段。

刷新页面后，实际 PostgreSQL 聊天接口恢复当前第二段对话。通过原生“打开”选择器重新导入上述磁盘文件，页面显示两个会话选项，能切回第一段；阅读模式禁用发送。导入内容未替代服务端可信上下文。

额外检查：导入无效 JSON 后错误提示出现，已有阅读内容保持；“返回当前对话”恢复实时视图。导出 JSON 的真实下载文件含 1 个合成会话、2 条消息、1 条工具轨迹。自动保存文件中不含验收模型配置里的测试 key。390×844 页面 document.scrollWidth=390，归档按钮无横向溢出，测试后已恢复 viewport。

**证据边界：** 文件 UI 验收使用 `/tmp/market-rag-accept-20261011/file_ui_fixture_server.py`，模型与工具为明确标注的测试替身；真实现有聊天路由、SSE、会话权限、PostgreSQL 消息持久化与浏览器写盘未替换。该检查不证明真实 DeepSeek 或 RAG 质量，不属于生产服务启动方式。测试库仅为自动生成的 `test_*` 数据库，服务仅监听 loopback。

## 自动检查

- `cd frontend && npm run test:i18n`：28 passed。
- `cd frontend && npm run build`：成功。
- 包含 UTF-8/工具轨迹往返、无效/超限文件、队列切换目标、失败中止流、多个会话保留及创建时间稳定的测试。

## 使用限制

自动写盘需要支持 File System Access API 的安全浏览器页面；当前文件句柄只保留到本页面关闭/刷新，因此刷新后需重新连接文件，已有磁盘记录不会消失。不支持的浏览器使用导出/导入。归档上限 5 MB、10,000 条消息。文件不存 API key、模型配置、会话访问凭据、服务端会话 ID 或隐藏推理。

截图和独立检查原始记录在 `/tmp/market-rag-accept-20261011/`，包括 `local-chat-autosaved.jpg`、`local-archive-reopened.jpg` 与 `frontend-primary-acceptance.md`。
