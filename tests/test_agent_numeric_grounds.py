"""The numeric check asks whether a figure has grounds in its source, not whether it is spelled the same."""
from app.agent_workflow.evidence import unsupported_number_values

STATEMENT={'ordinal':1,'source_type':'official_filing','title':'msft-20260331.htm','source_url':'https://www.sec.gov/Archives/edgar/data/789019/000119312526191507/msft-20260331.htm',
    'quote':'INCOME STATEMENTS\n(In millions, except per share amounts)\nThree Months Ended March 31,\n2026\n2025\nOperating income\n38,398\n32,000'}
FIGURE={'ordinal':1,'source_type':'reported_figure','title':'MSFT 10-K','quote':'营业收入（Revenue）FY2026：331,839,000,000 USD。'}
CALC={'ordinal':2,'source_type':'calculation','title':'可复核计算','quote':'{"expression":"(a-b)/b*100","value":17.79}'}
NAMES={'ordinal':1,'source_type':'official_filing','title':'0001193125-26-323660',
    'quote':'Microsoft Cloud revenue includes Microsoft 365 Commercial cloud. See Exhibit 99.1 and msft-20260331.htm. Revenue 331,839.'}

def test_a_figure_from_a_table_in_millions_may_be_restated_in_yi_or_billions():
    assert unsupported_number_values('FY2025 Q3 营业利润为 320 亿美元 [1]',[STATEMENT])==[]
    assert unsupported_number_values('Operating income was $32.0 billion [1]',[STATEMENT])==[]
    assert unsupported_number_values('营业利润为 32,000 百万美元 [1]',[STATEMENT])==[]

def test_a_rounded_figure_matches_at_its_own_precision():
    assert unsupported_number_values('营业收入约 3,318 亿美元 [1]',[FIGURE])==[]
    assert unsupported_number_values('Revenue was about $331.8 billion [1]',[FIGURE])==[]
    assert unsupported_number_values('营业收入约 3,300 亿美元 [1]',[FIGURE])==[]
    assert unsupported_number_values('营业收入约 3,000 亿美元 [1]',[FIGURE])==[3e11]

def test_a_rounded_percentage_matches_the_calculation():
    assert unsupported_number_values('同比约 +17.8% [2]',[FIGURE,CALC])==[]
    assert unsupported_number_values('同比约 18% [2]',[FIGURE,CALC])==[]

def test_identifiers_and_product_names_are_not_figures_when_the_evidence_shows_them():
    assert unsupported_number_values('Microsoft 365 商业云收入包含在内 [1]',[NAMES])==[]
    assert unsupported_number_values('档案号 0001193125–26–323660 [1]',[NAMES])==[]
    assert unsupported_number_values('来源文件 msft-20260331.htm [1]',[NAMES])==[]
    assert unsupported_number_values('见 Exhibit 99.1 [1]',[NAMES])==[]
    assert unsupported_number_values('这是 20-F 年报 [1]',[NAMES])==[]

def test_a_name_with_a_number_the_evidence_never_shows_is_still_checked():
    assert unsupported_number_values('Maia 300 芯片 [1]',[NAMES])==[300.0]

def test_an_invented_amount_is_still_rejected():
    assert unsupported_number_values('营收 500 亿美元 [1]',[NAMES])==[5e10]
    assert unsupported_number_values('Revenue 331,750 [1]',[NAMES])==[331750.0]
