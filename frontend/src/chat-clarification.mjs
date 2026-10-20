const requestIdPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const actionChoices = [
  ['抓取 SEC 原文','fetch_sec'], ['生成 AI 分析','analyze'], ['导入已保存资料','import'],
  ['建立索引','index'], ['创建预测','forecast'], ['查询任务状态','status'], ['刷新 SEC 原文','refresh_sec']
].map(([label,value])=>({label,value}));
const titles = {write_confirmation:'请确认以下操作',missing_information:'请补充信息',missing_action_type:'请选择要执行的操作',missing_action_source:'请指定已保存的来源',
  missing_action_symbol:'请指定股票代码',missing_task_id:'请提供任务 ID',missing_comparison_symbol:'请选择另一家公司',
  missing_stock_focus:'请选择查询的股票',symbol_suggestion:'请确认股票代码',period_requires_accession:'请提供报告 accession',ambiguous_web_url:'请提供公开网页链接',reference_unavailable:'请选择来源或原网页',reference_company_required:'请告诉我是哪家公司',
  reference_item_unavailable:'请指明是哪一条结果',
  confirm_write:'请确认要执行的写操作',choose_write_action:'请选择要执行的写操作',choose_revision_sources:'请选择用于修订的材料'};

export function normalizeClarification(value) {
  if (!value || typeof value !== "object" || !requestIdPattern.test(value.request_id)) return null;
  const choices = Array.isArray(value.choices) ? value.choices.filter(choice =>
    choice && typeof choice.label === "string" && choice.label.length <= 120 &&
    typeof choice.value === "string" && (choice.value.startsWith("reply:") ? choice.value.length <= 405 : /^[A-Za-z0-9 ._:-]{1,100}$/.test(choice.value))
  ).slice(0, 10) : [];
  const reason=String(value.reason ?? "").slice(0, 80);
  const card={ request_id: value.request_id, reason, choices: choices.length ? choices : reason==='missing_action_type' ? actionChoices : choices };
  if (typeof value.question==='string') card.question=value.question.slice(0,400);
  if (value.display && typeof value.display==='object') {
    const display={};
    if(value.display.agent_suggestion===true) display.agent_suggestion=true;
    for (const key of ['title','saved_date','web_date','alternative']) if(typeof value.display[key]==='string') display[key]=value.display[key].slice(0,600);
    const publicLink=url=>{try{const p=new URL(url);return p.protocol==='https:'&&!p.username&&!p.password?p.href:undefined}catch{return undefined}};
    if(typeof value.display.url==='string') display.url=publicLink(value.display.url);
    const fields=new Set(['title','symbol','url','source_id','source_type','source_refs','accession_number','version_id','record_id','document_id','candidate_id','days','max_tasks','form','decision_date','kind','approved','saved_date','web_date']);
    if(Array.isArray(value.display.targets)) display.targets=value.display.targets.slice(0,8).map(target=>Object.fromEntries(Object.entries(target??{}).filter(([key,v])=>fields.has(key)&&v!=null).map(([key,v])=>[key,key==='url'?publicLink(String(v))??'':typeof v==='object'?JSON.stringify(v).slice(0,1200):String(v).slice(0,2048)])));
    card.display=display;
  }
  // A revision card lists materials to tick; its button sends the capability plus the ticked values.
  const options=Array.isArray(value.options)?value.options.filter(option=>option&&typeof option.value==='string'&&/^\d{1,2}$/.test(option.value)
    &&typeof option.label==='string'&&option.label.trim()).slice(0,10).map(option=>({value:option.value,label:option.label.slice(0,300),
    ...(typeof option.detail==='string'?{detail:option.detail.slice(0,200)}:{})})):[];
  if (options.length) {
    const bound=(raw,fallback)=>Math.max(1,Math.min(options.length,Number.isInteger(raw)?raw:fallback));
    card.options=options;
    card.select={min:bound(value.select?.min,1),max:bound(value.select?.max,options.length)};
  }
  return card;
}

export function selectedOptions(clarification, values) {
  const confirm=clarification?.choices?.find(choice=>choice.value.startsWith('confirm:'));
  const listed=(clarification?.options??[]).map(option=>option.value);
  const picked=Array.isArray(values)?values:[];
  const limits=clarification?.select??{min:1,max:listed.length};
  if (!confirm||!picked.length||new Set(picked).size!==picked.length||picked.some(value=>!listed.includes(value))
      ||picked.length<limits.min||picked.length>limits.max) throw Error('invalid_clarification_choice');
  const ordered=listed.filter(value=>picked.includes(value));
  return {message:`${confirm.value}:${ordered.join('.')}`,pendingRequestId:clarification.request_id,label:`用所选的 ${ordered.length} 份材料修订`};
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

export function clarificationTitle(clarification) { return clarification?.display?.agent_suggestion===true?'Agent 建议：请决定是否批准':titles[clarification?.reason]??'请补充信息'; }

export function isActiveClarification(messages,index,readOnly) {
  const message=messages[index];
  return !readOnly && index===messages.length-1 && message?.role==='assistant' && ['waiting_for_input','waiting_for_user'].includes(message?.status) && Boolean(message.clarification);
}
