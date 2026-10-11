# Provider model discovery

Model settings now offers a provider model list beside the editable Model ID field. Selecting a candidate only fills Model ID. It does not change the profile display name, verify the model, save the profile, or make the profile active. The existing hello test and save step remains required.

The backend accepts `POST /agent-chat/models` with `provider`, optional `base_url`, and `api_key`. It uses the entered Base URL even for a preset provider, preserves the compatible endpoint path, and requests its `/models` route over HTTPS with pinned public DNS, no redirects, no environment proxy, a 20 second timeout, shared request capacity, and an 8 MB decompressed response cap. The key is sent as a bearer header and never returned in a response. Errors use safe codes and omit upstream response bodies. The route is covered by the existing agent-chat 32 KB body limit, public access allowance, and no-store response policy.

For pinned connections, the outgoing URL uses the selected public IP while the `Host` header and TLS certificate hostname use the original DNS hostname. The `sni_hostname` request extension is a string, as specified by [HTTPX's extension documentation](https://www.python-httpx.org/advanced/extensions/). Certificate verification stays enabled; redirects and environment proxy use are disabled.

The catalog accepts OpenAI-style `data[]` records, excludes known embedding, reranking, image/audio/video generation outputs, validates and deduplicates IDs, sorts by valid positive provider timestamps when present, otherwise keeps provider order, then returns at most five. Text-capable vision models remain eligible when metadata identifies text output. If some rows have valid times and others do not, dated rows lead and the UI says timestamps are missing for part of the list. A valid empty or non-chat-only list is distinct from a non-empty all-malformed response.

DeepSeek, Kimi, SiliconFlow, OpenRouter, and custom OpenAI-compatible endpoints use `/models`. For the exact official SiliconFlow preset base, the server appends `?sub_type=chat`; custom SiliconFlow bases and other endpoints are queried without that provider-specific parameter. Query parameters remain rejected in user-supplied Base URLs, and the narrowly enabled internal query keeps pinned public DNS, HTTPS, and redirect rejection. Other preset endpoints are attempted through the same contract; 404/405 are reported as unsupported. MiniMax is explicitly unsupported only at its exact preset endpoint; a compatible user override is attempted. The Qwen regional `/api/v1/models` contract is not substituted for the selected compatibility Base URL. A listed model only confirms catalog visibility; it does not confirm permission to invoke it.

OpenRouter accepts an absent or empty API key for its public catalog endpoint; an empty key is normalized away and no empty bearer header is sent. Other providers require a nonblank key between 8 and 2048 characters. The model-list UI uses the same direct URL construction as chat requests for both the relative proxy and a configured API origin. Pending key timers are canceled when provider/URL context changes, when the dialog closes, on test/save, and on unmount.

## Local UI walkthrough

For a local walkthrough, open two terminals at the repository root and stop both processes with Ctrl+C when finished. The commands use a separate API port to avoid the existing service on port 8000.

Terminal 1 starts the existing local API application with local profile storage enabled:

```sh
PUBLIC_API_MODE=false LOCAL_MODEL_PROFILES_ENABLED=true CORS_ALLOWED_ORIGINS=http://127.0.0.1:5173 .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8017
```

Terminal 2 starts Vite:

```sh
cd frontend && VITE_API_BASE_URL=http://127.0.0.1:8017 npm run dev -- --host 127.0.0.1 --port 5173
```

Open Model settings and add a profile. Choose OpenRouter and click Refresh to load its public model catalog without a key. Entering a key triggers debounced discovery; opening an existing key-bearing profile also loads candidates. Changing a Base URL invalidates pending requests and requires an explicit refresh or a new key edit before sending the retained key to the new URL. To exercise the safe authentication-error state without a real credential, choose DeepSeek and enter `synthetic-test-key-123`; the request should report an authentication error, retain the manual Model ID field, and not save a profile. Do not use that fixture value for hello verification.

## Verification record

- `PYTHONPATH=<repo> .venv/bin/python -m pytest -p no:cacheprovider -c /dev/null <isolated-copy-of-test_agent_model_discovery.py>`: 22 passed. This runs the actual FastAPI discovery route through its local TestClient and mock pinned transport, avoiding the repository test fixture's PostgreSQL connection.
- `PYTHONPATH=<repo> .venv/bin/python -m pytest -p no:cacheprovider -c /dev/null <isolated-copy-of-test_agent_chat_middleware.py>`: 1 passed. This exercises the same middleware registered by the main app without importing `app.main` (which loads `.env`).
- `PYTHONPATH=<repo> .venv/bin/pytest -q <isolated-copy-of-test_local_model_profiles.py>`: 10 passed.
- Isolated existing chat API tests: 14 passed, 3 deselected because those cases require the original repository test path or app startup fixture. Running the module under the repository fixture produced PostgreSQL connection errors before test bodies ran.
- `npm run test:i18n`: 48 passed, including relative/configured URL behavior and pending timer invalidation.
- `npm run build`: passed (`tsc -b` and Vite production build). Vite emitted a bundle-size advisory for the approximately 500 kB minified JS chunk.

The pinned transport regression asserts that a public IP is used in the outbound URL and the original hostname is retained as the string SNI value and Host header. The worker's isolated environment could not resolve OpenRouter. Subsequent independent primary acceptance succeeded through the actual main application route and browser with the live no-key catalog. The final regression totals are 97 backend tests and 48 frontend checks, with production build passing; see [final acceptance](model-discovery-acceptance.md). No provider credential or `.env` was read. See [DeepSeek list models](https://api-docs.deepseek.com/api/list-models), [OpenRouter list models](https://openrouter.ai/docs/api/api-reference/models/get-models), [SiliconFlow chat model-list docs](https://docs.siliconflow.cn/docs/api/models-get), [Moonshot docs](https://platform.moonshot.cn/docs), [MiniMax text OpenAI API](https://platform.minimax.cn/docs/api-reference/text-openai-api), and [Qwen list models](https://help.aliyun.com/zh/model-studio/list-models).
