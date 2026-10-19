"""Run the dialogue suites (read-only v2 and owner-write v2) against a running API.

Each case runs in its own conversation (a ``new_session`` step starts another one), as the owner
or as a fresh guest.  For every user turn the runner records the answer, every tool call with its
arguments, every citation, the card the turn ended on, the elapsed time and the turn's audit (read
from ``agent_messages``), then applies the case's checks.  Cards can be pressed the way the web
panel does it (``cancel_card``, ``confirm_card``, ``choose_card``, ``reconfirm_card``,
``stale_confirm``), and a case may assert on the temporary database with ``db_check``.

Keys are read into memory only and never printed, logged or written to the result file.  Card
values carry one-time consent capabilities, so the result file keeps only the button label and the
value's kind (confirm/cancel/change/reply), never the value itself.

Cases run in parallel (``--parallel``, default 4).  A case that reads or writes state another case
also touches declares ``"serial_group": "<name>"`` in the suite; cases in one group run one at a
time in file order, while different groups and ungrouped cases run concurrently.  Each case still
has its own conversation and card tracking, and its whole output is printed as one block.

Usage:
    .venv-rag/bin/python scripts/agent_dialogue_suite_v2.py --base http://127.0.0.1:9031 \
        --db test_g6_shadow_20261019 --suite docs/dialogue-tests/2026-10-19-suite-writes.json \
        --identity owner --profile tokenplan --out .superpowers/dialogue-writes

    .venv-rag/bin/python scripts/agent_dialogue_suite_v2.py --cases S01 \
        --suite docs/dialogue-tests/2026-10-19-suite-v2.json
"""
import argparse, json, os, re, secrets, subprocess, sys, time, urllib.error, urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

ROOT = '/Users/wupeng/monash-ai/projects/market-evidence-agent'
CREDENTIALS = os.path.expanduser('~/.dsh/.credentials.yaml')
SHADOW_DB_PREFIX = 'test_g6_shadow_'
PROFILES = {
    'tokenplan': {
        'provider': 'custom',
        'base_url': 'https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1',
        'model': 'deepseek-v4.1-flash-1017',
    },
    'openrouter': {'provider': 'openrouter', 'model': 'deepseek/deepseek-v4.1-flash'},
}
CONFIRM_LABELS = ('同意', '确认', '批准', '授权', '继续', '执行')
CANCEL_LABELS = ('取消', '放弃', '不保存')
CONTRADICTIONS = ('没有生成确认卡', '未生成确认卡', '没有出现确认卡')
CODE_RE = re.compile(r'(?<![A-Za-z0-9_])[a-z][a-z0-9]*(?:_[a-z0-9]+)+(?![A-Za-z0-9_])')
UUID_RE = re.compile(r'\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b')
PSQL_ENV = {**os.environ, 'PATH': os.path.expanduser('~/.orbstack/bin') + ':' + os.environ.get('PATH', '')}
PRINT_LOCK = Lock()  # one completed case is printed at a time, so parallel cases never interleave


class ApiError(Exception):
    def __init__(self, status, detail, body=''):
        self.status = status
        self.detail = detail
        self.body = body
        super().__init__(f'HTTP {status}: {detail}')


def env_value(name):
    with open(os.path.join(ROOT, '.env'), encoding='utf-8') as handle:
        for line in handle:
            key, _, value = line.strip().partition('=')
            if key.strip() == name:
                return value.strip().strip('"').strip("'")
    raise SystemExit(f'{name} is not configured')


def credential_refs():
    """Read the refs map without ever printing a value."""
    try:
        import yaml
        data = yaml.safe_load(open(CREDENTIALS, encoding='utf-8')) or {}
        refs = data.get('refs') or {}
        return {str(k): v for k, v in refs.items() if isinstance(v, str)}
    except Exception:
        text = open(CREDENTIALS, encoding='utf-8').read()
        refs, in_refs = {}, False
        for line in text.splitlines():
            if re.match(r'^refs:\s*$', line):
                in_refs = True
                continue
            if in_refs:
                if line and not line.startswith((' ', '\t')):
                    break
                match = re.match(r'^\s+([A-Za-z0-9_]+):\s*(.+?)\s*$', line)
                if match:
                    refs[match.group(1)] = match.group(2).strip().strip('"').strip("'")
        return refs


