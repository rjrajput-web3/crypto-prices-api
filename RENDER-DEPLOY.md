# Deploy on Render (free tier) — for the RapidAPI Hub listing

## One-time setup (browser, Render dashboard)

1. Render → New → Blueprint → connect repo `rjrajput-web3/crypto-prices-api` → Apply.
   (Or New → Web Service → same repo, build `pip install -r requirements.txt`,
   start `uvicorn app.main:app --host 0.0.0.0 --port $PORT`.)
2. Note the live URL — expected `https://crypto-prices-api.onrender.com`.
   If Render renames the service, use the URL the dashboard shows.
3. In the service → Environment, add `RAPIDAPI_PROXY_SECRET` = a long random
   string you generate (never commit it). Copy that SAME value into RapidAPI
   Studio as the API's proxy secret — RapidAPI sends it as the
   `X-RapidAPI-Proxy-Secret` header on every proxied call, and the API
   rejects `/v1/*` calls without it (401). `/health`, `/docs`, `/openapi.json`
   stay open for health checks and public docs.
4. Update `openapi.yaml` `servers:` to the live URL and re-check:
   `GET /health` → 200; `GET /v1/prices?ids=bitcoin` without the header → 401;
   with the header → 200 with a real price.

## Honest limits (say so in the RapidAPI listing)

- Free tier sleeps after 15 min idle; first request after idle takes ~1 min to wake.
- No database — in-memory TTL cache only (60s prices / 120s lists).
- Upstream is CoinGecko Public API v3 (no key); if CoinGecko is down or
  rate-limited the API returns honest 502/503, never invented prices.
