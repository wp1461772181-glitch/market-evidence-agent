"""Fetch the one first-party announcement used by the bounded Week 8 demo.

The full article is saved only under ``data/``, which is intentionally ignored
by Git.  The checked-in manifest preserves the source URL, digest, and a short
review anchor.  Re-running the script refreshes those two reproducible files.
"""

from __future__ import annotations

from hashlib import sha256
from html import unescape
from html.parser import HTMLParser
import json
from pathlib import Path
from urllib.request import Request, urlopen


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DOCUMENT_PATH = REPOSITORY_ROOT / "data" / "week8-documents" / "aapl-2026-q3.json"
MANIFEST_PATH = REPOSITORY_ROOT / "docs" / "week8-sources.json"
SOURCE_URL = "https://www.apple.com/newsroom/2026/07/apple-reports-third-quarter-results/"
EVIDENCE_QUOTE = (
    "Apple today announced financial results for its fiscal 2026 third quarter "
    "ended June 27, 2026."
)


class MainTextParser(HTMLParser):
    """Extract the visible main article text without a third-party dependency."""

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


def fetch_text() -> str:
    request = Request(SOURCE_URL, headers={"User-Agent": "market-evidence-agent-week8-source-cache/1.0"})
    with urlopen(request, timeout=30) as response:
        if response.status != 200:
            raise RuntimeError(f"aapl-2026-q3: HTTP {response.status}")
        parser = MainTextParser()
        parser.feed(response.read().decode("utf-8", errors="replace"))
    text = unescape(parser.text()).removeprefix("opens in new window\n")
    if len(text) < 500:
        raise RuntimeError("aapl-2026-q3: article text was unexpectedly short")
    if EVIDENCE_QUOTE not in text:
        raise RuntimeError("aapl-2026-q3: configured evidence quote was not found")
    return text


def main() -> None:
    text = fetch_text()
    document = {
        "document_id": "aapl-2026-q3",
        "company": "Apple",
        "ticker": "AAPL",
        "source_url": SOURCE_URL,
        "source_domain": "www.apple.com",
        "published_date": "2026-07-30",
        "title": "Apple reports third quarter results",
        "text": text,
        "sha256": sha256(text.encode("utf-8")).hexdigest(),
    }
    manifest = {
        "source_set_version": "week8-public-announcement-v1",
        "document_count": 1,
        "source_policy": "One first-party public Apple announcement for the bounded Week 8 revision demo; full text is local and gitignored.",
        "documents": [{
            **{key: value for key, value in document.items() if key != "text"},
            "text_characters": len(text),
            "gold_annotation": {
                "event_type": "earnings_release",
                "event_date": "2026-07-30",
                "expected_summary": "Apple reported fiscal 2026 third-quarter results, including quarterly revenue of $109.4 billion.",
                "evidence_quote": EVIDENCE_QUOTE,
            },
        }],
    }
    DOCUMENT_PATH.parent.mkdir(parents=True, exist_ok=True)
    DOCUMENT_PATH.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {DOCUMENT_PATH.relative_to(REPOSITORY_ROOT)} and {MANIFEST_PATH.relative_to(REPOSITORY_ROOT)}")


if __name__ == "__main__":
    main()