def model_payload(profile):
    if profile not in PROFILES:
        raise SystemExit(f'unknown profile: {profile}')
    if profile == 'tokenplan':
        key = credential_refs().get('ALIYUN_TOKEN_PLAN_API_KEY', '')
        if not key:
            raise SystemExit('ALIYUN_TOKEN_PLAN_API_KEY is not configured in ~/.dsh/.credentials.yaml')
    else:
        key = env_value('OPENROUTER_API_KEY')
    if not key or len(key) < 8:
        raise SystemExit('model credential is not configured')
    return {**PROFILES[profile], 'api_key': key}


def wait_for_api(base, seconds=60):
    """The shadow service may still be warming up; wait at most a minute, never restart it."""
    deadline = time.monotonic() + seconds
    last = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(base.rstrip('/') + '/docs', timeout=3) as response:
                if response.status < 500:
                    return
        except urllib.error.HTTPError as exc:
            if exc.code < 500:  # an HTTP answer, even a 404, means the process is listening
                return
            last = f'HTTP {exc.code}'
        except Exception as exc:
            last = f'{type(exc).__name__}: {exc}'[:200]
        time.sleep(2)
    raise SystemExit(f'API did not answer within {seconds}s: {last}')


# --production lets the runner read the product database's audit and consent rows (read-only) to grade a
# release smoke; db_check steps stay limited to scratch databases.
PRODUCTION_AUDIT = False


def psql(db, sql, timeout=60, audit=False):
    if not db.startswith(SHADOW_DB_PREFIX) and not (audit and PRODUCTION_AUDIT and db == 'market_evidence'):
        raise SystemExit(f'refusing database {db!r}: only {SHADOW_DB_PREFIX}... may be used')
    out = subprocess.run(
        ['docker', 'exec', '-e', 'PGOPTIONS=-c default_transaction_read_only=on',
         'market-evidence-postgres', 'psql', '-U', 'market_evidence', '-d', db, '-At', '-c', sql],
        capture_output=True, text=True, timeout=timeout, env=PSQL_ENV)
    if out.returncode:
        raise RuntimeError((out.stderr or out.stdout).strip()[:500])
    return out.stdout.strip()


def audit_of(db, conversation, turn):
    if not conversation or not turn:
        return {}
    sql = ("select coalesce(tool_calls->'audit','null') from agent_messages where conversation_id='%s' "
           "and turn_id='%s' and role='assistant' order by sequence desc limit 1" % (conversation, turn))
    try:
        return json.loads(psql(db, sql, audit=True) or 'null') or {}
    except Exception:
        return {}


def consent_of(db, request_id):
    if not request_id or not re.fullmatch(r'[0-9a-fA-F-]{36}', request_id):
        return {}
    sql = ("select coalesce(json_build_object('status',status,'operations',operations,'display',display)::text,'null') "
           "from agent_consents where id='%s'" % request_id)
    try:
        return json.loads(psql(db, sql, audit=True) or 'null') or {}
    except Exception:
        return {}


def summarise_operations(operations):
    """Keep the operation names and short arguments; the web snapshot can be a whole page."""
    out = []
    for operation in operations or []:
        if not isinstance(operation, dict):
            continue
        arguments = operation.get('arguments') if isinstance(operation.get('arguments'), dict) else {}
        clean = {}
        for key, value in arguments.items():
            if key in {'content', 'snapshot'}:
                continue
            clean[key] = value[:300] if isinstance(value, str) and len(value) > 300 else value
        out.append({'name': operation.get('name'), 'arguments': clean})
    return out


