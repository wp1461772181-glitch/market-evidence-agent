"""Tests for Route A mode sub-routing, RAG prelude gating, and multi-ticker symbols backfill."""
from __future__ import annotations

import asyncio
import uuid
import pytest

from app.agent_workflow import direct
from app.agent_workflow.routing import Routing, safe_parse_route
from app.agent_workflow.state import AgentRuntimeContext


NINE_TICKERS = ['AAPL', 'MSFT', 'NVDA', 'GOOGL', 'AMZN', 'META', 'TSLA', 'AVGO', 'TSM']


def _call(name: str, **arguments):
    return {
        'content': None,
        'tool_calls': [{'id': uuid.uuid4().hex, 'function': {'name': name, 'arguments': arguments}}],
    }


def test_safe_parse_route_handles_route_alias_and_invalid_mode():
    routing, defaulted = safe_parse_route({
        'route': 'A',
        'mode': 'prices',
        'symbols': NINE_TICKERS,
        'days_back': 180,
    })
    assert defaulted is False
    assert routing.category == 'A'
    assert routing.route == 'A'
    assert routing.mode == 'prices'
    assert routing.symbols == NINE_TICKERS
    assert routing.symbol == 'AAPL'

    # Missing or invalid mode safely falls back to 'none' (never opening RAG prelude)
    fallback, defaulted2 = safe_parse_route({
        'category': 'A',
        'mode': 'invalid_mode_xyz',
        'symbol': 'AAPL',
        'days_back': None,
    })
    assert defaulted2 is True
    assert fallback.mode == 'none'
    assert fallback.symbols == ['AAPL']
    assert fallback.symbol == 'AAPL'


def test_route_a_mode_rag_runs_search_knowledge_prelude():
    executed = []

    def execute(name, args):
        executed.append((name, args))
        if name == 'search_knowledge':
            return {
                'ok': True,
                'data': [{
                    'id': 'doc-1',
                    'symbol': 'AAPL',
                    'source_type': 'official_filing',
                    'title': 'AAPL 2025 10-K Supply Chain',
                    'filed_at': '2025-11-01',
                    'excerpt': 'Apple supply chain risk factors include single-source components.',
                }],
            }
        return {'ok': True, 'data': []}

    async def emit(_):
        pass

    ctx = AgentRuntimeContext(
        'custom', 'test', 'dummy',
        classifier=lambda _: {
            'category': 'A',
            'mode': 'rag',
            'symbol': 'AAPL',
            'symbols': ['AAPL'],
            'days_back': None,
        },
        decider=lambda *_: {'content': 'AAPL 10-K 披露了单一来源组件的供应链风险 [1]。'},
        executor=execute,
    )
    result = asyncio.run(direct.run_direct_turn('AAPL 最新 10-K 里关于供应链风险是怎么说的', ctx, emit=emit))
    assert result['status'] == 'succeeded'
    assert executed and executed[0][0] == 'search_knowledge'
    assert result['audit']['mode'] == 'rag'
    assert result['audit']['tool_bias'] == 'knowledge'


def test_route_a_mode_prices_skips_rag_prelude_and_backfills_nine_symbols():
    """When resolve extracts 9 tickers and classify drops symbols or only keeps 1,
    classify backfills all 9 tickers, skips search_knowledge prelude, and allows querying all 9."""
    executed = []

    def execute(name, args):
        executed.append((name, args))
        if name == 'query_stock_prices':
            sym = args['symbol']
            return {
                'ok': True,
                'data': [{
                    'id': f'price-{sym}',
                    'symbol': sym,
                    'close': 100.0 + NINE_TICKERS.index(sym) * 10.0,
                    'trading_date': '2026-10-15',
                }],
            }
        return {'ok': True, 'data': []}

    steps = iter([
        {
            'content': None,
            'tool_calls': [
                {'id': f'call-{sym}', 'function': {'name': 'query_stock_prices', 'arguments': {'symbol': sym, 'days': 180}}}
                for sym in NINE_TICKERS
            ],
        },
        _call(
            'calculate',
            expression='a - b',
            inputs=[
                {'name': 'a', 'value': '180', 'source_ordinal': 9},
                {'name': 'b', 'value': '100', 'source_ordinal': 1},
            ],
        ),
        {'content': '九只股票中 TSM 收盘价为 180 美元 [9]，AAPL 收盘价为 100 美元 [1]，差额为 80 美元 [10]。'},
    ])

    async def emit(_):
        pass

    question = '计算一下这九只股票近半年 K 线收盘价的波动率，然后指出波动最大的股票和波动最小的股票'
    canonical = '计算 AAPL、MSFT、NVDA、GOOGL、AMZN、META、TSLA、AVGO、TSM 这九只股票近半年 K 线收盘价的波动率，指出波动最大和最小的股票'

    ctx = AgentRuntimeContext(
        'custom', 'test', 'dummy',
        resolver=lambda _: {
            'relationship': 'followup',
            'canonical_question': canonical,
            'symbols': NINE_TICKERS,
        },
        # Simulate classifier bug: returns only single symbol 'AAPL' and empty symbols=[]
        classifier=lambda _: {
            'category': 'A',
            'mode': 'prices',
            'symbol': 'AAPL',
            'symbols': [],
            'metrics': ['price'],
            'days_back': 180,
        },
        decider=lambda *_: next(steps),
        executor=execute,
    )
    result = asyncio.run(
        direct.run_direct_turn(
            question,
            ctx,
            emit=emit,
            messages=[{'role': 'user', 'content': '我关注 AAPL、MSFT、NVDA、GOOGL、AMZN、META、TSLA、AVGO、TSM'}],
        )
    )
    assert result['status'] == 'succeeded'
    assert 'search_knowledge' not in [n for n, _ in executed]
    assert [a['symbol'] for n, a in executed if n == 'query_stock_prices'] == NINE_TICKERS
    assert result['audit'].get('classify_symbols_backfilled') is True
    assert result['previous_scope']['symbols'] == NINE_TICKERS
    assert result['previous_scope']['symbol'] == 'AAPL'
    assert result['audit']['mode'] == 'prices'
    assert result['audit']['tool_bias'] == 'prices'


