"""The analysis budget must be spent on the sections that answer the questions."""

import unittest

from app.analysis_window import MAX_ANALYSIS_CHARACTERS, build_analysis_window


def _filler(text: str, size: int) -> str:
    block = (text + " ") * (size // (len(text) + 1) + 1)
    return block[:size]


def _annual_report() -> str:
    cover = ("UNITED STATES\nSECURITIES AND EXCHANGE COMMISSION\nWashington, D.C. 20549\n\n"
             "FORM 10-K\n\nANNUAL REPORT PURSUANT TO SECTION 13 OR 15(d)\n\n") + _filler("cover page blurb.", 2_000)
    contents = ("\n\nTABLE OF CONTENTS\n\nItem 1. Business 5\nItem 1A. Risk Factors 12\n"
                "Item 7. Management's Discussion and Analysis 30\n"
                "Item 8. Financial Statements and Supplementary Data 45\nItem 9. Changes in Accountants 90\n")
    business = "\n\nItem 1. Business\n\n" + _filler("We sell devices and services worldwide.", 12_000)
    risk = "\n\nItem 1A. Risk Factors\n\n" + _filler("Our results depend on supply and demand.", 6_000)
    mda = ("\n\nItem 7. Management's Discussion and Analysis of Financial Condition\n\n"
           "MDNA-BODY-MARKER Revenue grew because services expanded.\n\n") + _filler("Revenue grew on services.", 12_000)
    statements = ("\n\nItem 8. Financial Statements and Supplementary Data\n\n"
                  "Report of Independent Registered Public Accounting Firm\n\n" + _filler("We audited the accounts.", 4_000)
                  + "\n\nCONSOLIDATED BALANCE SHEETS\n\nCash and cash equivalents 12,000\nTotal assets 100,000\n"
                  + _filler("Accounts payable and accruals.", 3_000))
    tail = "\n\nItem 9. Changes in and Disagreements with Accountants\n\n" + _filler("None.", 3_000)
    return cover + contents + business + risk + mda + statements + tail


class AnalysisWindowTests(unittest.TestCase):
    def test_annual_report_window_spends_the_budget_on_the_accounts(self):
        text = _annual_report()
        self.assertGreater(len(text), MAX_ANALYSIS_CHARACTERS)
        window = build_analysis_window(text, form="10-K")
        self.assertEqual(window.mode, "sections")
        self.assertLessEqual(window.characters, MAX_ANALYSIS_CHARACTERS)
        self.assertIn("MDNA-BODY-MARKER", window.text)
        self.assertIn("Total assets 100,000", window.text)
        self.assertIn("CONSOLIDATED BALANCE SHEETS", window.text)
        # The cover page and the table of contents never reach the reader.
        self.assertNotIn("SECURITIES AND EXCHANGE COMMISSION", window.text)
        self.assertNotIn("TABLE OF CONTENTS", window.text)
        titles = [segment.title for segment in window.segments]
        self.assertTrue(any(title.startswith("Item 7.") for title in titles), titles)
        self.assertTrue(any(title.startswith("Item 8.") for title in titles), titles)
        self.assertEqual(window.missing, ())

    def test_the_table_of_contents_never_wins_over_the_body_section(self):
        text = _annual_report()
        window = build_analysis_window(text, form="10-K")
        for segment in window.segments:
            self.assertIn(segment.title.split(".")[0], text[segment.start:segment.end] + segment.title)
            self.assertGreater(segment.characters, 1_000, segment)

    def test_a_short_filing_is_read_whole(self):
        text = "Item 1. Business\n\nShort press release about results."
        window = build_analysis_window(text, form="8-K")
        self.assertEqual(window.mode, "whole")
        self.assertTrue(window.complete)
        self.assertEqual(window.coverage, 1.0)
        self.assertEqual(window.text, text)

    def test_a_document_without_headings_falls_back_to_its_opening(self):
        text = _filler("plain prose without any section title.", MAX_ANALYSIS_CHARACTERS * 2)
        window = build_analysis_window(text, form="10-K")
        self.assertEqual(window.mode, "prefix")
        self.assertFalse(window.complete)
        self.assertTrue(window.missing)
        self.assertEqual(window.characters, MAX_ANALYSIS_CHARACTERS)

    def test_locator_reports_the_character_ranges_actually_used(self):
        text = _annual_report()
        window = build_analysis_window(text, form="10-K")
        locator = window.locator()
        self.assertEqual(locator["kind"], "extracted_text_sections")
        self.assertEqual(len(locator["segments"]), len(window.segments))
        for segment, reported in zip(window.segments, locator["segments"]):
            self.assertEqual(text[segment.start:segment.end].strip("\n"), text[reported["start"]:reported["end"]].strip("\n"))
            self.assertIn(reported["title"], locator["sections"])

    def test_a_quarterly_report_prefers_item_two_and_item_one(self):
        cover = "FORM 10-Q\n\n" + _filler("quarterly cover.", 1_000)
        contents = "\n\nTABLE OF CONTENTS\n\nItem 1. Financial Statements 3\nItem 2. Management's Discussion 20\n"
        statements = "\n\nItem 1. Financial Statements\n\nCONDENSED CONSOLIDATED BALANCE SHEETS\nTotal assets 5,000\n" \
                     + _filler("cash and receivables.", 10_000)
        mda = "\n\nItem 2. Management's Discussion and Analysis\n\nQUARTER-MDA-MARKER\n" + _filler("Revenue rose.", 10_000)
        risk = "\n\nItem 1A. Risk Factors\n\n" + _filler("Risk unchanged.", 4_000)
        window = build_analysis_window(cover + contents + statements + mda + risk, form="10-Q")
        self.assertIn("QUARTER-MDA-MARKER", window.text)
        self.assertIn("Total assets 5,000", window.text)


if __name__ == "__main__":
    unittest.main()
