# Primary 最终独立验收 · 2026-10-11

## 结论

**本地对话文件功能验收通过；整轮 RAG 验收仍为部分完成。** 无提交、推送、线上部署或高并发测试。本报告覆盖执行者此前“通过”的概括。

## 已实际确认

- 本地文件：Chrome 原生保存选择器新建真实磁盘 JSON；两段对话自动保存且旧段保留，刷新后重新导入可切换查看。只读模式、无效文件保留旧内容、导出带工具轨迹、390px 无横向溢出。详见 `docs/chat-local-files-acceptance.md`。此 UI 检查的模型与工具明确为测试替身，实际聊天路由、SQL、SSE、浏览器与磁盘未替换。
- Primary 自行运行前端测试28 passed、生产构建成功、diff check成功。
- Primary 最后独立四项边界检查：来源观察时间变化、候选审核时间变化、ANN 前置历史版本筛选共3 passed；FAQ 前置筛选1 failed。日志 `/tmp/market-rag-accept-20261011/primary-final-boundaries.txt`。历史候选挤占探针由原来的 FAIL 转 GREEN。
- 执行者现有常规后端套件574 passed、1 skipped；真实BGE/Milvus/PG与provider的组合测试2 passed。这些测试缺少 FAQ 实际召回断言，不能替代下述功能验收。

## 仍未通过的具体问题

`app/knowledge/repository.py` 给无业务发布时间的 FAQ 文档补首次导入时间，但创建 chunk 时仍写 `source.available_at=None`。`_eligible_ann_ids` 要求 chunk availability 非空，故已有 FAQ 被排除。独立数据库探针验证 `_current_rows` 可接受该 FAQ，但 ANN 前置集合为空；问题是时间字段传递回归，不能归因于 dense 模型不足或样本小。

Primary 加入“工具必须真的返回可信度星级 FAQ”断言后，用现存正常 DeepSeek 配置再次运行实际 BGE/Milvus/PG/聊天SQL/SSE链路，**1 failed**：FAQ tool data=[]。只记录真实测试请求/结果，未输出 API key。日志 `/tmp/market-rag-accept-20261011/primary-live-final.txt`，机器报告 `/tmp/market-rag-accept-20261011/primary-live-final/live-rag-acceptance.json`。模型和检索均无替换；此前执行者四例报告保持原路径未覆盖。

最后真实评估 artifact：原正例15/20=.75、裁定16/21=.7619、补充正例2/4=.50，raw ANN21/21；`heldout_recall_met=false`。补充数据的缺失事实/私有资料请求与强制空召回标签仍须区分，不可把它们全部称为“无关问题”。原 `corpus.json`、`queries.json`、`adjudications.json` 与本轮开始的SHA256逐字节相同。

修复应统一 FAQ 文档与 chunk 的已知首次入库时间，并考虑已存在、被幂等入库跳过的 null-time chunk 如何显式修复，然后重跑非空召回断言及实际质量门槛。不需要更换固定技术栈，也不能通过调整 heldout 或只看 pytest exit code宣称完成。当前 RAG 保持默认关闭；隔离的真实RAG浏览器服务未交付。此前“8000占用所以8015无法用”的说法不足以构成技术阻断：前端进程支持 `VITE_API_BASE_URL`，primary 已用该变量与8015独立文件UI harness完成走查；真正尚未交付的是后端RAG隔离 launcher。

## 复现

```bash
cd /Users/wupeng/monash-ai/projects/market-evidence-agent
PUBLIC_API_MODE=false .venv-rag/bin/python -m pytest -q -p tests.conftest \
  /tmp/market-rag-accept-20261011/test_primary_round.py \
  /tmp/market-rag-accept-20261011/test_historical_ann.py \
  /tmp/market-rag-accept-20261011/test_faq_ann_boundary.py
cd frontend
npm run test:i18n
npm run build
```

本轮临时验收文件位于 `/tmp/market-rag-accept-20261011/`，临时目录不是长期测试资产。处理原始开发数据或更新线上服务均未执行。
