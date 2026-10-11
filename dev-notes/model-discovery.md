
## Stage 1: bounded provider model discovery

- Added `POST /agent-chat/models` with HTTPS base URL normalization shared with chat configuration, SecretStr API key input, 32 KB request-body middleware coverage, no-store/public proxy allowance, semaphore capacity, 20-second timeout, pinned public DNS transport, no redirects, and an 8 MB decompressed response cap. Error responses use stable codes and never include upstream response bodies.
- Normalization accepts OpenAI `data[]`, validates and deduplicates IDs, filters non-conversational types/modalities, uses positive timestamps when present, preserves upstream order otherwise, and slices only after filtering and sorting. MiniMax remains explicitly unsupported pending a verified list contract.
- Official references checked: [DeepSeek list models](https://api-docs.deepseek.com/api/list-models), [OpenRouter list models](https://openrouter.ai/docs/api/api-reference/models/get-models), [SiliconFlow docs](https://docs.siliconflow.cn/), [Moonshot docs](https://platform.moonshot.cn/docs), [MiniMax text OpenAI API](https://platform.minimax.cn/docs/api-reference/text-openai-api), [Qwen list models](https://help.aliyun.com/zh/model-studio/list-models). OpenRouter's official model-list endpoint returned a catalog without credentials when opened on 2026-10-11; an API key is still forwarded when the user has one.

## Stage 2: settings UI, request races, and compatibility corrections

- Kept the Model ID input and added a separate five-item provider list. Candidate selection only fills Model ID; provider changes clear the previous API key and list; base URL changes cancel and clear the list. Existing profiles fetch on open; newly entered valid keys trigger one debounced request; manual refresh remains available.
- Added generation checks in addition to AbortController cancellation so a late success or error cannot replace current results after a provider/key/URL change or modal close. Profile verification and save remain unchanged.
- Fixed backup merge validation to accept a parsed legacy profile with an empty key and null `tested_at`; it still requires a successful hello test before save/use. Replaced the settings mode placeholders with complete Chinese descriptions and appended paired locale entries.
- Verification: provider discovery mocks 13 passed; front-end `npm run test:i18n` 46 passed; `npm run build` passed; existing `test_local_model_profiles.py` 10 passed. Existing chat API tests passed 14 tests when isolated from the PostgreSQL fixture; 3 path/startup-dependent cases were deselected. Root pytest setup cannot connect to its disposable PostgreSQL port in this sandbox.

## Stage 3: verification

- Re-ran the isolated discovery suite after the UI and validation updates: 13 passed. The independent local-profile suite passed 10/10, and the existing chat API suite passed 14/14 runnable isolated cases with 3 path/startup-dependent cases deselected.
- Re-ran frontend checks: 46/46 `test:i18n` checks passed and `npm run build` exited 0. Vite emitted its existing style of bundle-size advisory because the minified JS chunk is about 500 kB; it did not fail the build.
- `git diff --check` passed. Scoped original snapshots are kept in `/private/tmp/model-discovery-task/originals`; no existing services were started or restarted.

## Stage 4: acceptance corrections (2026-10-11)

- Fixed model-list URL construction to use direct agent-chat transport, avoiding a configured API base being prefixed twice. The request helper behavior test asserts both `/api/agent-chat/models` for the relative proxy and exactly `http://127.0.0.1:8027/agent-chat/models` for a configured base.
- OpenRouter discovery now normalizes an absent/blank key to no key; the UI omits blank `api_key`, and the backend omits Authorization. Non-public providers reject blank or short keys. Route tests exercised absent and empty key requests using a local FastAPI TestClient without live credentials.
- Tightened model filtering around output capabilities and explicit non-chat types. Image/audio/video generation outputs are excluded from both top-level and architecture metadata, while vision inputs with text output remain eligible. Filters run before timestamp ordering/slicing. Mixed dated/undated output reports `provider_timestamp_desc_with_undated`; all-malformed rows fail as incompatible while valid empty/non-chat lists remain empty.
- The exact official SiliconFlow preset now uses its documented `sub_type=chat` query under pinned HTTPS/DNS transport. The narrow transport opt-in permits only this internally generated query; incoming base URLs still reject queries. Model discovery at the exact MiniMax preset remains unsupported, while a custom compatible base is attempted.
- Debounced key discovery now captures the key/URL context and has a testable scheduler. URL/provider changes, dialog close/cancel, test/save, and unmount invalidate pending timers; list selection and refresh controls are disabled while a test/save or chat mutation is active.
- Verification: isolated actual route tests 22 passed and shared main-middleware tests 1 passed; `npm run test:i18n` 48 passed; `npm run build` passed with the existing ~500 kB bundle advisory. This worker did not make a live official provider catalog request; no services were restarted and no browser success is claimed.

## Primary live-network acceptance, before completion

- Mocked regressions passed, but browser and actual pinned OpenRouter catalog request failed during TLS setup. Existing SNI extension used bytes; installed httpcore/anyio requires a hostname string. This blocks completion despite passing mocked tests. The focused correction continues with this precise transport compatibility issue; no paid model keys or production services were used.

## SNI extension correction (2026-10-11)

- Changed `PinnedPublicTransport` to pass the original hostname as a string in `request.extensions["sni_hostname"]`. HTTPX documents this extension as a string and demonstrates using it with a pinned IP URL, matching Host header, and certificate hostname verification: https://www.python-httpx.org/advanced/extensions/. Installed httpcore 1.0.9 passes the value directly to `start_tls(server_hostname=...)`; the old bytes value reached anyio's `idna2008_resolve()` and failed at `host.encode('ascii')`.
- Tightened the pinned transport regression to require a string SNI hostname and assert that the outbound URL uses the pinned public IP, while Host and SNI retain `research.example`; certificate verification remains enabled by the normal HTTPX transport. Redirects remain disabled and `trust_env=False` remains set at both discovery client and inner transport.
- The requested real `POST /agent-chat/models` validation used a minimal FastAPI app with the actual router and `PinnedPublicTransport`, OpenRouter, and no API key. It returned safe `502 model_discovery_network_error` because this execution environment cannot resolve `openrouter.ai` (`socket.gaierror: [Errno 8] nodename nor servname provided, or not known`). A direct pinned transport diagnostic confirmed failure occurs during DNS, before TLS or HTTP; therefore live catalog success is not verified here. No key, `.env`, or existing service was read or used.

## Final independent primary acceptance (2026-10-11)

- Primary acceptance after the SNI repair succeeded against the real OpenRouter catalog through the actual main application route: HTTP 200, no-store, five filtered timestamp-sorted conversational candidates. Browser acceptance also displayed the live list and filled Model ID on selection. This supersedes the isolated worker environment's DNS-limited verification status above.
- Fresh scoped backend regression: 97 passed. Frontend checks: 48 passed. TypeScript/Vite production build and diff whitespace check passed. All 1019 baseline locale pairs were retained.
- Synthetic browser fixtures confirmed key debounce, provider key reset, no request to an edited URL before explicit refresh, manual Model ID preservation, Hello/save/refresh restoration, and unsupported-directory fallback. No real paid key was used. OpenRouter with no key currently requires Refresh after a provider switch; documentation now states this precisely.
- Legacy empty-key unverified backup merge and full Chinese storage-mode descriptions passed independent verification. Final scope and evidence are in `docs/model-discovery-acceptance.md`; screenshot and command evidence are under `/tmp/market-model-discovery-20261011/`. The existing API and remote deployment were not changed or restarted.
