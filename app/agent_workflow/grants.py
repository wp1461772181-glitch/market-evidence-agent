"""Prepare typed agent suggestions without external fetch or business mutation."""
import copy
from ..agent_tool_catalog import validate,write_tool_names

def suggestion_operation(name,arguments):
    if name not in write_tool_names():raise ValueError('invalid_action_tool')
    # The profile learns only from the user's own words: an agent suggestion is not one.
    if name in {'remember','forget'}:raise ValueError('invalid_action_tool')
    args=validate(name,arguments)
    operation={'name':name,'arguments':copy.deepcopy(args)}
    if name in {'fetch_sec','refresh_sec'}:
        operation['grant_only']=True
    if name=='ingest_web_source':
        from .web_sources import canonical_url
        operation['arguments']['url']=canonical_url(args['url'])
        operation['grant_only']=True
    return operation


def label_suggestion_card(card,operation):
    """The approve button of a suggestion card says what approving does (the same words in chat and inbox)."""
    name=operation.get('name')
    card['choices'][0]['label']='授权发现目标，查看确认卡' if name in {'fetch_sec','refresh_sec'} else ('批准建议，查看正文确认卡' if operation.get('grant_only') else '批准并执行')
    return card
