import asyncio
import pytest
from app.agent_workflow.direct import Turn
from app.agent_workflow.state import AgentRuntimeContext
from app.agent_workflow import consent
from app.database import Base, engine
from app.agent_workflow.models import AgentConsent
from app import agent_context

@pytest.fixture
def owner(monkeypatch, disposable_database):
    Base.metadata.create_all(engine, tables=[AgentConsent.__table__])
    monkeypatch.setattr(consent, 'owner_credential_matches', lambda c: c == 'owner')
    return 'owner'

def test_stated_count_mismatch_does_not_drop_proposals(owner, monkeypatch):
    """When user mentions '这两条' (count=2) but model proposes 3 URLs,

    confirm_proposals must NOT wipe proposals with result_count_mismatch.
    It should propose a card containing all 3 targets for the user to confirm/uncheck.
    """
    ctx = AgentRuntimeContext('custom', 'test', 'dummy', identity_credential='owner', conversation_id='test-count-mismatch')
    t = Turn('把这两条加到资料库里面去吧', ctx, None, [], {})
    t.route = 'D'
    ops = [
        {'name': 'ingest_web_source', 'arguments': {'url': f'https://example.com/news/{i}'}}
        for i in range(1, 4)
    ]
    t.proposals = [
        ([op], {'title': f'News {i}', 'targets': [{'url': op['arguments']['url'], 'title': f'News {i}'}]})
        for i, op in enumerate(ops, 1)
    ]
    asyncio.run(t.confirm_proposals())

    assert t.pending is not None, "Pending confirmation card should be created"
    display = t.pending.get('display', {})
    assert len(display.get('targets', [])) == 3, "All 3 proposed targets should be present on the card"
    assert t.feedback == [], "Should not have result_count_mismatch error in feedback"

def test_route_d_conversational_reply_is_not_corrupted_by_citations_or_write_not_proposed(owner):
    """In Route D (writes/actions), natural conversational replies like

    '确认后才会写入资料库；如只想保留其中一两条，可在卡片上只确认对应项。'
    must not be pruned with '已略去' or appended with '操作未执行：write_not_proposed'.
    """
    ctx = AgentRuntimeContext('custom', 'test', 'dummy', identity_credential='owner', conversation_id='test-route-d-clean')
    t = Turn('把这两条加到资料库里面去吧', ctx, None, [], {})
    t.route = 'D'
    t.observations = [{'name': 'ingest_web_source', 'status': 'proposed'}]
    t.pending = {'id': 'test-card', 'display': {'title': '确认以下操作'}}
    t.scope = {'requested_actions': ['ingest_web_source']}
    
    reply = "确认后才会写入资料库；如只想保留其中一两条，可在卡片上只确认对应项。"
    result = t.finish(reply)
    
    assert "已略去" not in result['answer'], "Should not contain '已略去'"
    assert "部分表述暂无法与来源核实" not in result['answer'], "Should not contain citation verify warning"
    assert "操作未执行：write_not_proposed" not in result['answer'], "Should not append write_not_proposed mechanically"
    assert result['answer'] == reply, "Answer should be preserved verbatim"

def test_system_prompt_does_not_contain_180_char_limit():
    """System prompt in direct workflow must not contain 180 Chinese characters / 120 English words cap."""
    ctx = AgentRuntimeContext('custom', 'test', 'dummy', identity_credential='owner', conversation_id='test-prompt')
    t = Turn('测试提问', ctx, None, [], {})
    t.route = 'A'
    protocol = t.tool_messages(closing=False)
    system_prompt = next(m['content'] for m in protocol if m['role'] == 'system')
    assert "180 Chinese characters" not in system_prompt
    assert "120 English words" not in system_prompt

def test_assistant_keep_chars_configured():
    """L2 dialogue history compression budget for assistant must be configured."""
    assert agent_context.ASSISTANT_KEEP_CHARS > 0
