# Crypto Prices API

A working, production-style REST API that serves clean, paginated JSON over the
**CoinGecko Public API v3** — no API key, no login, no KYC, free tier.

## Why CoinGecko

- Public JSON, no auth of any kind; well-documented free-tier rate limits.
- Real market data (price, market cap, 24h change) — the kind of endpoint that
  is directly monetizable on RapidAPI later.
- Verified live from this VM on 2026-11-11 (see `TEST-LOG.md`).

## Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/health` | Liveness + cache stats |
| GET | `/v1/prices?ids=bitcoin,ethereum&vs_currency=usd` | Current prices for up to 50 coins |
| GET | `/v1/coins?limit=20&offset=0&vs_currency=usd` | Top coins by market cap, paginated |
| GET | `/v1/coins/{coin_id}` | Detail for one coin (e.g. `bitcoin`) |
| GET | `/docs` | Swagger UI (auto-generated) |
| GET | `/openapi.json` | Live OpenAPI spec |

## Schemas & validation (Pydantic v2, strict)

- `limit`: 1–100 (default 20); `offset`: ≥ 0. Out-of-range → **422**.
- `vs_currency`: 3 lowercase letters (`usd`, `eur`, `inr`). Anything else → **422**.
- `ids`: 1–50 comma-separated CoinGecko ids; empty/unknown → **400/404**.
- Every error is JSON: `{"detail": "...", "code": "NOT_FOUND", "status": 404}`.

## Caching (in-memory TTL)

- `/v1/prices`: 60 s · `/v1/coins` and `/v1/coins/{id}`: 120 s.
- Repeated calls inside the TTL return `"cached": true` and do **not** hit
  CoinGecko (verified in `TEST-LOG.md`: identical payload, `cached: false`
  then `cached: true`).
- `/health` reports live cache entries.

## No fake data

If CoinGecko is unreachable, rate-limited (429), or returns bad data, the API
returns **502/503 with a clear message** — never invented prices.

## How to run

```bash
export TMPDIR=~/workspace/micro-api-builder/tmp   # /tmp is a 512MB tmpfs
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
# Docs: http://127.0.0.1:8000/docs
```

```bash
curl "http://127.0.0.1:8000/v1/prices?ids=bitcoin,ethereum&vs_currency=usd"
curl "http://127.0.0.1:8000/v1/coins?limit=3&offset=0"
```

## Files

- `app/main.py` — FastAPI app (schemas, pagination, TTL cache, error handling)
- `app/__init__.py`
- `requirements.txt` — fastapi, uvicorn, httpx, pydantic, PyYAML
- `openapi.yaml` — full OpenAPI 3.1 spec + mock examples (from the real test run)
- `README.md` — this file
- `TEST-LOG.md` — the real local test run proving it works
- `tmp/` — venv scratch, server log, raw test outputs (not part of the API)

## VM quirk (documented in code)

httpx's `trust_env` proxy parsing chokes on the runtime's `NO_PROXY` value
(bracketed IPv6 entries like `[::1]` → `InvalidURL: Invalid port: ':1]'`).
`app/main.py` sanitises `NO_PROXY`/`no_proxy` to `localhost,127.0.0.1` at import
so httpx keeps working **and** still picks up the egress proxy + custom CA
bundle. (Explicit `proxy=` + `trust_env=False` was tried first — it fails TLS
with `CERTIFICATE_VERIFY_FAILED: self-signed certificate in certificate chain`.)

## Status

Built and verified locally 2026-11-11. **Not published, deployed, or
registered anywhere** — local files only, per the task.
