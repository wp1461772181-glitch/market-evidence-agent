# App-wide Chinese and English Display Implementation Plan

> **For agentic workers:** Implement the tasks in order. Preserve all existing workspace changes and data. Steps use checkbox syntax for tracking.

**Goal:** Add a persistent Chinese/English switch that localizes every workspace screen and lazily translates saved AI analyses and research briefs into a separate reusable cache.

**Architecture:** A typed React locale provider supplies dictionaries and locale formatters to the whole app. A separate PostgreSQL translation cache stores DeepSeek translations of approved AI narrative fields, keyed by immutable record, canonical-content hash, locale, and prompt version; originals and prediction records remain untouched.

**Tech Stack:** React 19, TypeScript, Vite, FastAPI, SQLAlchemy, PostgreSQL JSONB, existing DeepSeek JSON provider.

**Spec:** `docs/superpowers/specs/2026-09-28-app-language-toggle-design.md`

## Global Constraints

- Support `zh-CN` and `en-US`; Chinese remains the first-visit default.
- Persist the selected locale in `localStorage` and rerender immediately.
- Preserve source-authored titles, exact quotations, IDs, source links, dates, enums, probabilities, and numeric values.
- Keep canonical analysis payloads, research briefs, prediction inputs, forecast versions, and Jev calls unchanged.
- Generate AI-content translations lazily and cache by content kind, immutable record ID, source SHA-256, locale, and translation prompt version.
- Do not send arbitrary browser-provided source text to the translation provider.
- Translation failure is not cached and must be presented honestly in the selected UI language.
- Add only an additive database table through an explicit, idempotent migration.

## Review Focus

- A source record with repeated strings or repeated numeric values must retain its exact identifiers and value tokens after translation; pin this in the localization-service tests.
- Two simultaneous cache misses for the same record must not create duplicate cache rows or duplicate successful translations; pin the cache-key uniqueness and conflict handling in service/API tests.
- A provider timeout, malformed JSON, missing/extra translation key, or changed numeric token must not replace canonical content or persist a partial cache; pin each failure class in localization API tests.
- An older database without the new table must produce a clear migration-required response; pin this in the API/migration tests.
- A locale change while a translation request is in flight must not let a stale response overwrite the current-language view; pin cancellation/current-locale behavior in the frontend acceptance check.

---

### Task 1: Add the additive translation cache and server localization API

**Files:**
- Create: `app/localization_models.py`
- Create: `app/ai_content_localization.py`
- Create: `app/localization_api.py`
- Create: `scripts/migrate_ai_content_translations.py`
- Modify: `app/main.py`
- Test: `tests/test_ai_content_localization.py`
- Test: `tests/test_ai_content_localization_api.py`
- Test: `tests/test_ai_content_localization_migration.py`

**Interfaces:**
- `localize_ai_content(db: Session, *, content_kind: Literal["material_analysis", "forecast_brief"], content_id: UUID, locale: Literal["en-US"], provider_factory: Callable[[], DeepSeekEventProvider] | None = None) -> dict[str, Any]`
- API response: `{content_kind, content_id, locale, source_sha256, prompt_version, cache_hit, fields: dict[str, str]}`.
- Translation fields are addressed by stable field paths; only the enumerated translatable values are returned. The server merges the values for display; IDs, source titles, quotes, URLs, dates, enums, and canonical records are loaded from the database and never taken from client input.
- Provider calls use `DeepSeekEventProvider.extract(...)` with JSON mode. The translation validator requires an exact field-key set and preserves every numeric token in each source value.
- A unique key covers `(content_kind, content_id, source_sha256, locale, prompt_version)`; use a PostgreSQL advisory transaction lock or an atomic insert-on-conflict path.

- [ ] **Step 1: Write service tests** for exact selected field paths, unchanged quote/ID/numeric values, cache hit with one provider call, hash invalidation, and invalid output not persisted.
- [ ] **Step 2: Run the focused service tests and confirm they fail** because the localization model/service do not yet exist.

Run: `.venv/bin/python -m pytest tests/test_ai_content_localization.py -q`
Expected: FAIL on missing localization interfaces.

