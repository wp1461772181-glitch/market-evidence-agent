const requestIdPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const actionChoices = [
  ['抓取 SEC 原文','fetch_sec'], ['生成 AI 分析','analyze'], ['导入已保存资料','import'],
  ['建立索引','index'], ['创建预测','forecast'], ['查询任务状态','status'], ['刷新 SEC 原文','refresh_sec']
].map(([label,value])=>({label,value}));
const titles = {missing_action_type:'请选择要执行的操作',missing_action_source:'请指定已保存的来源',
  missing_action_symbol:'请指定股票代码',missing_task_id:'请提供任务 ID',missing_comparison_symbol:'请选择另一家公司',
  missing_stock_focus:'请选择查询的股票',symbol_suggestion:'请确认股票代码',period_requires_accession:'请提供报告 accession',ambiguous_web_url:'请提供公开网页链接',reference_unavailable:'请选择来源或原网页',reference_company_required:'请告诉我是哪家公司',
  reference_item_unavailable:'请指明是哪一条结果',company_not_in_queue:'这只股票不在研究队列里',
  confirm_write:'请确认要执行的写操作',choose_write_action:'请选择要执行的写操作'};

export function normalizeClarification(value) {
  if (!value || typeof value !== "object" || !requestIdPattern.test(value.request_id)) return null;
  const choices = Array.isArray(value.choices) ? value.choices.filter(choice =>
    choice && typeof choice.label === "string" && choice.label.length <= 120 &&
    typeof choice.value === "string" && /^[A-Za-z0-9 ._:-]{1,80}$/.test(choice.value)
  ).slice(0, 10) : [];
  const reason=String(value.reason ?? "").slice(0, 80);
  return { request_id: value.request_id, reason, choices: choices.length ? choices : reason==='missing_action_type' ? actionChoices : choices };
}

export function selectedClarification(clarification, value) {
  if (!clarification?.choices.some(choice => choice.value === value)) throw Error("invalid_clarification_choice");
  return { message: value, pendingRequestId: clarification.request_id };
}

export function manualClarification(clarification, input) {
  const message=typeof input==='string'?input.trim():'';
  if (!requestIdPattern.test(clarification?.request_id??'') || !message || message.length>4000) throw Error('invalid_clarification_reply');
  return {message,pendingRequestId:clarification.request_id};
}

export function clarificationTitle(clarification) { return titles[clarification?.reason]??'请补充信息'; }

export function isActiveClarification(messages,index,readOnly) {
  const message=messages[index];
  return !readOnly && index===messages.length-1 && message?.role==='assistant' && message?.status==='waiting_for_input' && Boolean(message.clarification);
}
