#!/usr/bin/env python3
"""Run the explicit real four-strategy evaluation against disposable test_* PostgreSQL."""
import os,sys
sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sqlalchemy.engine import make_url
url=os.getenv('TEST_DATABASE_URL','postgresql+psycopg://market_evidence:market_evidence_dev@127.0.0.1:55432/test_market_rag_ch04_eval')
if not make_url(url).database or not make_url(url).database.startswith('test_'):
    raise SystemExit('Refusing evaluation: TEST_DATABASE_URL must name a test_* database')
os.environ['TEST_DATABASE_URL']=url
os.environ['KNOWLEDGE_CH04_REAL']='1'
import pytest
raise SystemExit(pytest.main(['-q','-s','tests/test_knowledge_ch04_real.py::test_four_real_strategies_and_write_report']))