def test_route_a_mode_financials_multi_stock_skips_rag_prelude():
    executed = []

    def execute(name, args):
        executed.append((name, args))
        if name == 'query_financials':
            sym = args['symbol']
            val = 300_000_000_000 if sym == 'AAPL' else 250_000_000_000
            return {
                'ok': True,
                'data': [{
                    'source_id': f'fin-{sym}',
                    'symbol': sym,
                    'form': '10-K',
                    'source_type': 'reported_figure',
                    'metric': 'revenue',
                    'value': val,
                    'title': f'{sym} 10-K Revenue FY2025',
                    'filed_at': '2025-11-01',
                    'excerpt': f'{sym} FY2025 营业收入（Revenue）：{val:,} USD。',
                }],
            }
        return {'ok': True, 'data': []}

    steps = iter([
        _call('query_financials', symbol='AAPL', metrics=['revenue']),
        _call('query_financials', symbol='MSFT', metrics=['revenue']),
        {'content': 'AAPL FY2025 营业收入为 300,000,000,000 美元 [1]，MSFT FY2025 营业收入为 250,000,000,000 美元 [2]。'},
    ])

    async def emit(_):
        pass

    ctx = AgentRuntimeContext(
        'custom', 'test', 'dummy',
        classifier=lambda _: {
            'category': 'A',
            'mode': 'financials',
            'symbols': ['AAPL', 'MSFT'],
            'metrics': ['营业收入'],
            'days_back': None,
        },
        decider=lambda *_: next(steps),
        executor=execute,
    )
    result = asyncio.run(direct.run_direct_turn('对比 AAPL 和 MSFT 的营业收入', ctx, emit=emit))
    assert result['status'] == 'succeeded'
    assert 'search_knowledge' not in [n for n, _ in executed]
    assert [a['symbol'] for n, a in executed if n == 'query_financials'] == ['AAPL', 'MSFT']
    assert result['audit']['mode'] == 'financials'
    assert result['audit']['tool_bias'] == 'financials'


def test_route_a_missing_mode_defaults_to_none_without_rag_prelude():
    executed = []

    def execute(name, args):
        executed.append((name, args))
        return {
            'ok': True,
            'data': [{'id': 'p1', 'symbol': 'NVDA', 'close': 140.0, 'trading_date': '2026-10-15'}],
        }

    steps = iter([
        _call('query_stock_prices', symbol='NVDA'),
        {'content': 'NVDA 收盘价为 140 美元 [1]。'},
    ])

    async def emit(_):
        pass

    # Classifier omits mode
    ctx = AgentRuntimeContext(
        'custom', 'test', 'dummy',
        classifier=lambda _: {'category': 'A', 'symbol': 'NVDA', 'days_back': None},
        decider=lambda *_: next(steps),
        executor=execute,
    )
    result = asyncio.run(direct.run_direct_turn('NVDA 收盘价是多少', ctx, emit=emit))
    assert result['status'] == 'succeeded'
    assert 'search_knowledge' not in [n for n, _ in executed]
    assert result['audit']['mode'] == 'none'
    assert result['audit'].get('classify_mode_defaulted') is True
    assert result['audit'].get('tool_bias') is None
