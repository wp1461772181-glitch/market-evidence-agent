"""Fetch the bounded, first-party Week 6 earnings-release source set.

The downloaded text is intentionally local-only (`data/` is gitignored).  The
small checked-in manifest records stable source metadata and review anchors.
Run this script from the repository root whenever the local cache is missing.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from html import unescape
from html.parser import HTMLParser
import json
from pathlib import Path
from urllib.request import Request, urlopen


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DOCUMENT_DIRECTORY = REPOSITORY_ROOT / "data" / "week6-documents"
MANIFEST_PATH = REPOSITORY_ROOT / "docs" / "week6-sources.json"
USER_AGENT = "market-evidence-agent-week6-source-cache/1.0"


@dataclass(frozen=True)
class Source:
    document_id: str
    company: str
    ticker: str
    source_url: str
    published_date: str
    title: str
    expected_summary: str
    evidence_quote: str


SOURCES: tuple[Source, ...] = (
    Source(
        "aapl-2024-q1", "Apple", "AAPL",
        "https://www.apple.com/newsroom/2024/02/apple-reports-first-quarter-results/",
        "2024-02-01", "Apple reports first quarter results",
        "Apple reported fiscal 2024 first-quarter results, including quarterly revenue of $119.6 billion.",
        "Apple today announced financial results for its fiscal 2024 first quarter ended December 30, 2023.",
    ),
    Source(
        "aapl-2024-q2", "Apple", "AAPL",
        "https://www.apple.com/newsroom/2024/05/apple-reports-second-quarter-results/",
        "2024-05-02", "Apple reports second quarter results",
        "Apple reported fiscal 2024 second-quarter results, including quarterly revenue of $90.8 billion.",
        "Apple today announced financial results for its fiscal 2024 second quarter ended March 30, 2024.",
    ),
    Source(
        "aapl-2024-q3", "Apple", "AAPL",
        "https://www.apple.com/newsroom/2024/08/apple-reports-third-quarter-results/",
        "2024-08-01", "Apple reports third quarter results",
        "Apple reported fiscal 2024 third-quarter results, including quarterly revenue of $85.8 billion.",
        "Apple today announced financial results for its fiscal 2024 third quarter ended June 29, 2024.",
    ),
    Source(
        "aapl-2024-q4", "Apple", "AAPL",
        "https://www.apple.com/newsroom/2024/10/apple-reports-fourth-quarter-results/",
        "2024-10-31", "Apple reports fourth quarter results",
        "Apple reported fiscal 2024 fourth-quarter results, including quarterly revenue of $94.9 billion.",
        "Apple today announced financial results for its fiscal 2024 fourth quarter ended September 28, 2024.",
    ),
    Source(
        "aapl-2025-q1", "Apple", "AAPL",
        "https://www.apple.com/newsroom/2025/01/apple-reports-first-quarter-results/",
        "2025-01-30", "Apple reports first quarter results",
        "Apple reported fiscal 2025 first-quarter results, including quarterly revenue of $124.3 billion.",
        "Apple today announced financial results for its fiscal 2025 first quarter ended December 28, 2024.",
    ),
    Source(
        "aapl-2025-q2", "Apple", "AAPL",
        "https://www.apple.com/newsroom/2025/05/apple-reports-second-quarter-results/",
        "2025-05-01", "Apple reports second quarter results",
        "Apple reported fiscal 2025 second-quarter results, including quarterly revenue of $95.4 billion.",
        "Apple today announced financial results for its fiscal 2025 second quarter ended March 29, 2025.",
    ),
    Source(
        "aapl-2025-q3", "Apple", "AAPL",
        "https://www.apple.com/newsroom/2025/07/apple-reports-third-quarter-results/",
        "2025-07-31", "Apple reports third quarter results",
        "Apple reported fiscal 2025 third-quarter results, including quarterly revenue of $94.0 billion.",
        "Apple today announced financial results for its fiscal 2025 third quarter ended June 28, 2025.",
    ),
    Source(
        "aapl-2025-q4", "Apple", "AAPL",
        "https://www.apple.com/newsroom/2025/10/apple-reports-fourth-quarter-results/",
        "2025-10-30", "Apple reports fourth quarter results",
        "Apple reported fiscal 2025 fourth-quarter results, including quarterly revenue of $102.5 billion.",
        "Apple today announced financial results for its fiscal 2025 fourth quarter ended September 27, 2025.",
    ),
    Source(
        "msft-2025-q1", "Microsoft", "MSFT",
        "https://news.microsoft.com/source/2024/10/30/microsoft-cloud-strength-drives-first-quarter-results-7/",
        "2024-10-30", "Microsoft Cloud strength drives first quarter results",
        "Microsoft reported fiscal 2025 first-quarter results, including revenue of $65.6 billion.",
        "Microsoft Corp. today announced the following results for the quarter ended September 30, 2024",
    ),
    Source(
        "msft-2025-q2", "Microsoft", "MSFT",
        "https://news.microsoft.com/source/2025/01/29/microsoft-cloud-and-ai-strength-drives-second-quarter-results-2/",
        "2025-01-29", "Microsoft Cloud and AI strength drives second quarter results",
        "Microsoft reported fiscal 2025 second-quarter results, including revenue of $69.6 billion.",
        "Microsoft Corp. today announced the following results for the quarter ended December 31, 2024",
    ),
)


class MainTextParser(HTMLParser):
    """Extract visible text from the page's main element using only stdlib."""

    _SKIPPED_TAGS = {"button", "script", "style", "svg", "noscript"}
    _BLOCK_TAGS = {"br", "div", "h1", "h2", "h3", "li", "p", "section", "table", "tr"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._main_depth = 0
        self._skip_depth = 0
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "main":
            self._main_depth += 1
        elif self._main_depth and tag in self._SKIPPED_TAGS:
            self._skip_depth += 1
        if self._main_depth and not self._skip_depth and tag in self._BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if self._main_depth and tag in self._SKIPPED_TAGS and self._skip_depth:
            self._skip_depth -= 1
        if self._main_depth and not self._skip_depth and tag in self._BLOCK_TAGS:
            self._parts.append("\n")
        if tag == "main" and self._main_depth:
            self._main_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._main_depth and not self._skip_depth:
            self._parts.append(data)

    def text(self) -> str:
        lines = (" ".join(line.split()) for line in "".join(self._parts).splitlines())
        return "\n".join(line for line in lines if line).strip()


def fetch_text(source: Source) -> str:
    request = Request(source.source_url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=30) as response:
        if response.status != 200:
            raise RuntimeError(f"{source.document_id}: HTTP {response.status}")
        parser = MainTextParser()
        parser.feed(response.read().decode("utf-8", errors="replace"))
    text = unescape(parser.text()).removeprefix("opens in new window\n")
    if len(text) < 500:
        raise RuntimeError(f"{source.document_id}: article text was unexpectedly short")
    if source.evidence_quote not in text:
        raise RuntimeError(f"{source.document_id}: configured evidence quote was not found")
    return text


def main() -> None:
    DOCUMENT_DIRECTORY.mkdir(parents=True, exist_ok=True)
    manifest_documents = []
    for source in SOURCES:
        text = fetch_text(source)
        text_hash = sha256(text.encode("utf-8")).hexdigest()
        document = {
            "document_id": source.document_id,
            "company": source.company,
            "ticker": source.ticker,
            "source_url": source.source_url,
            "source_domain": source.source_url.split("/")[2],
            "published_date": source.published_date,
            "title": source.title,
            "text": text,
            "sha256": text_hash,
        }
        (DOCUMENT_DIRECTORY / f"{source.document_id}.json").write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        manifest_documents.append({
            **{key: value for key, value in document.items() if key != "text"},
            "text_characters": len(text),
            "gold_annotation": {
                "event_type": "earnings_release",
                "event_date": source.published_date,
                "expected_summary": source.expected_summary,
                "evidence_quote": source.evidence_quote,
            },
        })

    manifest = {
        "source_set_version": "week6-public-earnings-v1",
        "document_count": len(manifest_documents),
        "source_policy": "First-party public earnings announcements only; cached main-article text is local and gitignored.",
        "documents": manifest_documents,
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(manifest_documents)} source documents and {MANIFEST_PATH.relative_to(REPOSITORY_ROOT)}")


if __name__ == "__main__":
    main()
