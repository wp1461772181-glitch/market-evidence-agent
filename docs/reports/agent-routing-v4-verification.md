# Reference and write-grant repair verification

Status: implementation in progress; no v4 production release or live-model acceptance yet. Base origin/main c9a75bc; current rollback target fc58db3-verified. Preserve all raw failures and protected-file hashes.

## Scope and decisions

- Approved A1–A4 and B1–B6: see the checked-in user specification and implementation plan.
- User approved verified issuer/ticker mapping, and guest browser-local queue changes while forbidding guest server business writes.
- Issuer mapping: company_mention is copied from allowed context; English issuer/ticker is checked against official SEC metadata. Semantic translation of the original language remains model interpretation, not a proof of synonym equivalence. A failed lookup clarifies; no static company alias table is added.
- Capability-ledger rows are necessary for consent. Proposal-stage “no writes” prohibits business mutation, not the required durable capability metadata.
- User resolved the reversible-write conflict: all SERVER writes require confirmation capabilities, including owner queue/relevance changes. Guest local queue remains immediate. Original v3 R16/R27 will be migrated from immediate owner mutation to a confirmation card, with actual execution after consent tested separately.

## Evidence to complete

- Baseline production interpreter: 1270 passed / 7 skipped.
- A regression RED: 14 failures; focused reference/entity GREEN: 18 passed.
- Grant RED: 6 failures; focused reference/entity/grant GREEN: 24 passed.
- Suggestion RED: 5 failures; combined focused GREEN: 29 passed, including actual temporary PostgreSQL replay/concurrency and a second frozen web-confirmation card.
- Frontend suggestion normalization/title/button RED→GREEN completed; complete tests/build and final source hashes still pending.
- Original full-suite interim failures are saved in a-full.log and implementation-full-v1.log; they are not final acceptance.
- Live-model repeats, original 50 cases, two-interpreter final suites, independent review, CI, one release and public guest smoke remain unchecked.

## Owner self-test after acceptance

1. Say “去拉取一下 Intel 的 SEC 数据到本地”. Verify the INTC filing target and server card.
2. Click the system-provided authorization/confirmation option; verify actual status rather than prose acknowledgement.
3. Say “比较日期，网页更新就放入知识库”. Verify dates, conditions and the exact frozen-content confirmation before accepting.

## Invalid evaluator trial retained

The first original-50 trial was interrupted after detecting a harness error: the captured SEC issuer JSON is a list, while the fixture loader assumed a dict and called `.values()`. The production verifier accepts the SEC response correctly; the faulty evaluation stub raised inside `verified_pair`, creating false company-confirmation cards. This run is INVALID as an accuracy measurement, retained as deepseek-flash-original-50.json/.log without replacement. Its provider usage and one outstanding conservative reservation remain charged against the SAME USD 0.50 ledger. The corrected full run will use a new filename and include all cases; no selected pass replacement is permitted.

## Final source verification before paid acceptance

- A commits: 2a09139 and 82a412e. B commit: b719b2c. Evaluator/cases commit: cfe7d31.
- Both complete backend interpreters: 1308 passed, 7 skipped (production 48.23s; independent 25.64s). Three additional evaluator-loader tests passed separately after the harness fix.
- Frontend: declared test:i18n suite 87 passed; TypeScript/Vite build succeeded with existing large-chunk warning. Initial npm test command was invalid (no such script), retained in logs and corrected to the declared suite.
- Fresh independent branch reviewer reproduced the confirmed-boolean bypass; primary reproduced RED, removed public flag, required exact executing durable consent for reversible dispatch, and independently reran focused/full checks. No other runtime/frontend blocker found.
- Literal Latin issuer mismatch (Intel mention with Apple/AAPL pair) now rejects. Non-Latin mention→English issuer remains model semantic interpretation, NOT SEC-verified translation. SEC verifies the English issuer/ticker relationship only.
- 56 protected learner paths rehashed after development: zero changes. Production remains untouched.

## Native failures and narrow correction decision (before runtime edits)

The corrected harness exposed actual over-rejections: translated company with no optional ticker fails the verifier even when official SEC identifies a unique issuer (R01/R16/R20); a literal ticker such as AMD is incorrectly checked as a legal issuer name (R11/R25/R27); resolver's copied historical text is rejected when its informative mention is not a literal substring of the newest question (R02/R13), a requirement not present in A2. This full trial is retained, including unrelated finite claim-guard failures, and is not reclassified as harness-invalid.

Ruling: allow the official SEC unique issuer-to-ticker match to fill an omitted ticker, then apply the same pair/mention checks; preserve the explicit ticker itself as a valid grounded mention. Do not guess when several tickers match. For A2 the exact-copy authority is `text` from the named source; `mention` is descriptive rather than an extra newest-message quote constraint. These changes correct implementation restrictions within the approved mapping/source-copy scope. No numeric/claim/evidence or consent changes are allowed. A new complete evaluation version is required after changes, with all prior results and costs kept.

The complete pre-correction native trial scored **29/50**, natural **27/47**, p50 **4278 ms** (rejections are fast and this is NOT a useful successful-latency improvement), single-source median **2.5**; successful-only p50 **5117 ms**. It exposed over-rejections and independent provider/finite-claim-guard failures. Source fixes are committed in 0416bcc, with all four targeted RED→GREEN regressions. The v4 ten-repeat run now targets source 8b68e95. No production restart or release.

## User-approved source extension

User confirmed that sequential follow-ups may refer back to earlier assistant lists via message identifiers with exact source validation. Before edits: the first v4 trial on 8b68e95 is retained (including failures and all fees); it is incomplete and will not be merged with the next-source results. Extend only the internal Reference source enum with a server-derived assistant-message identifier scoped to the supplied conversation, not arbitrary history or facts. Existing assistant_previous/result_set/user_history remain compatible. The repeated-dialogue evaluator will continue V02 from each actual V01 conversation rather than re-generating duplicate setup answers, preserving every real intermediate answer.

Post-extension full backend: both interpreters **1316 passed / 7 skipped** (49.08s and 23.66s). Bounded historical message identifiers, exact earlier-source copying and forged/other-history rejection pass. Remaining native cases are ordered to run zero-cost safety probes, primary B/SEC/injection/conditional-write behaviors, then detailed list variants; every attempted case still runs ten consecutive trials. This ordering protects coverage under the remaining fixed cumulative budget, does not select preferred outcomes, and is recorded in the checked-in JSONL.

## Final focused correction decision before edits

Source1280881 native trial has B ordinal8/10: two resolver outputs summarized/concatenated the source instead of copying an item name; exact-source validation correctly rejected them. Conditional-write trials also exposed repeated unnecessary company cards when an omitted ticker met several issuer-prefix matches even though one official normalized issuer name matched exactly.

Ruling: after this frozen native run, prefer one unique EXACT official issuer-name match before the existing unique-prefix fallback, keeping ambiguous exact matches unresolved. Add at most ONE resolver correction attempt for reference_text_unbound, explicitly asking for the shortest exact item-name substring and preserving the same source check. Never accept the rejected paraphrase, invent a referent or relax financial/evidence/consent guards. Retain these native failures; the final corrected source needs fresh native acceptance. If the fixed cumulative budget cannot fund it, do not release or imply that the pre-correction results validate the final source.

## Superseded implementation scope

This is a historical V4 evidence report. User-approved V5 changes now supersede its former prohibition on modifying content guards and batch semantics. Final current local results and native/production limits are in [V5 verification](agent-routing-v5-verification.md); the V4 model trials above are not V5 acceptance.
