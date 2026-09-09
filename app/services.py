import hashlib


MODEL_VERSION = "mock-v1"


def normalize_symbol(symbol: str) -> str:
    return symbol.strip().upper()


def is_valid_symbol(symbol: str) -> bool:
    return symbol.isascii() and symbol.isalpha() and 1 <= len(symbol) <= 5


def mock_forecast(symbol: str) -> tuple[float, float, float]:
    digest = hashlib.sha256(symbol.encode("ascii")).digest()
    bullish_weight = 0.20 + (digest[0] / 255) * 0.60
    neutral_weight = 0.10 + (digest[1] / 255) * 0.35
    bearish_weight = 0.25 + (digest[2] / 255) * 0.25
    total_weight = bullish_weight + neutral_weight + bearish_weight
    return tuple(
        weight / total_weight
        for weight in (bullish_weight, neutral_weight, bearish_weight)
    )