- [ ] **Step 3: Implement `AIContentTranslation` and `localize_ai_content`** in the named modules, translating only material-analysis narrative fields and the narrative fields of a forecast brief. Copy identifiers and citation structure from canonical source data; never store any change in the source rows.
- [ ] **Step 4: Add API tests and implement** `POST /v3/material-analyses/{analysis_id}/localization` and `POST /v2/forecast-versions/{version_id}/brief-localization`. Each route accepts no source-text payload, returns a cache hit when available, and maps missing schema/provider/validation errors to stable safe responses.
- [ ] **Step 5: Add and test the explicit migration** with read-only `--check`, locked idempotent `--apply`, prerequisite checks, and no table drops or existing-data updates.
- [ ] **Step 6: Register the router and keep startup table creation from bypassing the explicit migration.** Add a migration-required guard to both localization endpoints.
- [ ] **Step 7: Run focused backend checks.**

Run: `.venv/bin/python -m pytest tests/test_ai_content_localization.py tests/test_ai_content_localization_api.py tests/test_ai_content_localization_migration.py -q`
Expected: PASS; migration applies twice in a disposable PostgreSQL database and existing canonical rows remain unchanged.

### Task 2: Add the frontend locale layer and top-bar selector

**Files:**
- Create: `frontend/src/i18n.tsx`
- Create: `frontend/src/locales/zh-CN.json`
- Create: `frontend/src/locales/en-US.json`
- Create: `frontend/scripts/i18n.test.mjs`
- Modify: `frontend/src/main.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/package.json`
- Modify: `frontend/src/styles.css`

**Interfaces:**
- `Locale = "zh-CN" | "en-US"`.
- `LocaleProvider`, `useLocale() -> { locale, setLocale, t }`, `formatDateTime`, `formatDate`, and `formatNumber` are the shared locale interface.
- `t` accepts typed dictionary keys and returns the selected locale's string; no UI component contains model-generated translations.

- [ ] **Step 1: Write the Node built-in test** in `frontend/scripts/i18n.test.mjs` for dictionary key parity and non-empty values.
- [ ] **Step 2: Run `npm run test:i18n` and confirm it fails** before the dictionaries and locale helper exist.
- [ ] **Step 3: Implement locale JSON files, typed `i18n.tsx`, persistence, and shared formatters** with `zh-CN` as default and immediate React state updates. Type English keys as `Record<keyof typeof zhCN, string>`.
- [ ] **Step 4: Add the `test:i18n` package script; mount the provider in `main.tsx`; add the keyboard-accessible `中文 / English` selector** beside the stock selector in `Shell`.
- [ ] **Step 5: Convert shared shell, page header, loading view, overview, and evaluation-workspace copy** in `App.tsx` to typed translation keys.
- [ ] **Step 6: Run locale checks and frontend build.**

Run: `cd frontend && npm run build`
Expected: TypeScript and Vite build succeed; browser check confirms first-use Chinese, immediate toggle, and persisted locale after reload.

Run: `cd frontend && npm run test:i18n`
Expected: PASS; both dictionaries have the same non-empty key set. Browser acceptance also verifies absent/invalid storage resolves to `zh-CN`.

### Task 3: Localize the remaining workspace screens and shared display values

**Files:**
- Modify: `frontend/src/V2ForecastWorkspace.tsx`
- Modify: `frontend/src/EvidenceCenter.tsx`
- Modify: `frontend/src/EvidenceProcessing.tsx`
- Modify: `frontend/src/MaterialLibrary.tsx`
- Modify: `frontend/src/MaterialAnalysisDetail.tsx`
- Modify: `frontend/src/HistoricalReplayWorkspace.tsx`
- Modify: `frontend/src/ResearchBriefView.tsx`
- Modify: `frontend/src/CandlestickChart.tsx`
- Modify: `frontend/src/i18n.tsx`

**Interfaces:** Consume `useLocale()` and the typed dictionaries/formatters from Task 2. Keep original source titles, quoted passages, tickers, codes, and URLs as data values.

- [ ] **Step 1: Add matching dictionary keys** for all remaining route copy, labels, API-status descriptions, errors, empty/loading states, retry controls, chart labels, dates, and accessibility text.
- [ ] **Step 2: Convert the listed screen components** to dictionary keys and shared locale formatters. Map known safe backend codes to localized messages; preserve unknown codes while displaying a localized generic explanation.
- [ ] **Step 3: Run the frontend build and route-level UI review** in both locales.

