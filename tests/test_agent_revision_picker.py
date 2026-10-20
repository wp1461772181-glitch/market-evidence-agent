"""Revising a forecast through the agent: the user picks the materials, never the model.

Regression from 2026-10-20: asked to "修正这个快照", the agent chose three SEC filings itself, none
inside the revision window, and the card showed only "修正预测 · GOOGL".  Now the server lists the
materials the revision rule allows on a card the user ticks, and says plainly when there are none.
"""
import asyncio
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.agent_tool_catalog import definitions
from app.agent_workflow import consent, direct, tools, workspace_tools
from app.agent_workflow.models import AgentConsent
from app.agent_workflow.state import AgentRuntimeContext
from app.database import Base, SessionLocal, engine
from app.forecast_jobs import enqueue_job
from app.forecast_v2_models import ForecastVersionV2
from app.models import UploadedEvidence


from test_forecast_revision_candidates import forget_symbols


@pytest.fixture
def owner(monkeypatch, disposable_database):
    Base.metadata.create_all(bind=engine)
    for module in (consent, direct, tools, workspace_tools):
        monkeypatch.setattr(module, 'owner_credential_matches', lambda credential: credential == 'owner')
    yield 'owner'
    forget_symbols([f'RP{letter}' for letter in 'ABCDEFGH'])


def _context(conversation):
    return AgentRuntimeContext('custom', 'test', 'dummy', identity_credential='owner',
                               owner_hash='o' * 64, conversation_id=conversation)


async def _quiet(_):
    pass


def _forecast(symbol, decided, *, target_end=None):
    with SessionLocal() as db:
        job = enqueue_job(db=db, symbol=symbol, kind='new', idempotency_key=f'picker-{uuid.uuid4()}')
        version = ForecastVersionV2(
            id=uuid.uuid4(), root_id=uuid.uuid4(), job_id=job.id, version_no=1, symbol=symbol,
            target_contract={'target_end_date': (target_end or date(2027, 1, 29)).isoformat()},
            target_contract_hash='a' * 64, decision_at=decided, market_cutoff_at=decided - timedelta(minutes=5),
            price_input_manifest={}, evidence_version_manifest=[], feature_snapshot={},
            baseline_probabilities={'bearish': 0.2, 'neutral': 0.4, 'bullish': 0.4},
            joint_probabilities=None, model_status='research_only', model_manifest={}, trigger_type='manual',
        )
        version.root_id = version.id
        db.add(version); db.commit()
        return version.id


def _upload(symbol, title, published):
    with SessionLocal() as db:
        row = UploadedEvidence(
            symbol=symbol, title=title, source_url='https://news.example.test/' + uuid.uuid4().hex,
            published_at=published, observed_at=published + timedelta(hours=1), credibility_stars=4,
            credibility_reason='fixture', impact_severity='medium', filename='n.md',
            content_sha256=uuid.uuid4().hex.ljust(64, '0'), raw_content=b'x', content_text='x', status='unconfirmed')
        db.add(row); db.commit()
        return str(row.id)


def _turn(question, conversation, symbol, state=None):
    turn = direct.Turn(question, _context(conversation), _quiet, [], state or {})
    turn.route = 'D'
    turn.scope = {'symbol': symbol}
    turn.granted_actions = {'revise_forecast'}
    return turn


def test_the_model_cannot_name_revision_sources():
    schema = definitions()['revise_forecast'].args_schema
    assert set(schema.model_fields) == {'symbol', 'version_id', 'mentioned_sources'}
    with pytest.raises(ValidationError):
        schema.model_validate({'symbol': 'AAPL', 'source_refs': [
            {'source_type': 'official_filing', 'source_id': '11111111-1111-4111-8111-111111111111'}]})