def stream(base, session, payload):
    request = urllib.request.Request(
        base + '/agent-chat/stream', data=json.dumps(payload).encode(),
        headers={'Content-Type': 'application/json', 'x-chat-session': session}, method='POST')
    try:
        response = urllib.request.urlopen(request, timeout=400)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode('utf-8', 'replace')[:4000]
        detail = body
        try:
            parsed = json.loads(body)
            if isinstance(parsed, dict) and isinstance(parsed.get('detail'), str):
                detail = parsed['detail']
        except ValueError:
            pass
        raise ApiError(exc.code, detail, body) from None
    events = []
    for raw in response:
        line = raw.decode('utf-8', 'replace').rstrip('\r\n')
        if line.startswith('event: '):
            events.append([line[7:], ''])
        elif line.startswith('data: ') and events:
            events[-1][1] += line[6:]
    return [(kind, json.loads(body or '{}')) for kind, body in events]


def choice_summary(choice):
    value = str(choice.get('value') or '')
    return {'label': choice.get('label'), 'value_kind': value.split(':', 1)[0] if ':' in value else ''}


def summarise(events):
    answer = ''.join(d.get('text') or '' for k, d in events if k == 'delta')
    tools, results, citations, card, done, turn, conv = [], [], [], None, {}, None, None
    for kind, data in events:
        if kind == 'conversation':
            turn = data.get('turn_id')
            conv = data.get('conversation_id')
        elif kind == 'tool_start':
            tools.append({'name': data.get('name'), 'arguments': data.get('arguments') or {}})
        elif kind == 'tool_result':
            value = data.get('result') or {}
            rows = value.get('data')
            results.append({'name': data.get('name'), 'ok': value.get('ok'), 'error_code': value.get('error_code'),
                            'status': value.get('status'), 'job_status': value.get('job_status'),
                            'rows': len(rows) if isinstance(rows, list) else None})
        elif kind == 'citations':
            citations = data.get('items') or []
        elif kind == 'clarification':
            display = data.get('display') or {}
            card = {'request_id': data.get('request_id'), 'reason': data.get('reason'),
                    'question': data.get('question'), 'title': display.get('title'),
                    'targets': display.get('targets'),
                    'choices': [choice_summary(c) for c in (data.get('choices') or []) if isinstance(c, dict)],
                    'choices_full': [c for c in (data.get('choices') or []) if isinstance(c, dict)]}
        elif kind == 'done':
            done = data
    card_report = None
    if card:
        card_report = {k: v for k, v in card.items() if k != 'choices_full'}
    return {'answer': answer.strip(), 'status': done.get('status') or ('INCOMPLETE' if events else 'ERROR'),
            'reason': done.get('reason'), 'turn_id': turn,
            'conversation_id': done.get('conversation_id') or conv,
            'tools': tools, 'tool_results': results, 'card': card_report,
            'card_full': card, 'citations': [{'ordinal': c.get('ordinal'), 'source_type': c.get('source_type'),
                                              'title': c.get('title'), 'source_id': c.get('source_id'),
                                              'url': c.get('url') or c.get('source_url')} for c in citations]}


def contains_any(text, options):
    return any(option in text for option in options)


def matches_outcome(actual, expected):
    if not actual:
        return False
    actual, expected = str(actual), str(expected)
    return actual == expected or actual.startswith(expected + ':') or (expected.endswith(':') and actual.startswith(expected))


def internal_codes(text):
    plain = re.sub(r'https?://\S+', ' ', text or '')
    return sorted(set(CODE_RE.findall(plain)))


def contradicting_phrases(text):
    return [phrase for phrase in CONTRADICTIONS if phrase in (text or '')]


