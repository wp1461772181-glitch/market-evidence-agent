/** Chinese display names for the agent's tools.  The badge used to render the identifier with its
 * underscores replaced ("query stock prices"), which reads like code rather than a name.  The values
 * are the i18n source strings: the badge wraps them in t() so en-US.json supplies the English name.
 * tests/test_agent_tool_labels.py fails when a tool in the catalogue has no label here.
 */
export const TOOL_LABELS = {
  query_stock_prices: "已保存行情",
  query_latest_forecast: "已保存预测",
  query_materials: "已保存材料",
  query_sec_filings: "SEC 申报",
  query_financials: "已披露财务数字",
  query_research_faq: "研究问答库",
  query_material_analysis: "已保存的 AI 分析",
  search_material_analysis: "AI 分析检索",
  web_research: "公开网页检索",
  search_knowledge: "已存知识检索",
  read_workspace: "工作区读取",
  query_system_status: "任务状态",
  query_research_queue: "研究队列",
  add_to_research_queue: "加入研究队列",
  remove_from_research_queue: "移出研究队列",
  select_stock: "切换股票",
  open_page: "打开页面",
  ask_user: "询问用户",
  use_result_set: "复用已返回结果",
  request_action: "请求操作授权",
  calculate: "可复核计算",
  test_retrieval_diagnostics: "检索诊断",
  review_filing: "申报相关性核验",
  fetch_sec: "拉取 SEC 原文",
  read_history_citation: "取回历史引用",
  refresh_sec: "刷新 SEC 原文",
  analyze: "AI 材料分析",
  import: "导入知识库",
  import_sources: "批量导入来源",
  index: "构建向量索引",
  retry_document_index: "重试文档索引",
  forecast: "生成研究预测",
  revise_forecast: "修正预测",
  start_historical_replay: "历史回放",
  ingest_web_source: "保存网页为来源",
  save_material_evidence: "保存上传材料",
  withdraw_knowledge_document: "撤回知识文档",
  initialize_prices: "初始化历史行情",
  scan_official: "扫描官方申报目录",
  translate_content: "内容翻译",
  review_knowledge_candidate: "审核知识候选",
  remember: "记住偏好",
  forget: "忘掉偏好",
  recall_conversations: "回想往事",
};

/** The display name of a tool, falling back to its identifier when unknown. */
export function toolLabel(name) {
  const key = typeof name === "string" ? name : "";
  return TOOL_LABELS[key] ?? key.replaceAll("_", " ");
}
