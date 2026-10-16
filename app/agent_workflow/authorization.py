"""User-origin action guards; these checks never choose a route or select a tool."""
import re

# A request to analyse a relationship cannot authorize an import or queue mutation.
VERBS={
    'fetch_sec':r'抓取|获取|下载|fetch|download',
    'refresh_sec':r'刷新|重新抓取|refresh|refetch',
    'analyze':r'分析|analy[sz]e',
    'import':r'导入|入库|放入|放进|保存|import|ingest|save',
    'import_sources':r'导入|入库|放入|放进|保存|import|ingest|save',
    'ingest_web_source':r'导入|入库|放入|放进|保存|import|ingest|save',
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
    'open_page':r'切换|打开|open|switch',
}
NEGATION=re.compile(r'(?:不要|别|勿|无需|不用|不能|不想|禁止|do not|don.t|never|without)[^，。；;.!?\n]{0,50}',re.I)

def authorized(name,words):
    pattern=VERBS.get(name)
    if not pattern:return False
    # Token boundaries for English avoid authorizing "import" inside "important".
    verbs=re.compile('|'.join(r'(?<![A-Za-z])'+p+r'(?![A-Za-z])' if p.isascii() else p for p in pattern.split('|')),re.I)
    matches=list(verbs.finditer(words))
    if not matches:return False
    return all(not any(n.start()<=v.start()<n.end() for n in NEGATION.finditer(words)) for v in matches)
