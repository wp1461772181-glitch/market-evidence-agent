"""Reported figures taken straight from a filing's XBRL, not from its prose.

Reading "Total revenues ... 391,035" out of extracted text is guesswork: the
number may sit outside the analysis window, carry a footnote mark, or belong to
the prior year's column.  The filing already states each figure in a machine
readable form, so the analysis reads it from there and cites the rendered line.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Iterable

# Ordered fallbacks: companies report revenue under different tags.
CONCEPTS: dict[str, tuple[str, ...]] = {
    "revenue": ("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues",
                "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet"),
    "cost_of_revenue": ("CostOfRevenue", "CostOfGoodsAndServicesSold"),
    "gross_profit": ("GrossProfit",),
    "operating_income": ("OperatingIncomeLoss",),
    "net_income": ("NetIncomeLoss",),
    "operating_cash_flow": ("NetCashProvidedByUsedInOperatingActivities",
                            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"),
    "total_assets": ("Assets",),
    "cash_and_equivalents": ("CashAndCashEquivalentsAtCarryingValue",
                             "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"),
    "total_liabilities": ("Liabilities",),
    "stockholders_equity": ("StockholdersEquity",
                            "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"),
}
LABELS: dict[str, str] = {
    "revenue": "Revenue",
    "cost_of_revenue": "Cost of revenue",
    "gross_profit": "Gross profit",
    "operating_income": "Operating income",
    "net_income": "Net income",
    "operating_cash_flow": "Cash from operating activities",
    "total_assets": "Total assets",
    "cash_and_equivalents": "Cash and cash equivalents",
    "total_liabilities": "Total liabilities",
    "stockholders_equity": "Stockholders' equity",
}
MONEY_UNITS = ("USD",)
MAX_PERIODS = 4
# Forms whose XBRL carries the primary statements an analysis quotes.
FACT_FORMS = frozenset({"10-K", "10-Q", "20-F"})


def reported_figures(payload: dict, *, form: str, filed_at: date, limit: int = MAX_PERIODS) -> list[dict[str, Any]]:
    """The values this exact filing reported, newest period first."""
    units = payload.get("units") if isinstance(payload, dict) else None
    if not isinstance(units, dict): return []
    out: list[dict[str, Any]] = []
    seen: set[tuple] = set()
    for unit, entries in units.items():
        if unit not in MONEY_UNITS or not isinstance(entries, list): continue
        for entry in entries:
            if not isinstance(entry, dict): continue
            if str(entry.get("form") or "") != form: continue
            # ``filed`` pins the value to this filing; an amended or later report
            # about the same period is a different document and must not leak in.
            if str(entry.get("filed") or "") != filed_at.isoformat(): continue
            value = entry.get("val"); end = entry.get("end")
            if value is None or not end: continue
            start = entry.get("start")
            key = (start, end, value)
            if key in seen: continue
            seen.add(key)
            out.append({"unit": unit, "value": value, "start": start, "end": end,
                        "fiscal_period": entry.get("fp"), "fiscal_year": entry.get("fy")})
    out.sort(key=lambda item: (item["end"], item["start"] or ""), reverse=True)
    return out[:limit]


def collect(provider, cik: str, *, form: str, filed_at: date,
            concepts: Iterable[str] | None = None) -> list[dict[str, Any]]:
    """Fetch every metric this filing reported.  Network failures yield no figures."""
    findings: list[dict[str, Any]] = []
    for metric in (concepts or CONCEPTS):
        for concept in CONCEPTS.get(metric, ()):
            try:
                payload = provider.company_concept(cik, concept)
            except Exception:  # noqa: BLE001 - a missing concept is not an error
                continue
            figures = reported_figures(payload, form=form, filed_at=filed_at)
            if not figures: continue
            findings.append({"metric": metric, "label": LABELS.get(metric, metric),
                             "concept": concept, "unit": figures[0]["unit"], "periods": figures})
            break
    return findings


def _amount(value: Any) -> str:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return str(value)
    return f"{number:,}"


def _period_text(period: dict[str, Any]) -> str:
    start = period.get("start"); end = period.get("end")
    if start: return f"{start} to {end}"
    return str(end)


def describe(figures: Iterable[dict[str, Any]]) -> str:
    """A citable block: every line is exact text the analysis may quote."""
    lines: list[str] = []
    for figure in figures:
        for period in figure.get("periods") or []:
            million = ""
            try:
                million = f" ({int(period['value']) / 1_000_000:,.1f} million)"
            except (TypeError, ValueError, KeyError):
                million = ""
            lines.append(f"{figure['label']} — {_period_text(period)}: "
                         f"{figure.get('unit', 'USD')} {_amount(period.get('value'))}{million}")
    if not lines: return ""
    return ("[REPORTED FIGURES FROM THIS FILING'S XBRL — authoritative, prefer these over prose]\n"
            + "\n".join(lines)
            + "\n[END REPORTED FIGURES]")


def as_payload(figures: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Trim to what the analysis needs before the figures are frozen in a snapshot."""
    return [{"metric": figure.get("metric"), "label": figure.get("label"), "concept": figure.get("concept"),
             "unit": figure.get("unit"), "periods": (figure.get("periods") or [])[:MAX_PERIODS]}
            for figure in figures]


def block_from_stored(stored: Any) -> str:
    if not stored: return ""
    try:
        return describe(stored)
    except (TypeError, AttributeError):
        return ""
