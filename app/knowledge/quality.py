from __future__ import annotations
import json

def evidence_state(result:dict) -> str:
    if not result.get('ok'):return 'unavailable'
    data=result.get('data')
    return 'empty' if isinstance(data,list) and not data else 'present'

def parse_sufficiency(content:str|None) -> tuple[bool,str]:
    try:value=json.loads(content or '')
    except (ValueError,TypeError) as exc:raise ValueError('invalid_sufficiency_verdict') from exc
    if not isinstance(value,dict) or type(value.get('sufficient')) is not bool:raise ValueError('invalid_sufficiency_verdict')
    reason=value.get('reason','unspecified')
    if not isinstance(reason,str) or len(reason)>80:raise ValueError('invalid_sufficiency_verdict')
    return value['sufficient'],reason
