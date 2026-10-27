"""Score claim-support decisions against the labelled set in tests/fixtures/claim_jev_labels/.

Evaluators:
  local   deterministic reference checker (figures, scale, period, direction, negation,
          entity, topic overlap). It is a baseline and a check on the scoring code, not a
          measure of Jev.
  live    the real ``jev-claim-support-v2`` call via ``evaluate_claim_supports``, in groups
          of CLAIM_JEV_MAX_PER_TURN (default 4) like production; each claim is its own request. Needs OPENROUTER_API_KEY.
          The daily cap file is not touched.
  replay  a JSON file of recorded results: {"<row id>": {"choice": ..., "status": "ok",
          "probabilities": {...}, "latency_ms": ...}}. ``--live --save-replay`` writes one.

The protocol (docs/claim-support-label-protocol.md) asks for precision and recall of
``does_not_support``; enforce drops a claim only on that choice, so a ``supports`` row
predicted ``does_not_support`` is a wrongly dropped true sentence.

Usage:
  python scripts/score_claim_jev_labels.py                       # local, prints markdown
  python scripts/score_claim_jev_labels.py --live --save-replay artifacts/claim-jev-replay.json
  python scripts/score_claim_jev_labels.py --replay artifacts/claim-jev-replay.json --check
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

LABELS_PATH = ROOT / "tests" / "fixtures" / "claim_jev_labels" / "labels.json"
LABELS = ("supports", "does_not_support", "unrelated")

# Proposed enforce bar. The protocol leaves the number open; these are the values this
# report checks against, and an operator can tighten them with the CLI flags.
MIN_DNS_PRECISION = 0.90
MIN_DNS_RECALL = 0.60
MAX_SUPPORTS_DROP_RATE = 0.10
MIN_OK_RATE = 0.90


@dataclass
class Prediction:
    choice: str | None
    status: str = "ok"
    latency_ms: int = 0
    probabilities: dict[str, float] = field(default_factory=dict)


@dataclass
class Score:
    evaluator: str
    rows: int
    confusion: dict[str, dict[str, int]]
    dns_precision: float | None
    dns_recall: float | None
    supports_drop_rate: float
    ok_rate: float
    request_latency_ms: list[int]
    errors: list[dict[str, Any]]

    def passes(self, *, min_precision=MIN_DNS_PRECISION, min_recall=MIN_DNS_RECALL,
               max_drop=MAX_SUPPORTS_DROP_RATE, min_ok=MIN_OK_RATE) -> bool:
        return (
            self.dns_precision is not None and self.dns_precision >= min_precision
            and self.dns_recall is not None and self.dns_recall >= min_recall
            and self.supports_drop_rate <= max_drop
            and self.ok_rate >= min_ok
        )


def load_labels(path: Path = LABELS_PATH) -> list[dict[str, Any]]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return list(data["rows"])


def score(rows: list[dict[str, Any]], predictions: dict[str, Prediction], *, evaluator: str,
          request_latency_ms: Iterable[int] = ()) -> Score:
    confusion = {gold: {pred: 0 for pred in (*LABELS, "unchecked")} for gold in LABELS}
    errors = []
    ok = 0
    for row in rows:
        prediction = predictions.get(row["id"]) or Prediction(None, "missing")
        predicted = prediction.choice if prediction.status == "ok" and prediction.choice in LABELS else "unchecked"
        ok += predicted != "unchecked"
        confusion[row["label"]][predicted] += 1
        if predicted != row["label"]:
            errors.append({"id": row["id"], "gold": row["label"], "predicted": predicted, "status": prediction.status})
    tp = confusion["does_not_support"]["does_not_support"]
    predicted_dns = sum(confusion[gold]["does_not_support"] for gold in LABELS)
    gold_dns = sum(confusion["does_not_support"].values())
    gold_supports = sum(confusion["supports"].values())
    return Score(
        evaluator=evaluator,
        rows=len(rows),
        confusion=confusion,
        dns_precision=tp / predicted_dns if predicted_dns else None,
        dns_recall=tp / gold_dns if gold_dns else None,
        supports_drop_rate=confusion["supports"]["does_not_support"] / gold_supports if gold_supports else 0.0,
        ok_rate=ok / len(rows) if rows else 0.0,
        request_latency_ms=list(request_latency_ms),
        errors=errors,
    )


# ---------------------------------------------------------------- local reference checker

_SCALES = (
    (r"万亿|trillion", 1e12), (r"十亿|billion|bn", 1e9), (r"亿", 1e8), (r"百万|million|mn", 1e6),
    (r"万", 1e4), (r"千|thousand", 1e3),
)
_HEADER = ((r"in\s+millions|百万", 1e6), (r"in\s+thousands", 1e3), (r"in\s+billions", 1e9))
_NUMBER = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)\s*(%|percent|万亿|十亿|亿|百万|万|千|trillion|billion|million|thousand|bn|mn)?", re.I)
_UP = re.compile(r"\b(rose|rise|grew|grow|increase[ds]?|up|higher|gain(?:ed)?)\b|增长|上涨|上升|增加|提高", re.I)
_DOWN = re.compile(r"\b(fell|fall|declin(?:e|ed)|decrease[ds]?|down|lower|drop(?:ped)?)\b|下降|下跌|减少|降低", re.I)
_NEG = re.compile(r"\b(not|no|never|did not|didn't)\b|未(?!来)|没有|不", re.I)
_EXPECT = re.compile(r"\b(expects?|expected|guid(?:e|ed|ance)|outlook|forecast|anticipates?)\b|预计|预期|指引", re.I)
_SUPERLATIVE = re.compile(r"\b(fastest|highest|lowest|largest|record|best|worst)\b|最高|最快|最大|创纪录", re.I)
_QUARTER = {"q1": "1", "first quarter": "1", "q2": "2", "second quarter": "2",
            "q3": "3", "third quarter": "3", "q4": "4", "fourth quarter": "4"}
_SYNONYMS = {
    "revenue": "revenue", "revenues": "revenue", "sales": "revenue", "营收": "revenue", "收入": "revenue",
    "net income": "net_income", "净利润": "net_income", "归母净利润": "net_income",
    "eps": "eps", "earnings per share": "eps",
    "margin": "margin", "毛利率": "margin",
    "close": "close", "收盘": "close",
    "dividend": "dividend", "股息": "dividend", "分红": "dividend",
    "buyback": "buyback", "repurchase": "buyback", "回购": "buyback",
    "employees": "employees", "员工": "employees",
    "cash flow": "cash_flow", "现金流": "cash_flow",
    "research and development": "rnd", "r&d": "rnd", "研发": "rnd",
    "operating income": "operating_income", "营业利润": "operating_income",
    "direction": "forecast", "上涨方向": "forecast", "下跌": "forecast", "上涨": "forecast", "forecast": "forecast", "预计未来": "forecast",
    "deliver": "deliveries", "delivered": "deliveries", "shares": "shares_outstanding",
    "volume": "volume", "成交量": "volume", "海外业务收入占比": "overseas_share",
    "call": "call", "电话": "call", "days": "days", "天": "days",
}
_TOPIC_KEYS = sorted(_SYNONYMS, key=len, reverse=True)
_STOP_ENTITIES = {"The", "A", "An", "Q1", "Q2", "Q3", "Q4", "FY", "Fiscal", "Revenue", "Net", "Close", "Diluted",
                  "Gross", "Operating", "Services", "Quarterly", "As", "This", "Item", "Research", "Company",
                  "January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
                  "November", "December"}

# Segment/product keywords that can carry distinct figures in the same quote.
_SEGMENT_KEYS = ("iphone", "ipad", "mac", "services", "wearables", "airpods", "apple watch")
_SEGMENT_RE = re.compile(r"\b(" + "|".join(_SEGMENT_KEYS) + r")\b", re.I)


def _segment_numbers(text: str) -> dict[str, set[float]]:
    """Return numbers grouped by segment keyword, using semicolon/clause boundaries."""
    result: dict[str, set[float]] = {}
    for clause in re.split(r"[;]", text):
        lowered = clause.lower()
        numbers = {v for v, _ in _numbers(lowered)}
        for match in _SEGMENT_RE.finditer(lowered):
            seg = match.group(1).lower()
            result.setdefault(seg, set()).update(numbers)
    return result


def _numbers_overlap(a: set[float], b: set[float], *, relative: float = 0.01, absolute: float = 0.01) -> bool:
    if not a or not b:
        return True
    for va in a:
        for vb in b:
            if abs(va - vb) <= max(absolute, abs(va) * relative):
                return True
    return False


def _segment_figures_match(claim: str, quote: str) -> bool:
    """If a segment keyword is shared, the numbers near it must match between claim and quote."""
    claim_segments = _segment_numbers(claim)
    quote_segments = _segment_numbers(quote)
    for seg in set(claim_segments) & set(quote_segments):
        if not _numbers_overlap(claim_segments[seg], quote_segments[seg]):
            return False
    return True


def _numbers(text: str, header_scale: float = 1.0) -> list[tuple[float, int]]:
    plain = re.sub(r"(?<=\d),(?=\d{3})", "", text)
    found = []
    for match in _NUMBER.finditer(plain):
        raw, unit = match.group(1), (match.group(2) or "").lower()
        value = float(raw)
        decimals = len(raw.split(".")[1]) if "." in raw else 0
        if unit in {"%", "percent"}:
            found.append((value, decimals))
            continue
        if not unit and 1900 <= value <= 2100 and value == int(value):
            continue  # years are checked as periods
        scale = next((s for pattern, s in _SCALES if unit and re.fullmatch(pattern, unit, re.I)), None)
        found.append((value * (scale or header_scale), decimals))
    return found


def _figures_match(claim: str, quote: str) -> bool:
    header = next((s for pattern, s in _HEADER if re.search(pattern, quote, re.I)), 1.0)
    available = [v for v, _ in _numbers(quote, header)] + [v for v, _ in _numbers(quote)]
    for value, decimals in _numbers(claim):
        tolerance = 0.5 * 10 ** (-decimals) * (value / float(f"{value:.{decimals}f}") if value else 1)
        if not any(abs(candidate - value) <= max(tolerance, abs(value) * 0.0006) for candidate in available):
            return False
    return True


def _periods(text: str) -> tuple[set[str], set[str], set[str]]:
    lowered = text.lower()
    years = set(re.findall(r"(?<!\d)(?:19|20)\d{2}(?!\d)", lowered)) | set(re.findall(r"fy(\d{4})", lowered))
    quarters = {q for key, q in _QUARTER.items() if re.search(rf"\b{key}\b", lowered)}
    dates = set(re.findall(r"\d{4}-\d{2}-\d{2}", lowered))
    months = set(re.findall(r"\b(?:january|february|march|april|may|june|july|august|september|october|november|december)\s+\d{1,2}\b", lowered))
    return years, quarters, dates | months


def _topics(text: str) -> set[str]:
    lowered = text.lower()
    topics = set()
    for key in _TOPIC_KEYS:
        if re.search(rf"(?<![a-z]){re.escape(key)}(?![a-z])", lowered):
            topics.add(_SYNONYMS[key])
            lowered = lowered.replace(key, " ")
    return topics


def _entities(text: str) -> set[str]:
    return {word for word in re.findall(r"\b[A-Z][a-z]{2,}\b", text) if word not in _STOP_ENTITIES}


def _direction(text: str) -> str | None:
    up, down = bool(_UP.search(text)), bool(_DOWN.search(text))
    return "up" if up and not down else "down" if down and not up else None


def local_choice(quote: str, claim: str) -> str:
    claim_topics, quote_topics = _topics(claim), _topics(quote)
    if claim_topics and not claim_topics & quote_topics:
        return "unrelated"
    if not claim_topics and not _numbers(claim):
        return "unrelated"
    if not _figures_match(claim, quote):
        return "does_not_support"
    if not _segment_figures_match(claim, quote):
        return "does_not_support"
    c_years, c_quarters, c_dates = _periods(claim)
    q_years, q_quarters, q_dates = _periods(quote)
    if (c_years - q_years) or (c_quarters - q_quarters) or (c_dates and q_dates and c_dates - q_dates):
        return "does_not_support"
    c_dir, q_dir = _direction(claim), _direction(quote)
    if c_dir and q_dir and c_dir != q_dir:
        return "does_not_support"
    if "direction=" in quote and c_dir and f"direction={c_dir}" not in quote:
        return "does_not_support"
    if bool(_NEG.search(quote)) != bool(_NEG.search(claim)):
        return "does_not_support"
    structured = re.match(r"^[a-z_]+: ", quote) is not None  # field citation, not guidance prose
    if not structured and _EXPECT.search(quote) and not _EXPECT.search(claim):
        return "does_not_support"
    if _SUPERLATIVE.search(claim) and not _SUPERLATIVE.search(quote):
        return "does_not_support"
    claim_entities = _entities(claim)
    if claim_entities and _entities(quote) and not claim_entities <= _entities(quote):
        return "does_not_support"
    return "supports"


def local_predictions(rows: list[dict[str, Any]]) -> tuple[dict[str, Prediction], list[int]]:
    predictions = {}
    for row in rows:
        started = time.perf_counter()
        choice = local_choice(row["quote"], row["claim"])
        predictions[row["id"]] = Prediction(choice, "ok", int((time.perf_counter() - started) * 1000))
    return predictions, []


def live_predictions(rows: list[dict[str, Any]], *, batch_size: int,
                     evaluate: Callable[..., list] | None = None) -> tuple[dict[str, Prediction], list[int]]:
    if evaluate is None:
        from app.jev_claim_support import evaluate_claim_supports as evaluate
    predictions, latencies = {}, []
    for start in range(0, len(rows), max(1, batch_size)):
        batch = rows[start:start + max(1, batch_size)]
        started = time.monotonic()
        results = evaluate([(row["id"], row["quote"], row["claim"]) for row in batch], daily_cap=None)
        latencies.append(int((time.monotonic() - started) * 1000))
        for row, result in zip(batch, results):
            predictions[row["id"]] = Prediction(result.choice, result.status, result.latency_ms, dict(result.probabilities))
    return predictions, latencies


def replay_predictions(path: Path) -> tuple[dict[str, Prediction], list[int]]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    predictions = {
        row_id: Prediction(item.get("choice"), item.get("status", "ok"), int(item.get("latency_ms") or 0),
                           dict(item.get("probabilities") or {}))
        for row_id, item in data.get("results", data).items()
    }
    return predictions, [int(v) for v in data.get("request_latency_ms", [])] if "results" in data else []


def reaches_jev(row: dict[str, Any]) -> bool:
    """Whether the enforce quote gate would pass this row on to the Jev check at all."""
    from app.agent_workflow.claim_review import apply_claim_gate

    claims = {"claims": [{
        "id": "c1", "statement": row["claim"], "citation_ordinal": 1,
        "evidence": {"type": "quote", "evidence_quote": row["quote"].splitlines()[-1]},
    }]}
    seen: list[Any] = []
    apply_claim_gate(
        f"{row['claim']} [1]\n" + json.dumps(claims, ensure_ascii=False),
        [{"ordinal": 1, "quote": row["quote"], "source_type": "official_filing"}],
        mode="enforce", support_mode="shadow", support_fn=lambda items: seen.extend(items) or [],
    )
    return bool(seen)


# ---------------------------------------------------------------- report

def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1%}"


def _report_header(labels_path: Path | None, evaluator: str) -> list[str]:
    version = "unknown"
    if labels_path and labels_path.exists():
        try:
            version = json.loads(labels_path.read_text(encoding="utf-8")).get("version", "unknown")
        except Exception:
            pass
    return [
        "# Claim Jev Support Metrics Report",
        "",
        f"- **Date**: 2026-10-27",
        f"- **Label set**: `{version}`",
        f"- **Evaluator**: `{evaluator}`",
        f"- **Proposed enforcement bar**: does_not_support precision ≥ {MIN_DNS_PRECISION:.0%}, "
        f"does_not_support recall ≥ {MIN_DNS_RECALL:.0%}, supports drop rate ≤ {MAX_SUPPORTS_DROP_RATE:.0%}, "
        f"status ok rate ≥ {MIN_OK_RATE:.0%}",
        "",
        "## Quality notes",
        "",
        "- This report is generated from the labelled fixture set. Live/replay runs are required before "
        "any production enforce decision, because the local reference checker is only a deterministic baseline.",
        "- Latency numbers below reflect the evaluator used: the local checker issues no HTTP requests, "
        "so live runs should be evaluated with `--live` or `--replay` for realistic latency.",
        "- Disagreements listed below are rows where the evaluator's predicted label differs from the "
        "human label; they are the primary signal for model or checker quality.",
        "",
    ]


def render(result: Score, rows: list[dict[str, Any]], *, labels_path: Path | None = None) -> str:
    by_id = {row["id"]: row for row in rows}
    counts = {label: sum(row["label"] == label for row in rows) for label in LABELS}
    lines = _report_header(labels_path, result.evaluator)
    lines += [
        f"### Evaluator: `{result.evaluator}`",
        "",
        f"Rows: {result.rows} (supports {counts['supports']}, does_not_support {counts['does_not_support']}, unrelated {counts['unrelated']})",
        "",
        "| Metric | Value | Proposed bar |",
        "| --- | --- | --- |",
        f"| does_not_support precision | {_pct(result.dns_precision)} | ≥ {MIN_DNS_PRECISION:.0%} |",
        f"| does_not_support recall | {_pct(result.dns_recall)} | ≥ {MIN_DNS_RECALL:.0%} |",
        f"| supports rows dropped (predicted does_not_support) | {_pct(result.supports_drop_rate)} | ≤ {MAX_SUPPORTS_DROP_RATE:.0%} |",
        f"| status ok rate | {_pct(result.ok_rate)} | ≥ {MIN_OK_RATE:.0%} |",
        f"| passes bar | {'yes' if result.passes() else 'no'} | |",
        "",
        "Confusion (rows = gold, columns = predicted):",
        "",
        "| gold \\ predicted | supports | does_not_support | unrelated | unchecked |",
        "| --- | --- | --- | --- | --- |",
    ]
    for gold in LABELS:
        cells = result.confusion[gold]
        lines.append(f"| {gold} | {cells['supports']} | {cells['does_not_support']} | {cells['unrelated']} | {cells['unchecked']} |")
    lines.append("")
    if result.request_latency_ms:
        values = sorted(result.request_latency_ms)
        p95 = values[min(len(values) - 1, int(round(0.95 * (len(values) - 1))))]
        lines.append(f"Request latency: n={len(values)}, p50 {int(statistics.median(values))} ms, p95 {p95} ms, max {values[-1]} ms.")
    else:
        lines.append("Request latency: no HTTP requests in this run.")
    lines.append("")
    if result.errors:
        lines += ["Disagreements:", "", "| id | gold | predicted | status | claim |", "| --- | --- | --- | --- | --- |"]
        for error in result.errors:
            claim = by_id[error["id"]]["claim"].replace("|", "\\|")
            lines.append(f"| {error['id']} | {error['gold']} | {error['predicted']} | {error['status']} | {claim} |")
    else:
        lines.append("Disagreements: none.")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--live", action="store_true", help="call jev-claim-support-v2 (needs OPENROUTER_API_KEY)")
    source.add_argument("--replay", type=Path, help="score recorded results")
    parser.add_argument("--labels", type=Path, default=LABELS_PATH)
    parser.add_argument("--batch-size", type=int, default=None, help="claims per group, one request each (default CLAIM_JEV_MAX_PER_TURN)")
    parser.add_argument("--save-replay", type=Path, help="with --live: write results for later --replay")
    parser.add_argument("--out", type=Path, help="write the markdown section here instead of stdout")
    parser.add_argument("--check", action="store_true", help="exit 1 if the proposed bar is not met")
    parser.add_argument("--subset", choices=("all", "reached"), default="all",
                        help="reached: only rows the enforce quote gate passes on to Jev")
    args = parser.parse_args(argv)

    rows = load_labels(args.labels)
    if args.subset == "reached":
        rows = [row for row in rows if reaches_jev(row)]
    if args.live:
        from app.agent_workflow.claim_gate_flags import claim_jev_max_per_turn
        predictions, latencies = live_predictions(rows, batch_size=args.batch_size or claim_jev_max_per_turn() or 4)
        from app.jev_claim_support import QUESTION_VERSION
        evaluator = f"live {QUESTION_VERSION}"
        if args.save_replay:
            args.save_replay.parent.mkdir(parents=True, exist_ok=True)
            args.save_replay.write_text(json.dumps({
                "request_latency_ms": latencies,
                "results": {k: vars(v) for k, v in predictions.items()},
            }, ensure_ascii=False, indent=2), encoding="utf-8")
    elif args.replay:
        predictions, latencies = replay_predictions(args.replay)
        evaluator = f"replay {args.replay.name}"
    else:
        predictions, latencies = local_predictions(rows)
        evaluator = "local reference checker"
    if args.subset == "reached":
        evaluator += ", rows reaching Jev after the quote gate"
    result = score(rows, predictions, evaluator=evaluator, request_latency_ms=latencies)
    text = render(result, rows, labels_path=args.labels)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    else:
        print(text)
    return 1 if args.check and not result.passes() else 0


if __name__ == "__main__":
    raise SystemExit(main())