def grade(check, row):
    """Each check returns (name, passed, detail).  A check is machine-observable or it is not here."""
    out = []
    audit = row.get('audit') or {}
    answer = row.get('answer') or ''
    stages = audit.get('stages') or {}

    def add(name, ok, detail=''):
        out.append((name, bool(ok), detail))

    if 'route' in check:
        add('route', audit.get('route') in check['route'], audit.get('route'))
    if 'relationship' in check:
        rel = (stages.get('resolver') or {}).get('relationship')
        add('relationship', rel in check['relationship'], rel)
    names = [t['name'] for t in row.get('tools') or []]
    for name in check.get('tools_include', []):
        add('tool:' + name, name in names, names)
    for name in check.get('tools_exclude', []):
        add('no tool:' + name, name not in names, names)
    for group in check.get('tools_any', []):
        add('tool any:' + '|'.join(group), any(name in names for name in group), names)
    for spec in check.get('tool_args', []):
        hit = any(t['name'] == spec['name'] and all(t['arguments'].get(k) == v for k, v in spec['args'].items())
                  for t in row.get('tools') or [])
        add('args:' + spec['name'] + json.dumps(spec['args'], ensure_ascii=False), hit,
            [t for t in row.get('tools') or [] if t['name'] == spec['name']])
    proposed = [op.get('name') for op in row.get('operations') or [] if isinstance(op, dict)]
    for name in check.get('proposed_include', []):
        add('proposed:' + name, name in proposed, proposed)
    for group in check.get('proposed_any', []):
        add('proposed any:' + '|'.join(group), any(name in proposed for name in group), proposed)
    if 'card' in check:
        card = row.get('card')
        want = check['card']
        ok = (card is None) if want is False else (card is not None and (want is True or card.get('reason') == want))
        add('card', ok, card and card.get('reason'))
    for group in check.get('answer_any', []):
        add('answer has ' + '|'.join(group), contains_any(answer, group))
    for text in check.get('answer_excludes', []):
        add('answer lacks ' + text, text not in answer)
    if 'answer_absent_or_negative' in check:
        spec = check['answer_absent_or_negative']
        term = spec.get('term') if isinstance(spec, dict) else str(spec)
        unless = (spec.get('unless') if isinstance(spec, dict) else None) or ['不在', '没有', '已移出', '移除', '不含']
        ok = term not in answer or any(word in answer for word in unless)
        add('answer does not claim ' + str(term), ok, answer[:200])
    for source_type in check.get('citation_types', []):
        add('citation:' + source_type, any(c.get('source_type') == source_type for c in row.get('citations') or []),
            [c.get('source_type') for c in row.get('citations') or []])
    for group in check.get('citation_types_any', []):
        if isinstance(group, str):
            group = [group]
        types = [c.get('source_type') for c in row.get('citations') or []]
        add('citation any:' + '|'.join(group), any(source in group for source in types), types)
    if 'write_outcome' in check:
        expected = check['write_outcome'] if isinstance(check['write_outcome'], list) else [check['write_outcome']]
        actual = audit.get('write_outcome')
        add('write_outcome ' + '|'.join(expected), any(matches_outcome(actual, item) for item in expected), actual)
    if 'write_outcome_prefix' in check:
        actual = str(audit.get('write_outcome') or '')
        add('write_outcome prefix ' + check['write_outcome_prefix'], actual.startswith(check['write_outcome_prefix']), actual)
    if check.get('answer_no_codes'):
        bad_codes = internal_codes(answer)
        bad_phrases = contradicting_phrases(answer)
        add('answer has no internal codes', not bad_codes and not bad_phrases,
            '; '.join([*bad_codes, *bad_phrases]))
    if check.get('answer_no_ids'):
        bad_ids = sorted(set(UUID_RE.findall(answer)))
        add('answer has no internal ids', not bad_ids, '; '.join(bad_ids))
    if 'consent_status' in check:
        expected = check['consent_status'] if isinstance(check['consent_status'], list) else [check['consent_status']]
        actual = row.get('consent_status')
        add('consent status ' + '|'.join(expected), actual in expected, actual)
    if 'stop_reason' in check:
        actual = audit.get('stop_reason')
        add('stop_reason', actual in check['stop_reason'], actual)
    if check.get('no_dropped', True):
        add('nothing dropped', '未能与来源核对' not in answer, '')
    add('no protocol or placeholder', not re.search(r'DSML|<\|tool|<｜|已取得可核验来源|请拆成单独的一条再发', answer), '')
    if 'status' in check:
        add('status', row.get('status') in check['status'], row.get('status'))
    elif row.get('kind') not in {'db_check'}:
        add('not failed', row.get('status') not in {'failed', 'ERROR'}, row.get('status'))
    if 'max_seconds' in check:
        add('time', (row.get('seconds') or 0) <= check['max_seconds'], row.get('seconds'))
    return out


