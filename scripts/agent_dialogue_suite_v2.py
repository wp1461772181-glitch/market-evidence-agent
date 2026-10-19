"""Run the v2 dialogue suite against a running API and grade each turn on what it can observe.

Each case runs in its own conversation (a ``new_session`` step starts another one), as the owner
or as a fresh guest.  For every user turn the runner records the answer, every tool call with its
arguments, every citation, the card the turn ended on, the elapsed time and the turn's audit (read
from ``agent_messages``), then applies the case's checks.  Card buttons are pressed only where a
case says so (``cancel_card``); nothing is confirmed.

Keys and the owner credential are read into memory only and never printed.

Usage:
    .venv-rag/bin/python scripts/agent_dialogue_suite_v2.py --base http://127.0.0.1:9031 \
        --db test_g6_shadow_20261019 --suite docs/dialogue-tests/2026-10-19-suite-v2.json [--cases S01,S05] [--repeat 2]
"""
import argparse, json, os, re, secrets, subprocess, sys, time, urllib.request

ROOT = '/Users/wupeng/monash-ai/projects/market-evidence-agent'
MODEL = {'provider': 'openrouter', 'model': 'deepseek/deepseek-v4.1-flash'}


def env_value(name):
    for line in open(os.path.join(ROOT, '.env'), encoding='utf-8'):
        key, _, value = line.strip().partition('=')
        if key.strip() == name: return value.strip().strip('"').strip("'")
    raise SystemExit(f'{name} is not configured')


def stream(base, session, payload):
    request = urllib.request.Request(base + '/agent-chat/stream', data=json.dumps(payload).encode(),
                                     headers={'Content-Type': 'application/json', 'x-chat-session': session}, method='POST')
    events = []
    with urllib.request.urlopen(request, timeout=400) as response:
        for raw in response:
            line = raw.decode().rstrip('\n')
            if line.startswith('event: '): events.append([line[7:], ''])
            elif line.startswith('data: ') and events: events[-1][1] += line[6:]
    return [(kind, json.loads(body or '{}')) for kind, body in events]


def audit_of(db, conversation, turn):
    sql = ("select coalesce(tool_calls->'audit','null') from agent_messages where conversation_id='%s' and turn_id='%s' "
           "and role='assistant' order by sequence desc limit 1" % (conversation, turn))
    out = subprocess.run(['docker', 'exec', '-e', 'PGOPTIONS=-c default_transaction_read_only=on', 'market-evidence-postgres',
                          'psql', '-U', 'market_evidence', '-d', db, '-At', '-c', sql],
                         capture_output=True, text=True, env={**os.environ, 'PATH': os.path.expanduser('~/.orbstack/bin') + ':' + os.environ.get('PATH', '')})
    try: return json.loads(out.stdout.strip() or 'null') or {}
    except ValueError: return {}


def summarise(events):
    answer = ''.join(d.get('text') or '' for k, d in events if k == 'delta')
    tools, results, citations, card, done, turn = [], [], [], None, {}, None
    for kind, data in events:
        if kind == 'conversation': turn = data.get('turn_id')
        elif kind == 'tool_start': tools.append({'name': data.get('name'), 'arguments': data.get('arguments') or {}})
        elif kind == 'tool_result':
            r = data.get('result') or {}
            results.append({'name': data.get('name'), 'ok': r.get('ok'), 'error_code': r.get('error_code'),
                            'rows': len(r['data']) if isinstance(r.get('data'), list) else None})
        elif kind == 'citations': citations = data.get('items') or []
        elif kind == 'clarification':
            card = {'request_id': data.get('request_id'), 'reason': data.get('reason'), 'question': data.get('question'),
                    'title': (data.get('display') or {}).get('title'), 'targets': (data.get('display') or {}).get('targets')}
        elif kind == 'done': done = data
    return {'answer': answer.strip(), 'status': done.get('status'), 'reason': done.get('reason'), 'turn_id': turn,
            'conversation_id': done.get('conversation_id'), 'tools': tools, 'tool_results': results, 'card': card,
            'citations': [{'ordinal': c.get('ordinal'), 'source_type': c.get('source_type'), 'title': c.get('title')} for c in citations]}


def contains_any(text, options): return any(o in text for o in options)


