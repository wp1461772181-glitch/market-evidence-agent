"""Reported figures come from the filing's own XBRL, never from prose."""

import unittest
from datetime import date

from app.xbrl_financials import as_payload, block_from_stored, collect, describe, reported_figures


def _payload(*entries, unit: str = "USD") -> dict:
    return {"cik": 320193, "taxonomy": "us-gaap", "tag": "Revenues", "units": {unit: list(entries)}}


def _entry(value: int, end: str, *, form: str = "10-K", filed: str = "2025-11-01", start: str | None = "2024-09-29") -> dict:
    return {"start": start, "end": end, "val": value, "accn": "0000320193-25-000001", "fy": 2025, "fp": "FY",
            "form": form, "filed": filed}


class ReportedFigureTests(unittest.TestCase):
    def test_only_the_figures_this_filing_reported_are_kept(self):
        payload = _payload(
            _entry(391_035_000_000, "2025-09-27"),
            _entry(383_285_000_000, "2024-09-28"),
            # Same fiscal year, but reported by a different filing.
            _entry(1, "2025-09-27", filed="2026-02-01"),
            _entry(2, "2025-09-27", form="10-Q"),
        )
        figures = reported_figures(payload, form="10-K", filed_at=date(2025, 11, 1))
        self.assertEqual([figure["value"] for figure in figures], [391_035_000_000, 383_285_000_000])

    def test_periods_are_newest_first_and_deduplicated(self):
        duplicate = _entry(10, "2024-09-28")
        payload = _payload(_entry(20, "2025-09-27"), duplicate, dict(duplicate))
        figures = reported_figures(payload, form="10-K", filed_at=date(2025, 11, 1), limit=5)
        self.assertEqual([figure["value"] for figure in figures], [20, 10])

    def test_non_money_units_never_become_money_figures(self):
        payload = {"units": {"USD": [_entry(5, "2025-09-27")],
                             "USD/shares": [_entry(7, "2025-09-27")],
                             "shares": [_entry(9, "2025-09-27")]}}
        figures = reported_figures(payload, form="10-K", filed_at=date(2025, 11, 1))
        self.assertEqual([figure["value"] for figure in figures], [5])


class CollectTests(unittest.TestCase):
    class _Provider:
        def __init__(self, answers: dict):
            self.answers = answers; self.asked: list[str] = []

        def company_concept(self, cik: str, concept: str) -> dict:
            self.asked.append(concept)
            if concept not in self.answers: raise RuntimeError("no such concept")
            return self.answers[concept]

    def test_the_first_revenue_tag_that_answers_wins(self):
        provider = self._Provider({"Revenues": _payload(_entry(391_035_000_000, "2025-09-27"))})
        figures = collect(provider, "0000320193", form="10-K", filed_at=date(2025, 11, 1), concepts=("revenue",))
        self.assertEqual(provider.asked, ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues"])
        self.assertEqual(figures[0]["metric"], "revenue")
        self.assertEqual(figures[0]["label"], "Revenue")

    def test_a_registrant_without_xbrl_yields_nothing_rather_than_raising(self):
        provider = self._Provider({})
        self.assertEqual(collect(provider, "0000320193", form="10-K", filed_at=date(2025, 11, 1)), [])


class DescribeTests(unittest.TestCase):
    def test_every_rendered_line_is_exact_text_an_analysis_can_quote(self):
        figures = [{"metric": "revenue", "label": "Revenue", "concept": "Revenues", "unit": "USD",
                    "periods": [{"value": 391_035_000_000, "start": "2024-09-29", "end": "2025-09-27"}]}]
        block = describe(figures)
        line = "Revenue — 2024-09-29 to 2025-09-27: USD 391,035,000,000 (391,035.0 million)"
        self.assertIn(line, block)
        self.assertIn("REPORTED FIGURES", block.splitlines()[0])
        self.assertTrue(block.rstrip().endswith("[END REPORTED FIGURES]"))

    def test_balance_sheet_values_are_dated_by_their_own_period(self):
        figures = [{"label": "Total assets", "unit": "USD",
                    "periods": [{"value": 359_241_000_000, "start": None, "end": "2025-09-27"}]}]
        self.assertIn("Total assets — 2025-09-27: USD 359,241,000,000", describe(figures))

    def test_nothing_stored_means_no_block(self):
        self.assertEqual(describe([]), "")
        self.assertEqual(block_from_stored(None), "")
        self.assertEqual(block_from_stored([]), "")

    def test_a_stored_payload_round_trips(self):
        figures = [{"metric": "net_income", "label": "Net income", "concept": "NetIncomeLoss", "unit": "USD",
                    "periods": [{"value": 112_010_000_000, "start": "2024-09-29", "end": "2025-09-27"}]}]
        stored = as_payload(figures)
        self.assertIn("USD 112,010,000,000", block_from_stored(stored))
        self.assertEqual(stored[0]["metric"], "net_income")


if __name__ == "__main__":
    unittest.main()