def pick_choice(card, kind, step):
    choices = [c for c in card.get('choices_full') or [] if isinstance(c, dict)]
    if not choices:
        return None
    if kind in {'confirm_card', 'reconfirm_card', 'stale_confirm'}:
        for choice in choices:
            if str(choice.get('label') or '').strip() in {'同意', '确认'}:
                return choice
        for choice in choices:
            if any(hint in str(choice.get('label') or '') for hint in CONFIRM_LABELS):
                return choice
        for choice in choices:
            if str(choice.get('value') or '').startswith('confirm:'):
                return choice
        return None
    if kind == 'cancel_card':
        for choice in choices:
            if any(hint in str(choice.get('label') or '') for hint in CANCEL_LABELS):
                return choice
        for choice in choices:
            if str(choice.get('value') or '').startswith('cancel:'):
                return choice
        return None
    if kind == 'choose_card':
        keywords = step.get('label') or step.get('contains') or ''
        if isinstance(keywords, str):
            keywords = [keywords]
        for choice in choices:
            if any(str(keyword) in str(choice.get('label') or '') for keyword in keywords):
                return choice
        prefix = step.get('value_prefix')
        if prefix:
            for choice in choices:
                if str(choice.get('value') or '').startswith(str(prefix) + ':'):
                    return choice
        return None
    return None


def db_expectation(value, expect):
    if expect is None:
        return bool(value.strip()), f'non-empty (got {value[:120]!r})'
    if isinstance(expect, str):
        match = re.match(r'^(gt|ge|lt|le|eq|ne|contains|not_contains|equals|matches):(.*)$', expect, re.S)
        if match:
            expect = {'op': match.group(1), 'value': match.group(2)}
        else:
            expect = {'op': 'contains', 'value': expect}
    if not isinstance(expect, dict):
        return False, f'unsupported expectation {expect!r}'
    op = expect.get('op') or ('equals' if 'equals' in expect else 'contains' if 'contains' in expect
                              else 'matches' if 'matches' in expect else 'gt')
    wanted = expect.get('value')
    if 'equals' in expect:
        wanted = expect['equals']
    if 'contains' in expect:
        wanted = expect['contains']
    if 'matches' in expect:
        wanted = expect['matches']
    actual = value.strip()
    if op in {'gt', 'ge', 'lt', 'le'}:
        try:
            number = float(actual.splitlines()[0].split('|')[0])
            wanted_number = float(wanted)
        except (ValueError, IndexError, TypeError):
            return False, f'{op} {wanted} (not numeric: {actual[:120]!r})'
        passes = {'gt': number > wanted_number, 'ge': number >= wanted_number,
                  'lt': number < wanted_number, 'le': number <= wanted_number}[op]
        return passes, f'{op} {wanted} (got {actual[:120]!r})'
    if op in {'eq', 'equals'}:
        return actual == str(wanted), f'equals {wanted!r} (got {actual[:120]!r})'
    if op == 'ne':
        return actual != str(wanted), f'not equals {wanted!r} (got {actual[:120]!r})'
    if op == 'contains':
        return str(wanted) in actual, f'contains {wanted!r} (got {actual[:120]!r})'
    if op == 'not_contains':
        return str(wanted) not in actual, f'not contains {wanted!r} (got {actual[:120]!r})'
    if op == 'matches':
        try:
            return bool(re.search(str(wanted), actual)), f'matches {wanted!r} (got {actual[:120]!r})'
        except re.error as exc:
            return False, f'invalid regex: {exc}'
    return False, f'unsupported op {op!r}'


def run_db_check(step, db):
    sql = str(step.get('sql') or '').strip().rstrip(';')
    label = step.get('label') or 'db_check'
    if not re.match(r'(?is)^\s*(select|with)\b', sql) or ';' in sql:
        return {'ok': False, 'detail': 'db_check only accepts one read-only SELECT/WITH statement', 'result': ''}
    try:
        value = psql(db, sql)
        ok, detail = db_expectation(value, step.get('expect'))
    except Exception as exc:
        return {'ok': False, 'detail': str(exc), 'result': ''}
    return {'ok': ok, 'detail': detail, 'result': value[:2000]}


