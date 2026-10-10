# Agent Chat and public site access

## Scope

The full-page Agent tab supports DeepSeek and OpenRouter chat with five read-only research tools: saved prices, the latest workspace forecast, uploaded research materials, saved SEC filings, and fixed research FAQ. A successful setup still requires a real `hello` completion with the selected provider, model ID, and user supplied key. The conversation uses POST fetch with server-sent events; assistant text is forwarded incrementally, and tool execution/results are shown in the transcript.

The user may save up to 20 tested model profiles, including API keys, in this browser origin private file system (`model-profiles.json` via OPFS). If OPFS is unavailable, profiles use IndexedDB and the interface identifies that fallback. The file is private to this browser/site, is not a path in the project home, is not encrypted, and can be read by same-origin application code. Clearing site data removes it. Keys are sent to the application API only as part of requests to the selected provider and are never saved with server-side messages, conversations, or model instructions. The app does not store keys server-side. Legacy provider/model preferences can be imported without recovering a key; the user must enter a key and test again. A random browser chat credential and current conversation pointer use `sessionStorage`; the server stores only a hash of the credential, and message reads verify ownership. Refreshing clears the API key but restores the owned transcript. Messages and tool traces are stored server-side. Tool sources are treated as untrusted text, and tools cannot change research data or trigger external SEC fetches. Existing Hello and JSON chat routes remain available; research write routes keep their owner-key requirement.

The local demo forcibly routes Hello and both streamed provider calls to its labeled local mock, ignoring preset or custom endpoint values from the browser. A custom URL shown in the demo is a fixture field only and does not prove real provider compatibility.

## Public access boundary

With `PUBLIC_API_MODE=true`, GET and HEAD reads, OPTIONS, and `/health` are public. Only `POST /agent-chat/hello`, `POST /agent-chat/messages`, and `POST /agent-chat/stream` are additionally public; they use the API key in the request and do not use `APP_API_ACCESS_KEY`. Forecast creation, collection, upload, translation, review, and other write operations continue to require `X-App-Access-Key`. Keep `APP_API_ACCESS_KEY` strong (at least 32 characters) and configure exact `CORS_ALLOWED_ORIGINS` values without `*`.

## Deploy and use

1. Deploy the updated FastAPI app and frontend together, with `PUBLIC_API_MODE=true`, the existing strong `APP_API_ACCESS_KEY`, and exact allowed site origins configured.
2. Ensure outbound HTTPS access to `api.deepseek.com` and `openrouter.ai` from the API service. `httpx` is now a production dependency.
3. Open the public site and select **Agent 对话** at the end of the primary navigation. Open **后台设置**, choose a provider preset or Custom, review/edit the base URL and actual model ID, enter a display name and provider API key, then choose **Test and save**. The saved profile can be selected in the bottom-right chat picker. A test timestamp means the most recent successful test, not a live check on page reload.
4. Start chatting only after a successful hello response. If a request fails, correct the provider/model/key or retry after resolving the reported connectivity, access, quota, timeout, or response-format issue.

Deployment must not add a server-side provider key fallback. A real provider success still requires the user to configure a valid API key and an available model. User-supplied endpoints are HTTPS only, publicly routable, DNS-checked and pinned to a validated IP for the connection; redirects and proxy environment variables are disabled. Local/private model endpoints are unsupported. Provider presets are suggested compatible endpoints, not a guarantee of account access; Doubao requires the user’s own model/deployment ID.
