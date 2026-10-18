"""Query 改写与扩写, on the retrieval side.

The user's question is rewritten once by the resolver (a complete question instead of "它");
this module is the other half: the *same* question said several ways, so that wording the index
happens not to use is not a silent miss.  Two rules from the course hold it together:

* the variants are made at search time and thrown away afterwards -- never at ingestion, where
  near-identical copies would take the places of other knowledge in the top-k;
* the variants are written by the model, which is the only thing that generalises wording.  The
  alias table and the clause split below are the offline fallback for a caller with no model (the
  tool tests, a degraded deployment), not the mechanism.

What comes back from the model is copy, not instruction: a variant may not introduce a ticker,
a number or a period the question did not contain, and the original query is always searched first.
"""
from __future__ import annotations

import contextvars
import json
import re

MAX_VARIANTS = 3
MAX_VARIANT_CHARS = 120

_ALIASES=(('营收','revenue'),('营业收入','revenue'),('经营现金流','operating cash flow'),('经营活动现金流','operating cash flow'),('毛利率','gross margin'))
def retrieval_query(query: str) -> str:
    """Append bounded bilingual aliases without editing the user's question."""
    additions=[]
    folded=query.casefold()
    for zh,en in _ALIASES:
        if zh in query and en not in folded:additions.append(en)
        elif en in folded and zh not in query:additions.append(zh)
    return query if not additions else query+' '+' '.join(additions)

def expanded_queries(query: str) -> list[str]:
    """The offline fallback: split a two-topic financial question in two.  Never alter ingestion."""
    groups = (("营收", "现金流"), ("revenue", "cash flow"),
              ("利润", "营收"), ("profit", "revenue"))
    lower = query.lower()
    for first, second in groups:
        if first in lower and second in lower:
            left = query.replace(second, "").strip(" 和及,，")
            right = query.replace(first, "").strip(" 和及,，")
            return list(dict.fromkeys((query, left, right)))[:MAX_VARIANTS]
    return [query]


EXPANSION_PROMPT = (
    "You write retrieval queries for a saved-evidence research index. Turn the user's question into "
    f"at most {MAX_VARIANTS} short search queries that mean the same thing, each with a different "
    "focus (the subject, the metric or the document type), so a passage phrased in other words is "
    "still found. Rules: keep every company, ticker, period and figure exactly as given; add none "
    "that the question does not contain; do not answer the question; do not translate a name into a "
    "different name. Return JSON only: {\"queries\": [string, ...]}.")

_EXPANDER: contextvars.ContextVar = contextvars.ContextVar("retrieval_query_expander", default=None)


class use_expander:
    """Install the model that writes variants for the searches inside this block."""

    def __init__(self, expander):
        self.expander = expander
        self._token = None

    def __enter__(self):
        self._token = _EXPANDER.set(self.expander)
        return self

    def __exit__(self, *exc_info):
        if self._token is not None:
            _EXPANDER.reset(self._token)
            self._token = None
        return False


def parse_variants(raw) -> list[str]:
    """The ``queries`` list of the model's JSON, as written.  Raises ValueError on anything else, so
    the caller falls back instead of searching for a stray sentence."""
    text = raw if isinstance(raw, str) else ""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("expansion_not_json")
    try:
        value = json.loads(text[start:end + 1])
    except ValueError as exc:
        raise ValueError("expansion_not_json") from exc
    queries = value.get("queries") if isinstance(value, dict) else None
    if not isinstance(queries, list):
        raise ValueError("expansion_not_json")
    return [item for item in queries if isinstance(item, str)]


_FIGURE = re.compile(r"\d+(?:[.,]\d+)*%?")
_TICKER = re.compile(r"\b[A-Z]{2,5}\b")
_YEAR = re.compile(r"(?:19|20)\d{2}")


def _grounded(variant: str, query: str) -> bool:
    """A variant may rephrase, never add: no figure, ticker or year the question did not name."""
    for pattern in (_FIGURE, _TICKER, _YEAR):
        allowed = set(pattern.findall(query))
        if any(item not in allowed for item in pattern.findall(variant)):
            return False
    return True


def clean_variants(query: str, variants) -> list[str]:
    """The queries actually searched: the original first, then the usable variants, deduplicated
    and bounded.  Order matters -- the original wording is the one the user chose."""
    if not isinstance(variants, (list, tuple)):
        return [query]
    seen = {query.strip().casefold()}
    kept = [query]
    for variant in variants:
        if not isinstance(variant, str):
            continue
        text = " ".join(variant.split())
        if len(text) < 2 or len(text) > MAX_VARIANT_CHARS:
            continue
        folded = text.casefold()
        if folded in seen or not _grounded(text, query):
            continue
        seen.add(folded)
        kept.append(text)
        if len(kept) >= MAX_VARIANTS:
            break
    return kept


async def query_variants(query: str) -> list[str]:
    """Wider wordings for a search that found nothing: the original first, then the variants.

    Called only after a miss, never on the happy path -- a model call is worth it where a silent
    miss is the alternative, and not for a question the index already answers."""
    expander = _EXPANDER.get()
    if expander is not None:
        try:
            return clean_variants(query, await expander(query))
        except Exception:  # a failed expansion is a missing variant, never a failed search
            pass
    return clean_variants(query, expanded_queries(query))