def grade(check, row):
    """Each check returns (name, passed, detail).  A check is machine-observable or it is not here."""
    out = []; audit = row.get('audit') or {}; answer = row.get('answer') or ''
    stages = audit.get('stages') or {}
    def add(name, ok, detail=''): out.append((name, bool(ok), detail))
    if 'route' in check: add('route', audit.get('route') in check['route'], audit.get('route'))
    if 'relationship' in check:
        rel = (stages.get('resolver') or {}).get('relationship'); add('relationship', rel in check['relationship'], rel)
    names = [t['name'] for t in row.get('tools') or []]
    for name in check.get('tools_include', []): add('tool:' + name, name in names, names)
    for name in check.get('tools_exclude', []): add('no tool:' + name, name not in names, names)
    for spec in check.get('tool_args', []):
        hit = any(t['name'] == spec['name'] and all(t['arguments'].get(k) == v for k, v in spec['args'].items()) for t in row.get('tools') or [])
        add('args:' + spec['name'] + json.dumps(spec['args'], ensure_ascii=False), hit, [t for t in row.get('tools') or [] if t['name'] == spec['name']])
    if 'card' in check:
        card = row.get('card'); want = check['card']
        ok = (card is None) if want is False else (card is not None and (want is True or card.get('reason') == want))
        add('card', ok, card and card.get('reason'))
    for group in check.get('answer_any', []): add('answer has ' + '|'.join(group), contains_any(answer, group))
    for text in check.get('answer_excludes', []): add('answer lacks ' + text, text not in answer)
    if check.get('no_dropped', True): add('nothing dropped', '未能与来源核对' not in answer, '')
    if 'status' in check: add('status', row.get('status') in check['status'], row.get('status'))
    else: add('not failed', row.get('status') not in {'failed', 'ERROR'}, row.get('status'))
    if 'max_seconds' in check: add('time', (row.get('seconds') or 0) <= check['max_seconds'], row.get('seconds'))
    return out


def run_case(case, base, db, key, owner, identity=None):
    session = owner if (identity or case.get('identity', 'owner')) == 'owner' else secrets.token_hex(32)
    conversation = None; last_card = None; rows = []
    for index, step in enumerate(case['steps'], 1):
        kind = step.get('kind', 'user')
        if kind == 'new_session': conversation = None; last_card = None; continue
        payload = {**MODEL, 'api_key': key, 'conversation_id': conversation}
        if kind == 'cancel_card':
            if not last_card: rows.append({'step': index, 'kind': kind, 'blocked': 'no card to cancel'}); continue
            payload.update(message='cancel:' + last_card['request_id'], pending_request_id=last_card['request_id'], display_message='取消')
        else: payload['message'] = step['text']
        started = time.monotonic()
        try: row = summarise(stream(base, session, payload))
        except Exception as exc: row = {'answer': '', 'status': 'ERROR', 'reason': f'{type(exc).__name__}: {exc}'[:300]}
        row.update(step=index, kind=kind, message=payload['message'], seconds=round(time.monotonic() - started, 1))
        conversation = row.get('conversation_id') or conversation
        last_card = row.get('card') or (None if kind != 'cancel_card' else last_card)
        if row.get('conversation_id') and row.get('turn_id'): row['audit'] = audit_of(db, row['conversation_id'], row['turn_id'])
        row['checks'] = [{'name': n, 'ok': ok, 'detail': d} for n, ok, d in grade(step.get('check', {}), row)]
        rows.append(row)
        failed = [c['name'] for c in row['checks'] if not c['ok']]
        print(f"{case['id']}.{index} {row.get('seconds')}s {row.get('status')}/{row.get('reason')} "
              f"tools={[t['name'] for t in row.get('tools') or []]} card={bool(row.get('card'))} "
              f"{'PASS' if not failed else 'FAIL ' + '; '.join(failed)}", flush=True)
    return rows


def main(argv):
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', default='http://127.0.0.1:9031'); parser.add_argument('--db', default='test_g6_shadow_20261019')
    parser.add_argument('--suite', default=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'docs/dialogue-tests/2026-10-19-suite-v2.json'))
    parser.add_argument('--cases', default=''); parser.add_argument('--repeat', type=int, default=1)
    parser.add_argument('--out', default='/tmp/market-g6/dialogue-v2')
    parser.add_argument('--identity', choices=['owner', 'guest'], help='run every case as this identity')
    args = parser.parse_args(argv[1:])
    suite = json.load(open(args.suite, encoding='utf-8'))
    wanted = {c.strip().upper() for c in args.cases.split(',') if c.strip()}
    cases = [c for c in suite['cases'] if not wanted or c['id'] in wanted]
    key = env_value('OPENROUTER_API_KEY')
    owner = json.load(open(os.path.join(ROOT, 'data/personal-chat-login.json'), encoding='utf-8'))['chat_credential']
    os.makedirs(args.out, exist_ok=True); stamp = time.strftime('%Y%m%d-%H%M%S'); report = []
    for attempt in range(1, args.repeat + 1):
        for case in cases:
            rows = run_case(case, args.base, args.db, key, owner, args.identity)
            report.append({'case': case['id'], 'title': case['title'], 'attempt': attempt, 'rows': rows,
                           'passed': all(c['ok'] for r in rows for c in r.get('checks', [])) and not any(r.get('blocked') for r in rows)})
    path = os.path.join(args.out, f'results-{stamp}.json')
    json.dump(report, open(path, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    turns = [r for c in report for r in c['rows'] if 'checks' in r]
    seconds = sorted(r.get('seconds') or 0 for r in turns)
    print(f"\ncases {sum(c['passed'] for c in report)}/{len(report)} passed; turns {sum(all(x['ok'] for x in r['checks']) for r in turns)}/{len(turns)} passed; "
          f"p50 {seconds[len(seconds)//2] if seconds else 0}s p90 {seconds[int(len(seconds)*0.9)] if seconds else 0}s; saved {path}", flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
