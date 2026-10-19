"""Reported XBRL figures as rows a chat answer can cite.

A filing's XBRL states each figure with its own period, but the stored ``fiscal_year`` is the
filing's (a FY2026 10-K labels its FY2025 comparative column 2026 too), so the period a figure
belongs to is worked out from its dates and the company's fiscal year end.
"""
from __future__ import annotations
from datetime import date

METRIC_LABELS_ZH={'revenue':'营业收入','cost_of_revenue':'营业成本','gross_profit':'毛利','operating_income':'营业利润',
 'net_income':'净利润','operating_cash_flow':'经营活动现金流','total_assets':'总资产','cash_and_equivalents':'现金及现金等价物',
 'total_liabilities':'总负债','stockholders_equity':'股东权益'}
# Only pre-fetches evidence for a metric the user named; it never decides what the answer says.
XBRL_METRIC_TERMS={
 'revenue':('营收','营业收入','收入','总收入','总营收','revenue','revenues','total revenue','sales','net sales'),
 'cost_of_revenue':('营业成本','收入成本','销售成本','cost of revenue','cost of sales'),
 'gross_profit':('毛利','毛利润','gross profit','gross margin'),
 'operating_income':('营业利润','经营利润','运营利润','operating income','operating profit'),
 'net_income':('净利润','净收入','净利','net income','net profit','net earnings'),
 'operating_cash_flow':('经营活动现金流','经营现金流','经营活动产生的现金流量净额','operating cash flow','cash from operations','cash flow from operations'),
 'total_assets':('总资产','资产总额','total assets'),
 'cash_and_equivalents':('现金','现金余额','现金及现金等价物','cash','cash and cash equivalents'),
 'total_liabilities':('总负债','负债总额','total liabilities'),
 'stockholders_equity':('股东权益','净资产','所有者权益','stockholders equity',"stockholders' equity",'shareholders equity'),
}
_QUALIFIERS=('最近一期','最新一期','全年','年度','季度','单季','本期','上期','合并','总','的')
_SPANS=((80,100,'quarter'),(170,190,'ytd6'),(260,280,'ytd9'),(350,380,'annual'))


def _day(value):return date.fromisoformat(str(value)[:10]) if value else None


def xbrl_metrics_for(terms):
    """The XBRL metrics a list of user-worded metrics names exactly (after time qualifiers)."""
    found=[]
    for raw in terms or []:
        text=' '.join(str(raw or '').split()).casefold()
        candidates=[text]
        while True:
            word=next((w for w in _QUALIFIERS if candidates[-1].startswith(w) and len(candidates[-1])>len(w)),None)
            if word is None:break
            candidates.append(candidates[-1][len(word):].strip())
        for metric,words in XBRL_METRIC_TERMS.items():
            if any(c in {w.casefold() for w in words} for c in candidates) and metric not in found:found.append(metric)
    return found


def _year_end_month(document_end,dei_fiscal_period):
    if dei_fiscal_period in {'Q1','Q2','Q3'}:return (document_end.month-1+3*(4-int(dei_fiscal_period[1])))%12+1
    return document_end.month


def period_label(period,*,document_end,dei_fiscal_year,dei_fiscal_period):
    start=_day(period.get('start'));end=_day(period.get('end'))
    fye=_year_end_month(document_end,dei_fiscal_period)
    def fiscal_year(day):return day.year if day.month<=fye else day.year+1
    offset=(int(dei_fiscal_year)-fiscal_year(document_end)) if dei_fiscal_year else 0
    year=fiscal_year(end)+offset
    quarter=((end.month-fye-1)%12)//3+1
    if start is None:span='instant'
    else:
        days=(end-start).days
        span=next((name for low,high,name in _SPANS if low<=days<=high),'other')
    fiscal='FY' if span=='annual' or (span=='instant' and quarter==4) else f'Q{quarter}'
    dates=f'{start.isoformat()} 至 {end.isoformat()}' if start else ''
    if span=='annual':text=f'FY{year}（{dates}）'
    elif span=='quarter':text=f'FY{year} Q{quarter}（{dates}，3 个月）'
    elif span=='ytd6':text=f'FY{year} 上半年累计（{dates}，6 个月）'
    elif span=='ytd9':text=f'FY{year} 前三季度累计（{dates}，9 个月）'
    elif span=='instant':text=f'截至 {end.isoformat()}（FY{year} '+('年末' if quarter==4 else f'Q{quarter} 末')+'，时点数）'
    else:text=dates
    return {'fiscal_year':year,'fiscal_period':fiscal,'span':span,'label':text}


def _excerpt(figure,period,label,filing):
    value=int(period['value']);unit=figure.get('unit') or period.get('unit') or 'USD'
    zh=METRIC_LABELS_ZH.get(figure.get('metric'),figure.get('label') or figure.get('metric'))
    amount=f'{value:,} {unit}'
    if unit=='USD':amount+=f'，约 {value/1e8:,.2f} 亿美元，即 {value/1e6:,.0f} 百万美元'
    return (f"{zh}（{figure.get('label')}，{figure.get('concept')}）{label['label']}：{amount}。"
            f"来源：{filing.get('symbol')} {filing.get('form')}，档案号 {filing.get('accession_number')}，"
            f"{str(filing.get('filed_at'))[:10]} 提交，XBRL 披露值。")


def figure_rows(filing,*,metrics=None,fiscal_year=None,fiscal_period=None):
    """Every figure one saved filing reported, as citable rows, filtered by metric and period."""
    figures=[f for f in (filing.get('content_financials') or []) if isinstance(f,dict)]
    periods=[p for f in figures for p in (f.get('periods') or []) if isinstance(p,dict) and p.get('end') and p.get('value') is not None]
    if not periods:return []
    document_end=max(_day(p['end']) for p in periods)
    current=next((p for p in periods if _day(p['end'])==document_end),periods[0])
    dei_year,dei_period=current.get('fiscal_year'),current.get('fiscal_period')
    rows=[]
    for figure in figures:
        if metrics and figure.get('metric') not in metrics:continue
        for period in figure.get('periods') or []:
            if not isinstance(period,dict) or not period.get('end') or period.get('value') is None:continue
            try:label=period_label(period,document_end=document_end,dei_fiscal_year=dei_year,dei_fiscal_period=dei_period)
            except (TypeError,ValueError):continue
            if fiscal_year is not None and label['fiscal_year']!=fiscal_year:continue
            if fiscal_period is not None and (label['fiscal_period']!=fiscal_period or label['span'] not in {'annual','quarter'}):continue
            zh=METRIC_LABELS_ZH.get(figure.get('metric'),figure.get('label'))
            rows.append({'source_id':str(filing.get('id')),'symbol':filing.get('symbol'),'form':filing.get('form'),
                'accession_number':filing.get('accession_number'),'filed_at':str(filing.get('filed_at'))[:10],
                'source_url':filing.get('content_source_url') or filing.get('source_url'),'metric':figure.get('metric'),
                'label':figure.get('label'),'concept':figure.get('concept'),'unit':figure.get('unit') or period.get('unit'),
                'value':int(period['value']),'period_start':period.get('start'),'period_end':period.get('end'),
                'fiscal_year':label['fiscal_year'],'fiscal_period':label['fiscal_period'],'period_span':label['span'],
                'period_label':label['label'],'source_type':'reported_figure',
                'title':f"{filing.get('symbol')} {filing.get('form')} {str(filing.get('filed_at'))[:10]} · {zh} {label['label'].split('（')[0]}",
                'excerpt':_excerpt(figure,period,label,filing)})
    return rows
