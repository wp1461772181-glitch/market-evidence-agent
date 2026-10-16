"""C2: a classification validates labels; it never chooses a tool by lexical patterns."""
import pytest
from app.agent_workflow.routing import parse_route

@pytest.mark.parametrize('value',[None,{},'not-json',{'category':'A','sql':'drop table'}])
def test_invalid_classification_is_rejected(value):
    with pytest.raises((ValueError,TypeError)):parse_route(value)

def test_structured_time_span_and_latest_selector_survive_classification():
    route=parse_route({'category':'A','query':'美光新闻','company':'美光','symbol':'MU','days_back':15,'latest':True})
    assert route.days_back==15 and route.latest
