"""Prepare typed agent suggestions without external fetch or business mutation."""
import copy
from ..agent_tool_catalog import validate,write_tool_names

def suggestion_operation(name,arguments):
    if name not in write_tool_names():raise ValueError('invalid_action_tool')
    args=validate(name,arguments)
    operation={'name':name,'arguments':copy.deepcopy(args)}
    if name in {'fetch_sec','refresh_sec'}:
        operation['grant_only']=True
    if name=='ingest_web_source':
        from .web_sources import canonical_url
        operation['arguments']['url']=canonical_url(args['url'])
        operation['grant_only']=True
    return operation