def test_materials_in_the_window_are_offered_on_a_card_the_user_ticks(owner):
    decided = datetime.now(UTC) - timedelta(days=10)
    _forecast('RPA', decided)
    inside = _upload('RPA', 'Gemini 发布会报道', decided + timedelta(days=5))
    _upload('RPA', '太晚的报道', decided + timedelta(days=8))
    turn = _turn('修正 RPA 的预测', 'picker-card', 'RPA')

    outcome = asyncio.run(turn.propose('revise_forecast', {'symbol': 'RPA'}))

    assert outcome['ok'] and outcome['data'][0]['status'] == 'awaiting_selection'
    card = turn.pending
    assert card['reason'] == 'choose_revision_sources'
    assert [option['label'] for option in card['options']] == ['Gemini 发布会报道']
    assert '上传材料' in card['options'][0]['detail'] and '发布' in card['options'][0]['detail']
    assert card['select'] == {'min': 1, 'max': 1}
    assert card['choices'][0]['label'] == '用所选材料修订' and card['choices'][0]['value'].startswith('confirm:')
    assert card['choices'][-1]['value'] == 'cancel:' + card['id']
    assert 'RPA' in card['display']['title'] and '7 天' in card['display']['alternative']
    with SessionLocal() as db:
        stored = db.get(AgentConsent, uuid.UUID(card['id']))
        assert [c['source_id'] for c in stored.operations[0]['candidates']] == [inside]


def test_no_material_in_the_window_is_explained_without_a_card(owner):
    decided = datetime(2026, 10, 20, 2, 16, tzinfo=UTC)
    _forecast('RPB', decided)
    turn = _turn('修正 RPB 的预测', 'picker-none', 'RPB')

    outcome = asyncio.run(turn.propose('revise_forecast', {'symbol': 'RPB'}))

    assert outcome['data'][0]['status'] == 'no_candidates'
    assert turn.pending is None
    notice = turn.revision_notice
    assert '没有符合条件的材料' in notice and '还不能修订' in notice
    assert '2026-10-20' in notice and '2026-10-27' in notice and '2026-09-20' in notice
    with SessionLocal() as db:
        assert db.query(AgentConsent).filter(AgentConsent.conversation_id == 'picker-none').count() == 0


def test_an_expired_forecast_is_explained(owner):
    decided = datetime.now(UTC) - timedelta(days=60)
    _forecast('RPC', decided, target_end=date.today() - timedelta(days=20))
    turn = _turn('修正 RPC 的预测', 'picker-expired', 'RPC')

    outcome = asyncio.run(turn.propose('revise_forecast', {'symbol': 'RPC'}))

    assert outcome['data'][0]['status'] == 'target_expired'
    assert '已经过了' in turn.revision_notice and turn.pending is None


def test_a_symbol_without_a_forecast_is_explained(owner):
    turn = _turn('修正 RPD 的预测', 'picker-missing', 'RPD')
    outcome = asyncio.run(turn.propose('revise_forecast', {'symbol': 'RPD'}))
    assert outcome['data'][0]['status'] == 'no_forecast'
    assert '还没有已保存的预测' in turn.revision_notice


def _card_with_two(owner_name, conversation):
    decided = datetime.now(UTC) - timedelta(days=6)
    _forecast('RPE', decided)
    first = _upload('RPE', '第一篇报道', decided + timedelta(days=1))
    second = _upload('RPE', '第二篇报道', decided + timedelta(days=2))
    turn = _turn('修正 RPE 的预测', conversation, 'RPE')
    asyncio.run(turn.propose('revise_forecast', {'symbol': 'RPE'}))
    return turn.pending, {'第一篇报道': first, '第二篇报道': second}


def _press(card, conversation, value, monkeypatch, sent):
    def execute_costly(operation, key, context):
        sent.append(operation)
        return {'ok': True, 'data': [{'job_id': str(uuid.uuid4()), 'status': 'queued', 'symbol': 'RPE',
                                      'action': 'revise_forecast'}]}
    monkeypatch.setattr(tools, 'execute_costly', execute_costly)
    turn = direct.Turn(value, _context(conversation), _quiet, [],
                       {'pending_request': card, 'requested_pending_id': card['id']})
    return asyncio.run(turn.consent_reply()), turn


def test_only_the_ticked_materials_are_sent_and_the_report_names_them(owner, monkeypatch):
    card, ids = _card_with_two(owner, 'picker-press')
    token = card['choices'][0]['value'].split(':', 1)[1]
    second = next(o['value'] for o in card['options'] if o['label'] == '第二篇报道')
    sent = []

    result, turn = _press(card, 'picker-press', f'confirm:{token}:{second}', monkeypatch, sent)

    assert [ref['source_id'] for ref in sent[0]['arguments']['source_refs']] == [ids['第二篇报道']]
    assert sent[0]['arguments']['version_id']
    answer = result['answer']
    assert '第二篇报道' in answer and '第一篇报道' not in answer
    assert '修订好了吗' in answer and 'queued' not in answer
    assert turn.audit['write_outcome'] == 'executed'


