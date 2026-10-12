from __future__ import annotations

import json
import re
from typing import Any

from ..symbols import normalize_market_symbol

INTENTS = frozenset({
    "saved_data", "source_qa", "analysis_qa", "faq_qa",
    "compound_research", "web_research", "general", "unsupported_action",
})
ROUTES = {
    "saved_data": ("query_stock_prices", False),
    "source_qa": ("query_materials", True),
    "analysis_qa": ("search_material_analysis", True),
    "faq_qa": ("query_research_faq", True),
    "compound_research": ("query_materials", True),
    "web_research": ("web_research", True),
    "general": (None, False),
    "unsupported_action": (None, False),
}


def classify_intent(raw: str) -> dict[str, Any]:
    """Parse the model's constrained classifier response; malformed labels clarify."""
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {"intent": "clarify", "symbol": None, "material_id": None}
    if not isinstance(value, dict) or value.get("intent") not in INTENTS:
        return {"intent": "clarify", "symbol": None, "material_id": None}
    symbol = value.get("symbol")
    if symbol is not None:
        if not isinstance(symbol, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9.-]{0,11}", symbol):
            return {"intent": "clarify", "symbol": None, "material_id": None}
        try:
            symbol = normalize_market_symbol(symbol)
        except (ValueError, TypeError):
            return {"intent": "clarify", "symbol": None, "material_id": None}
    material_id = value.get("material_id")
    if material_id is not None and (not isinstance(material_id, str) or len(material_id) > 128):
        return {"intent": "clarify", "symbol": None, "material_id": None}
    symbols = value.get("symbols") or ([symbol] if symbol else [])
    if not isinstance(symbols, list) or len(symbols) > 5:
        return {"intent": "clarify", "symbol": None, "material_id": None}
    try:
        symbols = list(dict.fromkeys(normalize_market_symbol(item) for item in symbols
                                      if isinstance(item, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9.-]{0,11}", item)))
    except (ValueError, TypeError):
        return {"intent": "clarify", "symbol": None, "material_id": None}
    if symbol and symbol not in symbols:
        symbols.insert(0, symbol)
    if symbol is None and len(symbols) == 1:
        symbol = symbols[0]
    return {"intent": value["intent"], "symbol": symbol, "symbols": symbols, "material_id": material_id,
            "web_required": value.get("web_required") is True,
            "original_required": value.get("original_required", value["intent"] == "compound_research") is True,
            "analysis_required": value.get("analysis_required") is True}


def route_for_intent(intent: str) -> tuple[str | None, bool]:
    return ROUTES.get(intent, (None, False))


def resolve_focus(current_symbol: str | None, current_material: str | None,
                  entities: dict[str, Any]) -> tuple[str | None, str | None]:
    symbol = entities.get("symbol") or current_symbol
    material_id = entities.get("material_id")
    if material_id is None and entities.get("symbol") and entities["symbol"] != current_symbol:
        material_id = None
    elif material_id is None:
        material_id = current_material
    return symbol, material_id