def should_retry(row, pressed, db, card):
    """Retry only a transient provider/turn failure that produced no card and no completed consent."""
    if row.get('card'):
        return False
    status = row.get('status')
    reason = str(row.get('reason') or '')
    if status == 'ERROR':
        code = row.get('http_status') or 0
        return code in {429, 502, 503, 504} or code >= 500
    if status not in {'failed', 'INCOMPLETE'}:
        return False
    if not (reason in {'provider_timeout', 'provider_error', 'provider_protocol_invalid', 'provider_network_error',
                       'provider_rate_or_quota_error', 'no_progress'} or reason.startswith('provider_')):
        return False
    if pressed and card:
        try:
            if (consent_of(db, card.get('request_id')) or {}).get('status') in {'complete', 'cancelling'}:
                return False
        except Exception:
            pass
    return True


def emit_line(line):
    print(line, flush=True)


def run_case(case, base, db, model, owner, identity=None, retries=1, emit=emit_line):
    session = owner if (identity or case.get('identity', 'owner')) == 'owner' else secrets.token_hex(32)
    conversation = None
    last_card = None
    stale_card = None
    confirmed_card = None
    rows = []
    for index, step in enumerate(case['steps'], 1):
        kind = step.get('kind', 'user')
        if kind == 'new_session':
            conversation = None
            last_card = stale_card = confirmed_card = None
            continue
        if kind == 'db_check':
            outcome = run_db_check(step, db)
            label = step.get('label') or 'db_check'
            row = {'step': index, 'kind': kind, 'seconds': 0, 'label': label,
                   'db': outcome, 'checks': [{'name': 'db: ' + label, 'ok': outcome['ok'], 'detail': outcome['detail']}]}
            rows.append(row)
            emit(f"{case['id']}.{index} 0.0s db {label} {'PASS' if outcome['ok'] else 'FAIL ' + outcome['detail']}")
            continue

        full_card = None
        pressed = None
        if kind in {'cancel_card', 'confirm_card', 'choose_card', 'reconfirm_card', 'stale_confirm'}:
            if kind == 'reconfirm_card':
                full_card = confirmed_card
                if not full_card:
                    row = {'step': index, 'kind': kind, 'blocked': 'no confirmed card to confirm again'}
                    rows.append(row)
                    emit(f"{case['id']}.{index} BLOCKED {row['blocked']}")
                    continue
            elif kind == 'stale_confirm':
                full_card = stale_card
                if not full_card:
                    row = {'step': index, 'kind': kind, 'blocked': 'no earlier card was invalidated by a message'}
                    rows.append(row)
                    emit(f"{case['id']}.{index} BLOCKED {row['blocked']}")
                    continue
            else:
                full_card = last_card
                if not full_card and step.get('optional'):
                    # The data already held what the step would write (a saved page, a recorded review):
                    # the turn before answered that instead of showing a card, which is the right outcome.
                    emit(f"{case['id']}.{index} SKIPPED no card (optional step)")
                    continue
                if not full_card:
                    row = {'step': index, 'kind': kind, 'blocked': 'no card to press'}
                    rows.append(row)
                    emit(f"{case['id']}.{index} BLOCKED {row['blocked']}")
                    continue
            pressed = pick_choice(full_card, kind, step)
            if not pressed:
                row = {'step': index, 'kind': kind, 'blocked': 'no matching choice on the card',
                       'card': {k: v for k, v in full_card.items() if k != 'choices_full'}}
                rows.append(row)
                emit(f"{case['id']}.{index} BLOCKED {row['blocked']}")
                continue
            payload = {**model, 'conversation_id': conversation, 'message': pressed.get('value'),
                       'pending_request_id': full_card['request_id'], 'display_message': pressed.get('label')}
        else:
            payload = {**model, 'conversation_id': conversation, 'message': step['text']}

        started = time.monotonic()
        retries_used = []
        for attempt in range(retries + 1):
            try:
                row = summarise(stream(base, session, payload))
            except ApiError as exc:
                detail = str(exc.detail)
                if model.get('api_key'):
                    detail = detail.replace(model['api_key'], '[redacted]')
                row = {'answer': detail, 'status': 'ERROR', 'reason': str(exc.detail)[:300],
                       'http_status': exc.status, 'card': None, 'card_full': None, 'tools': [], 'tool_results': [],
                       'citations': []}
            except Exception as exc:
                row = {'answer': '', 'status': 'ERROR', 'reason': f'{type(exc).__name__}: {exc}'[:300],
                       'card': None, 'card_full': None, 'tools': [], 'tool_results': [], 'citations': []}
            attempt_seconds = round(time.monotonic() - started, 1)
            row.update(seconds=attempt_seconds)
            conversation = row.get('conversation_id') or conversation
            if attempt < retries and should_retry(row, pressed, db, full_card):
                retries_used.append({'status': row.get('status'), 'reason': row.get('reason'),
                                     'seconds': attempt_seconds})
                emit(f"{case['id']}.{index} retry {attempt + 1}: {row.get('status')}/{row.get('reason')} "
                     f"after {attempt_seconds}s")
                payload['conversation_id'] = conversation
                time.sleep(2)
                started = time.monotonic()
                continue
            break

        row.update(step=index, kind=kind, seconds=attempt_seconds,
                   message=(pressed.get('label') if pressed else step.get('text', '')),
                   display_message=(pressed.get('label') if pressed else None))
        if retries_used:
            row['retries'] = retries_used
        conversation = row.get('conversation_id') or conversation
        row['audit'] = audit_of(db, row.get('conversation_id'), row.get('turn_id'))
        if row.get('card_full'):
            row['operations'] = summarise_operations((consent_of(db, row['card_full'].get('request_id')) or {}).get('operations'))
        row['request_id'] = full_card['request_id'] if pressed else (row.get('card_full') or {}).get('request_id')
        if row['request_id']:
            consent = consent_of(db, row['request_id']) or {}
            row['consent_status'] = consent.get('status')
            if not row.get('operations') and consent.get('operations'):
                row['operations'] = summarise_operations(consent.get('operations'))

        new_card = row.pop('card_full', None)
        if kind in {'confirm_card', 'reconfirm_card'}:
            if full_card:
                confirmed_card = full_card
            last_card = new_card
        elif kind in {'cancel_card', 'choose_card', 'stale_confirm'}:
            last_card = new_card
        else:
            if last_card:
                stale_card = last_card
            last_card = new_card

        row['checks'] = [{'name': n, 'ok': ok, 'detail': d} for n, ok, d in grade(step.get('check', {}), row)]
        rows.append(row)
        failed = [c['name'] for c in row['checks'] if not c['ok']]
        write = (row.get('audit') or {}).get('write_outcome')
        emit(f"{case['id']}.{index} {row.get('seconds')}s {row.get('status')}/{row.get('reason')} "
             f"tools={[t['name'] for t in row.get('tools') or []]} card={bool(row.get('card'))} "
             f"proposed={[op.get('name') for op in row.get('operations') or [] if isinstance(op, dict)]} "
             f"write={write} {'PASS' if not failed else 'FAIL ' + '; '.join(failed)}")
    return rows


