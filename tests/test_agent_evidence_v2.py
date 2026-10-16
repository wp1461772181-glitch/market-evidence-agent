import pytest
from app.agent_workflow.evidence import gate_row,deterministic_confidence_gate

@pytest.mark.parametrize('claim,quote,blocked',[
 ('Tim Cook 离任[1]。','The achievement follows the departure of longtime chief executive Tim Cook.',False),
 ('Tim Cook 辞职[1]。','The departure of Tim Cook was announced.',True),
 ('Tim Cook 离任[1]。','Tim Cook resigned as chief executive.',False),
 ('Tim Cook 离任[1]。','Tim Cook has not resigned.',True),
 ('Tim Cook 没有离任[1]。','The departure of Tim Cook was announced.',True),
 ('John Ternus 上任 CEO[1]。','New chief executive John Ternus takes charge.',False),
 ('John Ternus 接任 CEO[1]。','New chief executive John Ternus takes charge.',False),
 ('John Ternus 被任命为 CEO[1]。','Chief executive John Ternus takes charge.',True),
 ('John Ternus 已经上任 CEO[1]。','John Ternus was appointed CEO and will take office next year.',True),
])
def test_departure_and_role_start_translations_keep_event_strength_and_polarity(claim,quote,blocked):
    from app.agent_workflow.evidence import unsupported_claims
    assert unsupported_claims(claim,[{'ordinal':1,'quote':quote,'source_type':'web'}]) is blocked

@pytest.mark.parametrize('amount,blocked',[('5万亿美元',False),('7万亿美元',True),('0.5万亿美元',True)])
def test_trillion_compound_chinese_units_are_checked_instead_of_ignored(amount,blocked):
    from app.agent_workflow.evidence import unsupported_numbers
    assert unsupported_numbers(amount+'[1]',[{'ordinal':1,'quote':'Apple reached US$5 trillion.'}]) is blocked

@pytest.mark.parametrize('body',[
 'Apple FY2024 revenue was 10. Apple FY2025 profit was 20.',
 'Apple Q3 2024 revenue was 10. Apple Q1 2025 revenue was 20.',
 'Apple partners with Samsung. Samsung Q3 2025 revenue was 10.',
])
def test_company_period_and_metric_must_belong_to_same_claim(body):
    row={'source_type':'web','excerpt':body,'published_at':'2026-10-15T00:00:00Z'}
    assert gate_row('Apple Q3 2025 revenue',row,company='Apple',metrics=['revenue'],period='Q3 2025')[0] is False

@pytest.mark.parametrize('body',['Apple Q3 2025 revenue was 10.','Apple 2025 Q3 revenue was 10.','Apple 2025年第三季度 revenue was 10.'])
def test_equivalent_quarter_spellings_are_verified(body):
    assert gate_row('Apple Q3 2025 revenue',{'source_type':'web','excerpt':body},company='Apple',metrics=['revenue'],period='Q3 2025')[0]


def test_a_period_in_one_row_cannot_authorize_a_metric_in_another():
    assert not deterministic_confidence_gate('AAPL FY2025 revenue',{'ok':True,'data':[
        {'symbol':'AAPL','excerpt':'FY2025 earnings report'},
        {'symbol':'AAPL','excerpt':'FY2024 revenue is 10'},
    ]},verified_symbols=['AAPL'])[0]

@pytest.mark.parametrize('metric,row',[
 ('price',{'symbol':'AMD','close':100,'trading_date':'2026-10-15'}),
 ('date',{'symbol':'AMD','excerpt':'AMD annual report','filed_at':'2026-10-14'}),
 ('forecast',{'symbol':'AMD','probabilities':{'bearish':0.2,'neutral':0.3,'bullish':0.5}}),
])
def test_typed_fields_are_evidence_for_their_declared_metric(metric,row):
    assert gate_row('AMD',row,symbol='AMD',metrics=[metric])[0]

def test_exact_publication_window_rejects_eighth_day_and_future():
    from datetime import UTC,datetime,timedelta
    from app.freshness import published_within
    now=datetime(2026,10,15,12,tzinfo=UTC)
    assert published_within((now-timedelta(days=7)).isoformat(),7,now)
    assert not published_within((now-timedelta(days=7,seconds=1)).isoformat(),7,now)
    assert not published_within((now+timedelta(seconds=1)).isoformat(),7,now)

def test_saved_filing_cannot_use_filing_date_to_authorize_another_financial_year():
    row={'id':'saved','symbol':'AAPL','source_type':'official_filing','excerpt':'FY2024 revenue was 10. FY2025 profit was 20.','filed_at':'2025-01-01'}
    assert not gate_row('AAPL FY2025 revenue',row,symbol='AAPL',metrics=['revenue'],period='FY2025')[0]

@pytest.mark.parametrize('quote,claim',[
 ('Apple CEO did not resign.','Apple CEO resigned.[1]'),
 ('Apple did not acquire ExampleCo.','Apple acquired ExampleCo.[1]'),
 ('Apple CEO resigned.','Apple CEO did not resign.[1]'),
 ('苹果 CEO 并未离任。','苹果 CEO 已离任。[1]'),
 ('苹果没有收购 ExampleCo。','苹果已收购 ExampleCo。[1]'),
])
def test_cited_event_polarity_cannot_be_reversed(quote,claim):
    from app.agent_workflow.evidence import unsupported_claims
    assert unsupported_claims(claim,[{'ordinal':1,'source_type':'web','quote':quote}])

@pytest.mark.parametrize('quote,claim',[
 ('Apple CEO did not resign.','苹果 CEO 没有辞职。[1]'),
 ('Apple appointed a new CFO.','苹果任命了新 CFO。[1]'),
])
def test_supported_event_polarity_can_cross_languages(quote,claim):
    from app.agent_workflow.evidence import unsupported_claims
    assert not unsupported_claims(claim,[{'ordinal':1,'source_type':'web','quote':quote}])

@pytest.mark.parametrize('claim',['AMD 收盘价 999 美元。AMD 收盘价 100 美元 [1]。','AMD CEO resigned. Price is 100 dollars [1].'])
def test_missing_marker_cannot_bypass_amount_or_event_validation(claim):
    from app.agent_workflow.evidence import unsupported_numbers,unsupported_claims
    citations=[{'ordinal':1,'source_type':'saved_price','quote':'100'}]
    assert unsupported_numbers(claim,citations) or unsupported_claims(claim,citations)


@pytest.mark.parametrize('quote,claim',[
 ('$54.2B','营收为 542 亿美元 [1]。'),
 ('$68.3B','净现金为 683 亿美元 [1]。'),
 ('$1.5M','收入为 150 万美元 [1]。'),
 ('$2.5K','金额为 2500 美元 [1]。'),
 ('$54,200M','营收为 $54.2B [1]。'),
 ('54.2 billion dollars','营收为 $54,200M [1]。'),
])
def test_financial_currency_suffixes_preserve_units_across_languages(quote,claim):
    from app.agent_workflow.evidence import unsupported_numbers
    assert not unsupported_numbers(claim,[{'ordinal':1,'source_type':'web','quote':quote}])


@pytest.mark.parametrize('claim',['营收为 $99B [1]。','营收为 54.2 万美元 [1]。','营收为 54.2 美元 [1]。'])
def test_currency_suffix_support_does_not_allow_a_changed_value_or_unit(claim):
    from app.agent_workflow.evidence import unsupported_numbers
    assert unsupported_numbers(claim,[{'ordinal':1,'source_type':'web','quote':'Revenue was $54.2B.'}])