Run: `cd frontend && npm run build`
Expected: PASS; overview, forecast, evidence, materials, revisions, evaluation, and replay contain English UI in `en-US`, while source-authored titles and exact quotes remain unchanged.

### Task 4: Connect cached translations to material analyses and research briefs

**Files:**
- Create: `frontend/src/useAiContentLocalization.ts`
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/MaterialAnalysisDetail.tsx`
- Modify: `frontend/src/ResearchBriefView.tsx`
- Modify: `frontend/src/V2ForecastWorkspace.tsx`
- Modify: `frontend/src/EvidenceCenter.tsx`
- Test: `tests/test_ai_content_localization_api.py`

**Interfaces:**
- API clients `getMaterialAnalysisLocalization(analysisId, signal?)` and `getForecastBriefLocalization(versionId, signal?)` call the two POST routes from Task 1 and return typed `LocalizationResult` records.
- `useAiContentLocalization(contentKind, contentId, enabled)` returns `{fields, status, error, retry}` where status is `idle | loading | ready | failed`; `enabled` is true only for `en-US`. It ignores aborted/stale responses, keys client state by record ID and source hash returned by the API, and does not modify canonical props.

- [ ] **Step 1: Add frontend API/type coverage** for the localization response and route error mapping.
- [ ] **Step 2: Implement the hook** so it loads once per content hash and locale, cancels/ignores obsolete requests on route, record, or locale changes, and exposes retry after failure.
- [ ] **Step 3: Apply returned fields as display-only overlays** in saved material analyses and forecast/replay/evidence research briefs. Render translated loading/failure notices; on failure show original narrative with an explicit original-language label.
- [ ] **Step 4: Verify old records, unchanged quote strings, unchanged IDs, cache reuse, and graceful provider failure** using fake-provider API tests and one browser walkthrough.

Run: `.venv/bin/python -m pytest tests/test_ai_content_localization.py tests/test_ai_content_localization_api.py -q`
Expected: PASS; repeated same-record requests reuse the cache and no localization result changes canonical source/forecast payloads.

### Task 5: Complete end-to-end acceptance and update operating instructions

**Files:**
- Modify: `README.md`
- Modify: `docs/v3-acceptance.md`
- Test: existing localization tests from Tasks 1 and 4

- [ ] **Step 1: Run the relevant backend suite and frontend production build.**

Run: `.venv/bin/python -m pytest tests/test_ai_content_localization.py tests/test_ai_content_localization_api.py tests/test_ai_content_localization_migration.py -q`
Expected: PASS.

Run: `cd frontend && npm run build`
Expected: PASS.

- [ ] **Step 2: Apply the additive localization migration** using the repository script; verify `--check` reports no missing table and that the migration can be applied idempotently.
- [ ] **Step 3: Walk all seven screens in Chinese and English**, change language without reloading, refresh the page, and confirm the choice persists. Check browser console and responsive layout.
- [ ] **Step 4: Use an existing material-analysis version and a saved research brief in English mode.** Confirm one translation is stored per source hash, second visit is a cache hit, source titles/quotes remain exact, and prediction/version payloads are unchanged.
- [ ] **Step 5: Exercise provider failure with a fake provider** and confirm the English fallback notice, original-language label, and retry behavior.
- [ ] **Step 6: Document locale selection, lazy translation/cache behavior, and the one-time migration command** in README and acceptance notes.

## Self-review notes

- Spec coverage: selector and persistence are Task 2; all static screen text and formatting are Task 3; saved AI translations, cache and source-preservation requirements are Tasks 1 and 4; migration, failure paths, and seven-route acceptance are Tasks 1 and 5.
- Type consistency: the backend response and frontend `LocalizationResult` share content kind, record ID, locale, source hash, prompt version, cache-hit flag, and a field-path-to-string map.
- Review Focus items are assigned to localization-service, API/migration, and stale-response checks.
- No new frontend dependency is required; the locale catalog check uses Node's built-in test runner.
- Git commands currently fail with the host's unaccepted Xcode license message. Do not run `sudo xcodebuild -license`; preserve the current checkout and report commit status separately if still blocked during implementation.
