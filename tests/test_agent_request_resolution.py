"""C2/C7/C9 replace legacy text slot extraction with typed model-provided arguments."""
import pytest
from app.agent_tool_catalog import validate
from app.agent_workflow.routing import parse_route

@pytest.mark.parametrize('category',['A','B','C','D'])
def test_four_routes_parse_without_choosing_tools_by_words(category):
    assert parse_route({'category':category,'days_back':None}).category==category

@pytest.mark.parametrize('category',['saved_data','web_research','general','system_action','planner','X'])
def test_retired_or_unknown_labels_are_not_routes(category):
    with pytest.raises(ValueError):parse_route({'category':category,'days_back':None})

@pytest.mark.parametrize('args',[{'query':'news','days_back':0},{'query':'news','days_back':367},{'query':'news','max_results':0},{'query':'news','unknown':1}])
def test_tool_arguments_are_schema_validated(args):
    with pytest.raises(ValueError):validate('web_research',args)

@pytest.mark.parametrize('symbol',['AMD','MU','RBLX','ZZZZ','BRK.B'])
def test_symbol_format_does_not_require_a_company_directory(symbol):
    assert validate('query_stock_prices',{'symbol':symbol})['symbol']==symbol
