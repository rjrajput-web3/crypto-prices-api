# TEST-LOG — Crypto Prices API (local verification)

- **Date:** 2026-11-11, ~14:01 IST (08:31 UTC)
- **App:** FastAPI, `app/main.py`, served by uvicorn on `http://127.0.0.1:8000`
- **Upstream:** CoinGecko Public API v3 (`https://api.coingecko.com/api/v3`), no key
- **Method:** `curl` against the locally running server (per task constraints;
  Node fetch fails through the proxy). Raw outputs saved in `tmp/t*.json|txt`.
- **Result: 11/11 checks passed.** No fake data anywhere — every price below
  came from CoinGecko at request time.

## T1 — Health

```
$ curl -s http://127.0.0.1:8000/health
{"status":"ok","source":"CoinGecko Public API v3 (no key)","cache":{"entries":0,"ttl_prices_seconds":60,"ttl_markets_seconds":120,"ttl_coin_detail_seconds":120}}
```
PASS — server up, cache empty at start.

## T2 — Prices, first call (cache MISS)

```
$ curl -s "http://127.0.0.1:8000/v1/prices?ids=bitcoin,ethereum,solana&vs_currency=usd"
{"vs_currency":"usd","count":3,"prices":[
 {"id":"bitcoin","vs_currency":"usd","price":73758.0,"market_cap":1483319803345.0923,"change_24h_pct":-0.9988475628592792,"last_updated":"2026-11-11T08:31:41Z"},
 {"id":"ethereum","vs_currency":"usd","price":2057.57,"market_cap":378272778606.4982,"change_24h_pct":-2.427019018676282,"last_updated":"2026-11-11T08:31:41Z"},
 {"id":"solana","vs_currency":"usd","price":83.01,"market_cap":49091838172.99615,"change_24h_pct":-2.9657167173045575,"last_updated":"2026-11-11T08:31:41Z"}],
 "cached":false}
```
PASS — real upstream prices, `cached:false`.

## T3 — Prices, identical repeat call (cache HIT)

Same request again returned the **byte-identical payload** except
`"cached":true`. PASS — TTL cache works; no second upstream hit.

## T4 — Paginated list, page 1 (`limit=3&offset=0`)

```
$ curl -s "http://127.0.0.1:8000/v1/coins?limit=3&offset=0"
{"vs_currency":"usd","limit":3,"offset":0,"count":3,"total_fetched":3,"cached":false,
 "coins":[
  {"id":"bitcoin","symbol":"btc","name":"Bitcoin","current_price":73726.0,"market_cap":1482681720921.0,"market_cap_rank":1,"price_change_percentage_24h":-1.12036,"total_volume":33390079578.0,"last_updated":"2026-11-11T08:30:51.000Z"},
  {"id":"ethereum","symbol":"eth","name":"Ethereum","current_price":2056.53,"market_cap_rank":2,"price_change_percentage_24h":-2.54089,"total_volume":14326554545.0,"last_updated":"2026-11-11T08:30:51.000Z"},
  {"id":"tether","symbol":"usdt","name":"Tether","current_price":0.999039,"market_cap_rank":3,"price_change_percentage_24h":-0.00488,"total_volume":54765083008.0,"last_updated":"2026-11-11T08:30:51.000Z"}]}
```
PASS — ranks 1–3, `count` matches `limit`.

## T5 — Paginated list, page 2 (`limit=2&offset=3`)

```
$ curl -s "http://127.0.0.1:8000/v1/coins?limit=2&offset=3"
{"vs_currency":"usd","limit":2,"offset":3,"count":2,"total_fetched":5,"cached":false,
 "coins":[
  {"id":"binancecoin","symbol":"bnb","name":"BNB","current_price":680.34,"market_cap_rank":4,"price_change_percentage_24h":-1.33706,"total_volume":690757355.0,"last_updated":"2026-11-11T08:29:47.000Z"},
  {"id":"usd-coin","symbol":"usdc","name":"USDC","current_price":0.999461,"market_cap_rank":5,"price_change_percentage_24h":-0.00866,"total_volume":14596832665.0,"last_updated":"2026-11-11T08:29:47.000Z"}]}
```
PASS — pagination is exact: offset 3 continues at rank 4 (BNB), no overlap
with page 1.

