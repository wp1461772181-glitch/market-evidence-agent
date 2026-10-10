# Agent Chat and public site access

## Scope

The full-page Agent tab supports DeepSeek and OpenRouter chat with five read-only research tools: saved prices, the latest workspace forecast, uploaded research materials, saved SEC filings, and fixed research FAQ. A successful setup still requires a real `hello` completion with the selected provider, model ID, and user supplied key. The conversation uses POST fetch with server-sent events; assistant text is forwarded incrementally, and tool execution/results are shown in the transcript.

The API key stays in App-level React memory and is never stored with messages. Provider and model preferences may be remembered in local storage. A random browser chat credential and current conversation pointer use `sessionStorage`; the server stores only a hash of the credential, and message reads verify ownership. Refreshing clears the API key but restores the owned transcript. Messages and tool traces are stored server-side. Tool sources are treated as untrusted text, and tools cannot change research data or trigger external SEC fetches. Existing Hello and JSON chat routes remain available; research write routes keep their owner-key requirement.

## Public access boundary

With `PUBLIC_API_MODE=true`, GET and HEAD reads, OPTIONS, and `/health` are public. Only `POST /agent-chat/hello`, `POST /agent-chat/messages`, and `POST /agent-chat/stream` are additionally public; they use the API key in the request and do not use `APP_API_ACCESS_KEY`. Forecast creation, collection, upload, translation, review, and other write operations continue to require `X-App-Access-Key`. Keep `APP_API_ACCESS_KEY` strong (at least 32 characters) and configure exact `CORS_ALLOWED_ORIGINS` values without `*`.

## Deploy and use

1. Deploy the updated FastAPI app and frontend together, with `PUBLIC_API_MODE=true`, the existing strong `APP_API_ACCESS_KEY`, and exact allowed site origins configured.
2. Ensure outbound HTTPS access to `api.deepseek.com` and `openrouter.ai` from the API service. `httpx` is now a production dependency.
3. Open the public site and select **Agent 对话** at the end of the primary navigation. Open **后台设置**, choose DeepSeek or OpenRouter, enter the exact model ID and provider API key, then choose **测试并启用**.
4. Start chatting only after a successful hello response. If a request fails, correct the provider/model/key or retry after resolving the reported connectivity, access, quota, timeout, or response-format issue.

Deployment must not add a server-side provider key fallback. A real provider success still requires the user to configure a valid API key and an available model; the isolated implementation run did not use paid credentials.
