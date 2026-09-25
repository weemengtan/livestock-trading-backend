# Deploying the test environment (Vercel + Railway)

Frontend → Vercel (repo `livestock-trade-frontend`), backend → Railway (repo `livestock-trading-backend`).
This is a **test** environment: resettable data, demo accounts, no real production data.

## 0. Decide the domain setup first (it decides whether login works)

The refresh cookie is `SameSite=Strict`. A browser only sends it when the frontend and API are the **same site**.

| Setup | Result |
|---|---|
| **Custom domain, subdomains** — `app.yourdomain.com` (Vercel) + `api.yourdomain.com` (Railway) | Works everywhere incl. iPhone Safari. **Recommended.** Keep `COOKIE_SAMESITE=strict`. |
| Default `*.vercel.app` + `*.up.railway.app` | Different sites → login works, but **every page reload logs the user out**. Workaround: `COOKIE_SAMESITE=none` + `COOKIE_SECURE=true` — works on Chrome/Firefox/Edge/Android, **breaks on Safari/iOS**. |

## 1. Railway (backend)

1. New project → **Deploy from GitHub repo** → `livestock-trading-backend`. `railway.json` supplies build, migrate-on-deploy (`alembic upgrade head`), start command, health check.
2. Add **PostgreSQL** and **Redis** to the project.
3. Backend service → Variables:

| Variable | Value |
|---|---|
| `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` (the `postgresql://` scheme is converted to asyncpg automatically) |
| `REDIS_URL` | `${{Redis.REDIS_URL}}` |
| `JWT_SECRET` | `python -c "import secrets; print(secrets.token_urlsafe(64))"` |
| `COOKIE_SECURE` | `true` |
| `COOKIE_SAMESITE` | `strict` (custom domain) or `none` (see §0) |
| `CORS_ALLOWED_ORIGINS` | exact frontend origin, e.g. `https://app.yourdomain.com` — no trailing slash, comma-separate several |
| `MFA_ENFORCEMENT_ENABLED` | `false` for testing (same as local) |
| `REFERENCE_DATA_FOUR_EYES_REQUIRED` | `false` for testing (so one tester can activate their own change) |
| `ENVIRONMENT` | leave unset (= `prod`). The wipe runs from your laptop, not the service — see §3. |

4. Settings → Networking → **Generate domain** (or attach `api.yourdomain.com`). Check `https://<api>/api/v1/health` → `{"status":"ok","db":true,"redis":true}`.

**Do not run `scripts/create_app_role.sql` on this environment.** It makes the app role unable to disable the audit trigger, which is exactly what the wipe needs. Use it when this becomes real production.

## 2. Vercel (frontend)

1. Import `livestock-trade-frontend`. Framework preset: Next.js (auto). Leave build/install commands default.
2. Environment variable `NEXT_PUBLIC_API_URL` = `https://<api origin>` (no trailing slash). It is inlined at **build** time and feeds the CSP — after changing it, **Redeploy**.
3. Add the resulting Vercel origin to Railway's `CORS_ALLOWED_ORIGINS` and redeploy the backend.
4. Optional (push notifications): generate keys with `uv run python -m scripts.generate_vapid_keys`; `VAPID_*` on Railway, `NEXT_PUBLIC_VAPID_PUBLIC_KEY` on Vercel. Blank is fine — WS + polling still work.

## 3. Reset/seed loop from your laptop

The scripts run **locally** against the Railway database through its public TCP proxy, via `scripts/cloud.sh`.

One-time setup:

1. Postgres service → Variables → copy `DATABASE_PUBLIC_URL`.
2. `cp .env.railway.example .env.railway` (git-ignored) and fill in `DATABASE_URL`, keep `ENVIRONMENT=dev`, set a `SEED_PASSWORD` (the default demo password is public and this DB is on the internet).
3. Bootstrap once:
   ```
   scripts/cloud.sh seed                                  # orgs + bobby/bing/buyer@example.com
   scripts/cloud.sh reference /path/to/bootstrap.json     # reference data (business-confidential file, stays local)
   scripts/cloud.sh market                                # demo market observations
   ```

Every test round:

```
scripts/cloud.sh reset      # wipe_business_data (asks you to type 'yes') + seed_market_intel
```

The wipe keeps organisations, users, sessions, reference data and registries — so accounts and reference data survive resets and only `wipe` + `market` are needed repeatedly. It prints the target host before asking; check it says the Railway proxy host, not `localhost`.

Local scripts still work as before (`uv run python -m scripts...` uses `.env`); `cloud.sh` is the only thing that reads `.env.railway`.

## Gotchas

- **Stale browsers after a wipe.** The buyer PWA keeps an offline queue in IndexedDB. Testers' browsers may hold entries from before the wipe and sync them afterwards. Have them clear site data (or use a fresh profile) after a reset.
- **Rate limits** key on the first `X-Forwarded-For` value, which a client can set. Fine for testing; revisit before production.
- **Preview deployments** on Vercel get different origins that CORS will reject; only the production URL is allowed unless you add the others.
- The Postgres proxy is publicly reachable (password-protected). Remove the TCP proxy when you're not resetting if that concerns you.
