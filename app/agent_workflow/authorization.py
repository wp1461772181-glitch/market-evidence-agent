"""User-origin action guards; these checks never choose a route or select a tool."""
import re

# A request to analyse a relationship cannot authorize an import or queue mutation.
VERBS={
    'fetch_sec':r'抓取|获取|下载|拉取|拉去|同步|缓存|搜寻|fetch|download|sync|cache|pull',
    'refresh_sec':r'刷新|重新抓取|refresh|refetch',
    'analyze':r'分析|解读|研读|研报|analy[sz]e',
    'import':r'导入|入库|放入|放进|保存|存入|存到|加入|收录|录入|import|ingest|save',
    'import_sources':r'导入|入库|放入|放进|保存|存入|存到|加入|收录|录入|import|ingest|save',
    'ingest_web_source':r'导入|入库|放入|放进|保存|存入|存到|加入|收录|录入|import|ingest|save',
    'index':r'索引|嵌入|index|embed',
    'retry_document_index':r'索引|嵌入|重试|index|embed|retry',
    'forecast':r'预测|forecast|predict',
    'revise_forecast':r'修正|修订|更新预测|revise|revision',
    'start_historical_replay':r'回放|回测|重放|replay|backtest',
    'initialize_prices':r'初始化|抓取|获取|下载|initiali[sz]e|fetch|download',
    'scan_official':r'扫描|scan',
    'translate_content':r'翻译|translate',
    'review_knowledge_candidate':r'审核|核验|接受|驳回|批准|拒绝|review|approve|reject',
    'review_filing':r'审核|核验|接受|驳回|批准|拒绝|review|approve|reject',
    'add_to_research_queue':r'加入|添加|放入|add',
    'remove_from_research_queue':r'移除|移掉|移出|remove',
    'select_stock':r'切换|选择|打开|select|open|switch',
    'save_material_evidence':r'保存|上传|录入|收录|save|upload',
    'withdraw_knowledge_document':r'撤回|删除|撤销|withdraw|delete',
    'test_retrieval_diagnostics':r'诊断|测试|检索|diagnose|test',
    'remember':r'记住|记下|记得|以后|今后|往后|从现在起|每次都|一律|remember|from now on|always',
    'create_routine':r'每小时|每个小时|每天|每日|每周|工作日|定时|定期|例行|schedule|hourly|daily|every (?:hour|day|morning)',
    'update_routine':r'改|修改|编辑|调整|换成|暂停|停用|停掉|启用|恢复|update|edit|change|pause|resume',
    'delete_routine':r'删除|删掉|删了|去掉|撤掉|delete|remove',
    'forget':r'忘掉|忘记|忘了|删掉|删除|去掉|forget|delete',
}
# "以后别再用万元" is a standing instruction, not a refusal: for remember these are the request itself.
_STANDING_INSTRUCTION=re.compile(r'(?:以后|今后|往后|从现在起)?(?:都)?(?:别再|不要再|不再|别用|不要用)')
_REMEMBER_REFUSED=re.compile(r'(?:不要|别|不用|无需|不必)(?:帮我)?(?:记住|记下|记)|do not remember|don.t remember',re.I)
NEGATION=re.compile(r'(?:不要|别|勿|无需|不用|不能|不想|禁止|切勿|请勿|千万别|do not|don.t|never|without)[^，。；;.!?\n]{0,35}',re.I)
_POLITE_NON_NEGATION=re.compile(r'不用(?:客气|担心|多说|慌|犹豫|管|急|在意|考虑|理会|查其他|看其他|管其他|确认)|别(?:客气|担心|急|管)|无需(?:向我)?确认|不需确认',re.I)

# 改/删 alone is any edit: a routine change also names what it changes.
_ROUTINE_NOUN=re.compile(r'例行|任务|定时|每小时|每天|每日|工作日|routine|schedule',re.I)

def routine_ask(name,words):
    """The owner's own words ask to create, change or delete a routine (the card is still the guard)."""
    if name=='create_routine':return authorized(name,words)
    return bool(_ROUTINE_NOUN.search(words)) and authorized(name,words)

def authorized(name,words):
    pattern=VERBS.get(name)
    if not pattern:return False
    if name=='remember' and _REMEMBER_REFUSED.search(words):return False
    if name=='remember' and _STANDING_INSTRUCTION.search(words):return True
    # Token boundaries for English avoid authorizing "import" inside "important".
    verbs=re.compile('|'.join(r'(?<![A-Za-z])'+p+r'(?![A-Za-z])' if p.isascii() else p for p in pattern.split('|')),re.I)
    clean_words=_POLITE_NON_NEGATION.sub('',words)
    matches=list(verbs.finditer(clean_words))
    if not matches:return False
    return any(not any(n.start()<=v.start()<n.end() for n in NEGATION.finditer(clean_words)) for v in matches)

def vetoed(name,words):
    """Negative veto only. This function can NEVER grant an operation."""
    pattern=VERBS.get(name)
    if not pattern:return False
    if name=='remember':return bool(_REMEMBER_REFUSED.search(words))
    clean_words=_POLITE_NON_NEGATION.sub('',words)
    verb_pattern='|'.join(r'(?<![A-Za-z])'+p+r'(?![A-Za-z])' if p.isascii() else p for p in pattern.split('|'))
    has_negated = any(re.search(verb_pattern,n.group(0),re.I) for n in NEGATION.finditer(clean_words))
    if has_negated and authorized(name, words):
        return False
    return has_negated