## T6 — Coin detail (`/v1/coins/bitcoin`)

```
$ curl -s "http://127.0.0.1:8000/v1/coins/bitcoin"
{"id":"bitcoin","symbol":"btc","name":"Bitcoin",
 "description_en":"Bitcoin is the world's first decentralized cryptocurrency, created in 2009 by the pseudonymous Satoshi Nakamoto. ... (1,962 chars)",
 "homepage":["http://www.bitcoin.org"],"market_cap_rank":1,
 "current_price_usd":73726.0,"market_cap_usd":1482681720921.0,
 "price_change_percentage_24h":-1.12036,"last_updated":"2026-11-11T08:30:51.000Z","cached":false}
```
PASS — full detail object (description truncated here; full JSON in `tmp/t6_detail.json`).

## T7 — Unknown coin → 404 (not fake data)

```
$ curl -s -w "HTTP %{http_code}" "http://127.0.0.1:8000/v1/coins/not-a-real-coin-xyz"
{"detail":"Coin not found on CoinGecko.","code":"NOT_FOUND","status":404}
HTTP 404
```
PASS.

## T8 — Validation: `limit=500` → 422

```
$ curl -s -w "HTTP %{http_code}" "http://127.0.0.1:8000/v1/coins?limit=500"
{"detail":[{"type":"less_than_equal","loc":["query","limit"],"msg":"Input should be less than or equal to 100","input":"500","ctx":{"le":100}}]}
HTTP 422
```
PASS.

## T9 — Validation: bad `vs_currency=USDD` → 422

```
{"detail":[{"type":"string_pattern_mismatch","loc":["query","vs_currency"],"msg":"String should match pattern '^[a-z]{3}$'","input":"USDD","ctx":{"pattern":"^[a-z]{3}$"}}]}
HTTP 422
```
PASS.

## T10 — Health after the run (cache populated)

```
{"status":"ok","source":"CoinGecko Public API v3 (no key)","cache":{"entries":4,"ttl_prices_seconds":60,"ttl_markets_seconds":120,"ttl_coin_detail_seconds":120}}
```
PASS — 4 live cache entries (prices, 2 coin-list windows, bitcoin detail).

## T11 — Live OpenAPI spec

```
$ curl -s http://127.0.0.1:8000/openapi.json -o tmp/openapi.json
9689 bytes; openapi 3.1.0; title "Crypto Prices API"
paths: /health, /v1/coins, /v1/coins/{coin_id}, /v1/prices
```
PASS — spec served by the running app; converted to `openapi.yaml` with
mock examples taken verbatim from T2/T4/T7/T8 above.

## Bugs found & fixed during verification

1. `httpx` + runtime `NO_PROXY` (`[::1]` etc.) → `InvalidURL: Invalid port: ':1]'`
   → fixed by sanitising `NO_PROXY`/`no_proxy` at import in `app/main.py`.
2. Explicit `proxy=` + `trust_env=False` → TLS `CERTIFICATE_VERIFY_FAILED`
   (self-signed cert in chain) → reverted to default `trust_env=True`, which
   picks up the VM's custom CA bundle.
3. `last_updated` on `/v1/prices` was always `null` → fixed to convert the
   upstream `last_updated_at` epoch to ISO-8601 (verified in T2: `...08:31:41Z`).
4. Note: an intermediate server process was killed by a `pkill -f` that matched
   its own shell; replaced with a PID-from-`ss` kill script (`tmp/kill8000.py`).
   No test data was affected — the full suite above ran on the final build.
