# Model profile storage and backup

## Local downloaded runs

The local FastAPI service can persist profiles to `data/model-profiles.json`, which is ignored by Git. It never reads or rewrites `.env`. Enable the feature only for a local installation with `LOCAL_MODEL_PROFILES_ENABLED=true`; optionally set `LOCAL_MODEL_PROFILES_PATH` to a fixed server-configured JSON path. The endpoint is `GET/PUT /agent-chat/local-model-profiles`; it is unavailable when `PUBLIC_API_MODE=true` and accepts only loopback clients, localhost/loopback Host and Origin authorities, and requests without proxy headers. The API does not accept a path from the browser. Writes use a restrictive mode-0600 temporary file and atomic replacement. Keep one local API process for this file; the lock is process-local.

## Local development startup

Run these two commands in separate terminals from the repository root. They use fake/no model credentials; add a real API key only in Model settings.

```sh
PUBLIC_API_MODE=false LOCAL_MODEL_PROFILES_ENABLED=true CORS_ALLOWED_ORIGINS=http://127.0.0.1:5173 .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

```sh
cd frontend && VITE_API_BASE_URL=http://127.0.0.1:8000 npm run dev -- --host 127.0.0.1 --port 5173
```

Optionally set `LOCAL_MODEL_PROFILES_PATH` on the backend command to choose a fixed server-side path. The configuration is stored as an ordinary JSON file (default `data/model-profiles.json`); export downloads a separate `market-evidence-model-profiles.json` backup that includes private API keys. The local backend file remains after browser site data is cleared. Public browser mode stores profiles in that browser, and clearing its site data removes them. Keep backup files private. Imported models are preserved but must be tested again before use. Nonempty local files take precedence; only an empty file is eligible for one-time browser-profile migration.

## Public website and backup

The public website continues to use browser OPFS/IndexedDB and never probes for shared server profiles. In Model settings, export creates `market-evidence-model-profiles.json`; this private file includes API keys. Keep it private and use only trusted devices. Import merges records, preserves existing entries, remaps duplicate IDs, and is limited to 20 profiles and 128 KiB. Import never tests or calls an endpoint. Imported models must be selected, tested with the Test and Save action, and saved before they are ready. Chat archives are separate and never contain model profiles or keys.

No model keys are included in source, `.env.example`, chat archives, or automated tests; automated fixtures use fake keys only.
