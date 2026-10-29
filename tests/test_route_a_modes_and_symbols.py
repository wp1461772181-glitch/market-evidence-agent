"""Tests for Route A mode sub-routing, RAG prelude gating, and multi-ticker symbols backfill."""
from __future__ import annotations

import asyncio
import uuid
from datetime import date, timedelta

import exchange_calendars as xcals
import pytest

from app.agent_workflow import direct
from app.agent_workflow.routing import Routing, safe_parse_route
from app.agent_workflow.state import AgentRuntimeContext


NINE_TICKERS = ['AAPL', 'MSFT', 'NVDA', 'GOOGL', 'AMZN', 'META', 'TSLA', 'AVGO', 'TSM']
XNYS = xcals.get_calendar("XNYS")


def _sessions_back(end: date, count: int) -> list[date]:
    """Newest-first XNYS sessions on or before `end`; seeded history must be real trading days."""
    sessions: list[date] = []
    day = end
    while len(sessions) < count:
        if XNYS.is_session(day):
            sessions.append(day)
        day -= timedelta(days=1)
    return sessions


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
    assert result['status'] == 'succeeded', f"reason={result.get('reason')} audit={result.get('audit')} answer={result.get('answer')}"
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
    assert result['status'] == 'succeeded', f"reason={result.get('reason')} audit={result.get('audit')} answer={result.get('answer')}"
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
    assert result['status'] == 'succeeded', f"reason={result.get('reason')} audit={result.get('audit')} answer={result.get('answer')}"
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
    assert result['status'] == 'succeeded', f"reason={result.get('reason')} audit={result.get('audit')} answer={result.get('answer')}"
    assert 'search_knowledge' not in [n for n, _ in executed]
    assert result['audit']['mode'] == 'none'
    assert result['audit'].get('classify_mode_defaulted') is True
    assert result['audit'].get('tool_bias') is None


def test_nine_stocks_six_month_volatility_compacts_window_and_grounds_volatility_figures():
    """9 stocks × 127 daily bars must return 1 saved_price_set receipt per stock, compute via 1 calculate_formula call, and pass replay + claim gate."""
    from datetime import UTC, date, datetime, timedelta
    from sqlalchemy import delete
    from app.agent_tools import build_price_set_row
    from app.agent_workflow.claim_review import apply_claim_gate
    from app.agent_workflow.evidence import unsupported_number_values
    from app.database import Base, SessionLocal, engine
    from app.models import MarketPrice, MarketPriceRevision

    Base.metadata.create_all(bind=engine)
    base_date = date(2026, 10, 29)
    fetched_ts = datetime(2026, 10, 29, 0, 0, 0, tzinfo=UTC)
    receipt_by_symbol = {}

    with SessionLocal() as session:
        session.execute(delete(MarketPriceRevision).where(MarketPriceRevision.symbol.in_(NINE_TICKERS)))
        session.execute(delete(MarketPrice).where(MarketPrice.symbol.in_(NINE_TICKERS)))
        session.commit()
        for idx, sym in enumerate(NINE_TICKERS):
            rows = []
            for day_idx, d in enumerate(_sessions_back(base_date, 127)):
                swing = (idx + 1) * 0.8 * (1 if day_idx % 2 == 0 else -1)
                close_val = round(100.0 + idx * 15.0 + swing, 2)
                mp = MarketPrice(
                    id=uuid.uuid4(),
                    symbol=sym,
                    trading_date=d,
                    open=close_val - 0.5,
                    high=close_val + 1.0,
                    low=close_val - 1.0,
                    close=close_val,
                    volume=10_000_000 + day_idx * 1000,
                    source='stooq',
                    fetched_at=fetched_ts,
                )
                session.add(mp)
                rows.append({
                    'id': str(mp.id),
                    'symbol': sym,
                    'trading_date': d.isoformat(),
                    'open': float(mp.open),
                    'high': float(mp.high),
                    'low': float(mp.low),
                    'close': float(mp.close),
                    'volume': int(mp.volume),
                    'source': mp.source,
                    'fetched_at': fetched_ts.isoformat(),
                })
            session.commit()
            receipt, _ = build_price_set_row(rows, symbol=sym)
            receipt_by_symbol[sym] = receipt

    def execute(name, args):
        if name == 'query_stock_prices':
            return {'ok': True, 'data': [receipt_by_symbol[args['symbol']]], 'source': 'market_prices'}
        return {'ok': True, 'data': []}

    formula_code = (
        "def f(close):\n"
        "    r = np.diff(close) / close[:-1]\n"
        "    return float(np.std(r, ddof=1) * math.sqrt(252) * 100)\n"
    )

    step_idx = 0

    def decide(_messages, _tools, observations=None):
        nonlocal step_idx
        step_idx += 1
        if step_idx == 1:
            return {
                'content': None,
                'tool_calls': [
                    {'id': f'call-{sym}', 'function': {'name': 'query_stock_prices', 'arguments': {'symbol': sym, 'start': '2026-04-29', 'end': '2026-10-29', 'limit': 183}}}
                    for sym in NINE_TICKERS
                ],
            }
        if step_idx == 2:
            return _call(
                'calculate_formula',
                formula_code=formula_code,
                bindings=[
                    {
                        'label': sym,
                        'inputs': {'close': {'source_ordinal': i + 1, 'field': 'close'}},
                    }
                    for i, sym in enumerate(NINE_TICKERS)
                ],
                decimals=2,
                unit='pct',
            )
        calc_obs = next(o for o in (observations or []) if o.get('name') == 'calculate_formula')
        items = calc_obs['result']['data']
        lines = [
            f"- {item['label']}：近半年收盘价年化波动率 {item['value']}% {item['citation']}。"
            for item in items
        ]
        hi_item = max(items, key=lambda x: float(x['value']))
        lo_item = min(items, key=lambda x: float(x['value']))
        lines.append(
            f"按 252 个交易日年化计算，波动最大的股票是 {hi_item['label']}（{hi_item['value']}%）{hi_item['citation']}，"
            f"波动最小的股票是 {lo_item['label']}（{lo_item['value']}%）{lo_item['citation']}。"
        )
        return {'content': '\n'.join(lines)}

    async def emit(_):
        pass

    ctx = AgentRuntimeContext(
        'custom', 'test', 'dummy',
        resolver=lambda _: {
            'relationship': 'followup',
            'canonical_question': '计算 AAPL、MSFT、NVDA、GOOGL、AMZN、META、TSLA、AVGO、TSM 这九只股票近半年 K 线收盘价的波动率，指出波动最大和最小的股票',
            'symbols': NINE_TICKERS,
        },
        classifier=lambda _: {
            'category': 'A',
            'mode': 'prices',
            'symbol': 'AAPL',
            'symbols': NINE_TICKERS,
            'days_back': 183,
        },
        decider=decide,
        executor=execute,
    )
    result = asyncio.run(
        direct.run_direct_turn(
            '计算一下这九只股票近半年 K 线收盘价的波动率，然后指出波动最大的股票和波动最小的股票',
            ctx,
            emit=emit,
            messages=[{'role': 'user', 'content': '我关注 AAPL、MSFT、NVDA、GOOGL、AMZN、META、TSLA、AVGO、TSM'}],
        )
    )
    assert result['status'] == 'succeeded', f"reason={result.get('reason')} audit={result.get('audit')} answer={result.get('answer')}"
    assert result['audit']['formula_replay'] == {'checked': 9, 'failed': []}
    assert not unsupported_number_values(result['answer'], result['citations'])
    gate = apply_claim_gate(result['answer'], result['citations'], mode='enforce')
    assert not gate.dropped_lines


