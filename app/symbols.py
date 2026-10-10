"""Shared safe US ticker validation and provider symbol mapping."""
import re

_SYMBOL = re.compile(r"^[A-Z]{1,5}(?:[.-][A-Z]{1,2})?$")


def normalize_market_symbol(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("symbol must be a US stock ticker")
    normalized = value.strip().upper()
    if not _SYMBOL.fullmatch(normalized):
        raise ValueError("symbol must be a valid US ticker (letters and optional share-class suffix)")
    return normalized.replace("-", ".")


def yahoo_symbol(value: str) -> str:
    return normalize_market_symbol(value).replace(".", "-")


def sec_ticker_matches(value: str, candidate: object) -> bool:
    try:
        wanted = normalize_market_symbol(value).replace(".", "-")
        found = normalize_market_symbol(str(candidate)).replace(".", "-")
        return wanted == found
    except ValueError:
        return False
