# GitHub Pages frontend with a local backend

This is a personal/demo deployment: GitHub Pages serves the React frontend;
FastAPI, PostgreSQL, the forecast worker, and provider credentials remain on the
owner's Mac. The browser reaches FastAPI through Tailscale Funnel. The free
Tailscale Personal plan is for non-commercial personal use; see the [plan
details](https://tailscale.com/pricing).

## Current readiness

- GitHub repository: `https://github.com/wp1461772181-glitch/market-evidence-agent` (public).
- Expected project page: `https://wp1461772181-glitch.github.io/market-evidence-agent/`.
- Browser origin for CORS: `https://wp1461772181-glitch.github.io` (no repository path).
- The Pages site and `VITE_API_BASE_URL` Actions variable are not configured yet.
- The deployment workflow is manual and refuses to build without the public API origin.
- The local PostgreSQL data is stored in a Docker-managed volume. Its host port must
  be bound to `127.0.0.1`; never expose port `55432` through a tunnel.

## Security boundary

GitHub Pages is public. The browser asks for one shared app access key and stores
it only in the current tab's session storage. FastAPI checks the key on every
route except `/health`, compares it in constant time, and sends `no-store`
responses. This is suitable for a private personal demo where the owner controls
the key; it is not multi-user authentication and the key should not be shared as
a public password. CORS only limits browser origins; the access key is the API
gate.

Keep DeepSeek/OpenRouter keys and database credentials in the ignored local
`.env`. Never place them in `VITE_*`, GitHub Pages files, or the frontend bundle.
Do not replace the existing `.env`; add these settings alongside its current
database/provider settings:

```dotenv
PUBLIC_API_MODE=true
APP_API_ACCESS_KEY=<a locally generated random value of at least 32 characters>
CORS_ALLOWED_ORIGINS=https://wp1461772181-glitch.github.io
```

Generate the access key locally with `./.venv/bin/python -c 'import secrets;
print(secrets.token_urlsafe(32))'`. Keep that terminal output private. FastAPI
refuses to start in public mode if the key is too short or the exact CORS origin
is missing or wildcarded.

## Local services

PostgreSQL must be reachable by the Mac at `127.0.0.1:55432`. The checked-in
database setup binds this port to loopback. If an existing container publishes
the port on all network interfaces, inspect it first and rebind it before
exposing FastAPI; `scripts/rebind_postgres_localhost.py` defaults to a dry run,
reuses the existing Docker-managed data volume, keeps the old container stopped
for rollback, and never removes a volume. Apply only when no forecast or material
job is processing:

```bash
./.venv/bin/python scripts/rebind_postgres_localhost.py
./.venv/bin/python scripts/rebind_postgres_localhost.py --apply
```

Start FastAPI in a terminal. It binds to loopback only and loads the ignored
`.env` on startup:

```bash
./.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Confirm `http://127.0.0.1:8000/health` returns `{"status":"ok"}`. Keep the
forecast worker active as well; the installed macOS LaunchAgent uses the
existing database container and processes queued forecast/material jobs. Do
not expose PostgreSQL itself.

## Tailscale Funnel

Install the standalone/system-extension build of Tailscale for macOS, sign in,
and enable Funnel for the Mac in the Tailscale admin console. The Mac App Store
variant cannot run Funnel on macOS; see Tailscale's [Funnel requirements and
setup](https://tailscale.com/docs/features/tailscale-funnel).

With FastAPI running, expose only its local port:

```bash
tailscale funnel --bg 8000
tailscale funnel status
```

The status output contains a stable HTTPS URL ending in `.ts.net`. Funnel makes
that URL public; it does not make the database public. Keep the Mac awake,
online, and running the API, worker, and Tailscale client. If the Mac sleeps or
restarts, the GitHub Pages shell remains visible but data/API actions stop until
the local services return.

## GitHub Pages release

1. Push the reviewed source changes, including
   `.github/workflows/deploy-pages.yml`, to the repository's `main` branch.
2. In repository **Settings → Pages**, choose **GitHub Actions** as the build
   and deployment source.
3. In **Settings → Secrets and variables → Actions → Variables**, add
   `VITE_API_BASE_URL` with the Funnel HTTPS origin only, for example
   `https://<machine>.<tailnet>.ts.net` (no path; trailing slash is optional).
4. Run **Actions → Deploy frontend to GitHub Pages → Run workflow**.
5. Open `https://wp1461772181-glitch.github.io/market-evidence-agent/`, enter
   the local app access key, and confirm the dashboard loads from FastAPI.

The workflow sets the correct `/<repository-name>/` base path, builds with
`npm ci` and `npm run build`, and deploys only when the API origin variable is
present. It does not deploy automatically on every push. Do not add backend
provider credentials as GitHub variables or secrets; the built frontend does
not need them.

## Remaining boundary

The GitHub repository currently has uncommitted application changes. Review and
publish the intended release as one coherent change before enabling Pages. The
public demo is only available while the owner's Mac and local services are
online; an always-on multi-user service needs an authenticated hosted backend
and database instead.
