import asyncio
import os
from sqlalchemy.exc import OperationalError, TimeoutError as SATimeout
from .agent_tools import TOOL_REGISTRY

TRANSIENT=(TimeoutError, asyncio.TimeoutError, ConnectionError, OperationalError, SATimeout)
async def execute_tool(name: str, arguments: dict) -> dict:
    tool=TOOL_REGISTRY.get(name)
    if tool is None: return {"ok":False,"data":None,"error_code":"unknown_tool","source":None,"as_of":None}
    try:
        parsed=tool.args_schema.model_validate(arguments)
    except Exception:
        return {"ok":False,"data":None,"error_code":"invalid_arguments","source":None,"as_of":None}
    try:
        # Cold BGE loading and cross-encoder swapping can take tens of seconds; a web search that
        # fetches several pages can take minutes. Market/ordinary tools keep their short budget.
        is_knowledge = name in {"query_research_faq", "query_materials", "search_material_analysis"}
        timeout = (max(1.0, float(os.getenv("KNOWLEDGE_RETRIEVAL_TIMEOUT_SECONDS", "60"))) if is_knowledge
                   else max(5.0, min(300.0, float(os.getenv("WEB_RESEARCH_TIMEOUT_SECONDS", "120")))) if name == "web_research"
                   else 5.0)
        async with asyncio.timeout(timeout):
            for attempt in range(2):
                try:
                    value=await tool.ainvoke(parsed.model_dump())
                    return value if isinstance(value,dict) and "ok" in value else {"ok":True,"data":value,"source":name,"as_of":None}
                except asyncio.CancelledError: raise
                except TRANSIENT:
                    if attempt: return {"ok":False,"data":None,"error_code":"tool_unavailable","source":name,"as_of":None}
                except Exception:
                    return {"ok":False,"data":None,"error_code":"tool_failed","source":name,"as_of":None}
    except TimeoutError:
        return {"ok":False,"data":None,"error_code":"tool_timeout","source":name,"as_of":None}