def test_a_tick_that_is_not_on_the_card_runs_nothing(owner, monkeypatch):
    card, _ = _card_with_two(owner, 'picker-forged')
    token = card['choices'][0]['value'].split(':', 1)[1]
    sent = []
    with pytest.raises(ValueError):
        _press(card, 'picker-forged', f'confirm:{token}:9', monkeypatch, sent)
    with pytest.raises(ValueError):
        _press(card, 'picker-forged', f'confirm:{token}', monkeypatch, sent)
    assert sent == []


def test_typed_consent_on_the_selection_card_points_to_the_ticks(owner):
    card, _ = _card_with_two(owner, 'picker-typed')
    turn = direct.Turn('同意', _context('picker-typed'), _quiet, [], {'pending_request': card})
    result = asyncio.run(turn.consent_reply())
    assert '勾选' in result['answer'] and result['status'] == 'waiting_for_input'


def test_a_job_reported_as_one_object_reads_as_submitted():
    facts = direct.execution_facts({'ok': True, 'data': [{'ok': True, 'data': {'id': 'j', 'status': 'queued'}}]})
    assert facts[0]['status'] == 'queued'
    assert direct.outcome_detail(facts[0], True) == '已提交，正在处理'


def test_the_workspace_read_lists_the_same_candidates(owner):
    decided = datetime.now(UTC) - timedelta(days=6)
    _forecast('RPF', decided)
    inside = _upload('RPF', '窗口内报道', decided + timedelta(days=6))
    value = workspace_tools.revision_plan('RPF', None)
    assert value['status'] == 'ready'
    assert [c['source_id'] for c in value['candidates']] == [inside]


def test_the_turn_ends_with_the_reason_and_nothing_tacked_on(owner):
    decided = datetime(2026, 10, 20, 2, 16, tzinfo=UTC)
    _forecast('RPB', decided)
    turn = _turn('帮我修正一下 RPB 的预测快照', 'picker-finish', 'RPB')
    turn.scope['requested_actions'] = ['revise_forecast']
    asyncio.run(turn.propose('revise_forecast', {'symbol': 'RPB'}))

    result = turn.finish(turn.revision_notice, generated=True)

    assert result['answer'] == turn.revision_notice
    assert '操作未执行' not in result['answer'] and '确认卡' not in result['answer']
    assert result['audit']['write_outcome'] == 'refused:no_candidates'


def test_with_nothing_to_offer_the_recent_materials_say_why_not(owner):
    decided = datetime.now(UTC) - timedelta(days=10)
    _forecast('RPG', decided)
    _upload('RPG', 'Gemini Agent 发布报道', decided + timedelta(days=9))
    turn = _turn('修正 RPG 的预测', 'picker-why', 'RPG')

    asyncio.run(turn.propose('revise_forecast', {'symbol': 'RPG'}))

    assert 'Gemini Agent 发布报道' in turn.revision_notice and '晚于预测后 7 天' in turn.revision_notice


def test_a_named_material_that_cannot_be_used_is_explained_on_the_card(owner):
    decided = datetime.now(UTC) - timedelta(days=10)
    _forecast('RPH', decided)
    _upload('RPH', '窗口内报道', decided + timedelta(days=3))
    named = _upload('RPH', '太晚的报道', decided + timedelta(days=9))
    turn = _turn('用太晚的报道修订 RPH 的预测', 'picker-named', 'RPH')

    asyncio.run(turn.propose('revise_forecast', {'symbol': 'RPH', 'mentioned_sources': [
        {'source_type': 'uploaded_media', 'source_id': named}]}))

    card = turn.pending
    assert [option['label'] for option in card['options']] == ['窗口内报道']
    assert '你提到的' in card['display']['alternative'] and '太晚的报道' in card['display']['alternative']
    assert '晚于预测后 7 天' in card['display']['alternative']


def test_named_materials_are_only_for_explaining():
    schema = definitions()['revise_forecast'].args_schema
    value = schema.model_validate({'symbol': 'AAPL', 'mentioned_sources': [
        {'source_type': 'official_filing', 'source_id': '11111111-1111-4111-8111-111111111111'}]})
    assert value.mentioned_sources[0].source_type == 'official_filing'
    assert 'never' in definitions()['revise_forecast'].description.lower()
