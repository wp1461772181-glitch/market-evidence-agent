# Agent Chat and public site access

## Scope

The full-page Agent tab provides ordinary model conversation through DeepSeek or OpenRouter. It does not retrieve project materials, forecasts, or database records and has no tool calling. Provider, model, and API key setup lives on a separate Admin settings page. A successful setup requires a real `hello` chat completion with the selected provider, model ID, and user supplied key; a non-empty assistant answer is required before chat is enabled.

The API key stays in the App-level React session provider memory and is not persisted or returned. Provider and model preferences may be remembered in browser local storage. Refreshing the page clears the key and its verified status and requires entering it again. Moving between Agent, Admin settings, and research pages preserves the current mounted page session, including conversation and draft. Clearing a conversation keeps the successfully tested model configuration available; changing the provider, model, or key invalidates verification and clears the current conversation. Requests use fixed HTTPS provider endpoints, bounded messages, short completion limits, and a 25 second timeout. Canceling stops the browser wait; it may not stop provider processing or billing.

## Public access boundary

With `PUBLIC_API_MODE=true`, GET and HEAD reads, OPTIONS, and `/health` are public. Only `POST /agent-chat/hello` and `POST /agent-chat/messages` are additionally public; they use the API key in the request and do not use `APP_API_ACCESS_KEY`. Forecast creation, collection, upload, translation, review, and other write operations continue to require `X-App-Access-Key`. Keep `APP_API_ACCESS_KEY` strong (at least 32 characters) and configure exact `CORS_ALLOWED_ORIGINS` values without `*`.

## Deploy and use

1. Deploy the updated FastAPI app and frontend together, with `PUBLIC_API_MODE=true`, the existing strong `APP_API_ACCESS_KEY`, and exact allowed site origins configured.
2. Ensure outbound HTTPS access to `api.deepseek.com` and `openrouter.ai` from the API service. `httpx` is now a production dependency.
3. Open the public site and select **Agent 对话** at the end of the primary navigation. Open **后台设置**, choose DeepSeek or OpenRouter, enter the exact model ID and provider API key, then choose **测试并启用**.
4. Start chatting only after a successful hello response. If a request fails, correct the provider/model/key or retry after resolving the reported connectivity, access, quota, timeout, or response-format issue.

Deployment must not add a server-side provider key fallback. A real provider success still requires the user to configure a valid API key and an available model; this has not been verified with a real credential.