def print_block(case, lines):
    """Print one finished case as a single block; the lock keeps parallel cases from interleaving."""
    with PRINT_LOCK:
        print(f"\n== {case['id']} {case.get('title', '')} ==")
        for line in lines:
            print(line)
        sys.stdout.flush()


def case_blocks(cases):
    """Return worker jobs.  One serial_group is one job, so its cases run in file order, never together."""
    groups, blocks = {}, []
    for index, case in enumerate(cases):
        group = case.get('serial_group')
        if not group:
            blocks.append([(index, case)])
            continue
        group = str(group)
        if group not in groups:
            groups[group] = []
            blocks.append(groups[group])
        groups[group].append((index, case))
    return blocks


def run_block(items, base, db, model, owner, identity, retries):
    results = []
    for index, case in items:
        lines = []
        try:
            rows = run_case(case, base, db, model, owner, identity, retries, emit=lines.append)
        except BaseException:
            print_block(case, lines)
            raise
        print_block(case, lines)
        results.append((index, case, rows,
                        all(c['ok'] for r in rows for c in r.get('checks', []))
                        and not any(r.get('blocked') for r in rows)))
    return results


def run_attempt(cases, parallel, base, db, model, owner, identity, retries):
    if parallel == 1:
        results = []
        for block in case_blocks(cases):
            results.extend(run_block(block, base, db, model, owner, identity, retries))
        return results
    blocks = case_blocks(cases)
    with ThreadPoolExecutor(max_workers=parallel, thread_name_prefix='dialogue') as pool:
        futures = [pool.submit(run_block, block, base, db, model, owner, identity, retries)
                   for block in blocks]
        results = []
        for future in as_completed(futures):
            results.extend(future.result())
    return results


