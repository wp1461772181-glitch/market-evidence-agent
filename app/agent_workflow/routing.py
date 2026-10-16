"""Validated four-class routing contract; tool choice belongs to the agent."""
from __future__ import annotations
import json
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field

class Routing(BaseModel):
    model_config=ConfigDict(extra='forbid')
    category:str=Field(pattern='^[ABCD]$')
    query:str=Field(default='',max_length=400)
    symbol:str|None=None
    company:str|None=None
    company_mention:str|None=Field(default=None,max_length=160,description='Exact company name copied from user words or verified referents. For a translated issuer name, retain the original mention here and use the corresponding English issuer name in company; the server verifies the ticker against official data.')
    metrics:list[Literal['revenue','profit','eps','cash_flow','debt','price','date','forecast']]=Field(default_factory=list,max_length=6,description='Canonical requested metrics only. Publication/report dates use date; saved stock prices use price. Do not invent metric names or infer profit merely from earnings-news wording.')
    period:str|None=Field(default=None,max_length=160,description='Only a fiscal/report period explicitly requested by the user. Do not infer a current year or quarter from latest/recent; publication freshness belongs in days_back.')
    financial_only:bool=Field(default=False,description="True for financial reports/财报; restrict saved latest report to 10-K or 10-Q, excluding 8-K notices.")
    latest:bool=False
    form:Literal['10-K','10-Q','8-K']|None=Field(default=None,description='Only an explicitly requested form. For latest 财报 with no form, leave null and use financial_only=true to compare 10-K and 10-Q together.')
    days_back:int|None=Field(...,ge=1,le=366,description='Required. For recent/latest news use 7; half a month uses 15; explicit duration overrides; null only if no news freshness window was requested.')
    max_results:int=Field(default=5,ge=1,le=5)


def parse_route(value):
    return Routing.model_validate(json.loads(value) if isinstance(value,str) else value)