def main(argv):
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', default='http://127.0.0.1:9031')
    parser.add_argument('--db', default='test_g6_shadow_20261019')
    parser.add_argument('--suite', default=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                                        'docs/dialogue-tests/2026-10-19-suite-v2.json'))
    parser.add_argument('--cases', default='')
    parser.add_argument('--repeat', type=int, default=1)
    parser.add_argument('--parallel', type=int, default=4,
                        help='run up to N cases at once; cases in one serial_group still run in order (default 4)')
    parser.add_argument('--retries', type=int, default=1, help='retry a transient provider failure once (default 1)')
    parser.add_argument('--out', default='/tmp/market-g6/dialogue-v2')
    parser.add_argument('--identity', choices=['owner', 'guest'], help='run every case as this identity')
    parser.add_argument('--production', action='store_true',
                        help='grade a release smoke against the product database (read-only audit; no db_check)')
    parser.add_argument('--profile', choices=sorted(PROFILES), default='tokenplan',
                        help='model profile (default: tokenplan)')
    args = parser.parse_args(argv[1:])
    if args.parallel < 1:
        raise SystemExit('--parallel must be at least 1')
    global PRODUCTION_AUDIT
    PRODUCTION_AUDIT = bool(args.production)
    if not args.db.startswith(SHADOW_DB_PREFIX) and not (args.production and args.db == 'market_evidence'):
        raise SystemExit(f'refusing database {args.db!r}: only {SHADOW_DB_PREFIX}... may be used')
    suite = json.load(open(args.suite, encoding='utf-8'))
    if args.production and any(step.get('kind') == 'db_check' for case in suite['cases'] for step in case['steps']):
        raise SystemExit('--production runs no db_check steps')
    wanted = {c.strip().upper() for c in args.cases.split(',') if c.strip()}
    cases = [c for c in suite['cases'] if not wanted or c['id'] in wanted]
    if not cases:
        raise SystemExit('no cases selected')
    model = model_payload(args.profile)
    owner = json.load(open(os.path.join(ROOT, 'data/personal-chat-login.json'), encoding='utf-8'))['chat_credential']
    wait_for_api(args.base)
    os.makedirs(args.out, exist_ok=True)
    stamp = time.strftime('%Y%m%d-%H%M%S')
    report = []
    for attempt in range(1, args.repeat + 1):
        results = run_attempt(cases, args.parallel, args.base, args.db, model, owner,
                              args.identity, args.retries)
        for index, case, rows, passed in sorted(results, key=lambda item: item[0]):
            report.append({'case': case['id'], 'title': case.get('title'), 'attempt': attempt, 'rows': rows,
                           'passed': passed})
    path = os.path.join(args.out, f'results-{stamp}.json')
    json.dump({'suite': suite.get('suite_id'), 'profile': args.profile, 'cases': report},
              open(path, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    turns = [r for c in report for r in c['rows'] if r.get('checks') and r.get('kind') != 'db_check']
    db_rows = [r for c in report for r in c['rows'] if r.get('kind') == 'db_check']
    seconds = sorted(r.get('seconds') or 0 for r in turns)
    print(f"\ncases {sum(c['passed'] for c in report)}/{len(report)} passed; turns "
          f"{sum(all(x['ok'] for x in r['checks']) for r in turns)}/{len(turns)} passed; "
          f"db checks {sum(all(x['ok'] for x in r['checks']) for r in db_rows)}/{len(db_rows)} passed; "
          f"p50 {seconds[len(seconds)//2] if seconds else 0}s p90 {seconds[int(len(seconds)*0.9)] if seconds else 0}s; "
          f"saved {path}", flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))