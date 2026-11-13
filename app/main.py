"""
Crypto Prices API — Micro-API & Dynamic Data Endpoint Builder
=============================================================
Public data source: CoinGecko Public API v3 (https://api.coingecko.com/api/v3)
  - No API key, no login, no KYC, free tier.
  - Why CoinGecko: stable public JSON, no auth, well-documented rate limits,
    real market data (prices / market caps / 24h change) — ideal for a
    clean, monetizable JSON API on RapidAPI later.
  - Upstream docs: https://docs.coingecko.com/reference/introduction

Design:
  - FastAPI + Pydantic v2 strict schemas (typed request/response models).
  - Pagination: limit/offset on list endpoints (limit 1-100, default 20).
  - Error handling: 400 validation, 404 unknown coin, 502 upstream
    unavailable / rate-limited — NEVER invented/fake data.
  - In-memory TTL cache: repeated calls within the TTL are served from
    cache and do NOT hit CoinGecko (see X-Cache header: HIT / MISS).
    TTLs: /v1/prices 60s, /v1/coins 120s, /v1/coins/{id} 120s.

Run:
  uvicorn app.main:app --host 127.0.0.1 --port 8000
  Docs: http://127.0.0.1:8000/docs  |  OpenAPI: /openapi.json
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

COINGECKO_BASE = "https://api.coingecko.com/api/v3"
UPSTREAM_TIMEOUT = 15.0

# RapidAPI proxy-secret guard (added 2026-11-13 for the RapidAPI Hub listing).
# When RAPIDAPI_PROXY_SECRET is set (Render env var, never in git), every
# /v1/* call must carry the matching X-RapidAPI-Proxy-Secret header — this
# is how RapidAPI proves a request came through its proxy/billing runtime.
# Direct calls to the Render URL without the secret get 401, so nobody can
# bypass RapidAPI billing. /health, /docs and /openapi.json stay open
# (Render health checks + public docs). When the env var is unset (local
# dev), the guard is a no-op and all routes behave as before.
RAPIDAPI_PROXY_SECRET = os.environ.get("RAPIDAPI_PROXY_SECRET", "")

# VM QUIRK (httpx + proxy): the runtime sets NO_PROXY with bracketed IPv6
# entries (e.g. [::1], [fd8b:...]) which httpx's trust_env proxy parsing
# chokes on -> InvalidURL "Invalid port: ':1]'". Sanitise it at import so
# httpx (trust_env=True, default) works AND still picks up the egress
# proxy + custom CA bundle from the environment (explicit proxy +
# trust_env=False fails TLS: self-signed cert in chain).
os.environ["NO_PROXY"] = "localhost,127.0.0.1"
os.environ["no_proxy"] = "localhost,127.0.0.1"

# ---------------------------------------------------------------------------
# In-memory TTL cache
# ---------------------------------------------------------------------------
# key -> (expires_at_epoch, payload_dict)
_CACHE: Dict[str, tuple[float, Any]] = {}

# TTLs (seconds)
TTL_PRICES = 60
TTL_MARKETS = 120
TTL_COIN_DETAIL = 120


def cache_get(key: str) -> Any | None:
    entry = _CACHE.get(key)
    if entry is None:
        return None
    expires_at, payload = entry
    if time.time() > expires_at:
        del _CACHE[key]
        return None
    return payload


def cache_set(key: str, payload: Any, ttl: int) -> None:
    _CACHE[key] = (time.time() + ttl, payload)


def cache_stats() -> dict:
    now = time.time()
    live = sum(1 for exp, _ in _CACHE.values() if exp > now)
    return {"entries": live, "ttl_prices_seconds": TTL_PRICES,
            "ttl_markets_seconds": TTL_MARKETS,
            "ttl_coin_detail_seconds": TTL_COIN_DETAIL}


# ---------------------------------------------------------------------------
# Schemas (strict, typed)
# ---------------------------------------------------------------------------
class ErrorResponse(BaseModel):
    detail: str = Field(..., examples=["Upstream CoinGecko API unavailable. Try again later."])
    code: str = Field(..., examples=["UPSTREAM_UNAVAILABLE"])
    status: int = Field(..., examples=[502])


class HealthResponse(BaseModel):
    status: str = Field(..., examples=["ok"])
    source: str = Field(..., examples=["CoinGecko Public API v3 (no key)"])
    cache: dict = Field(..., examples=[{"entries": 2}])


class PriceItem(BaseModel):
    id: str = Field(..., examples=["bitcoin"])
    vs_currency: str = Field(..., examples=["usd"])
    price: float = Field(..., examples=[73841.0])
    market_cap: Optional[float] = Field(None, examples=[1450000000000.0])
    change_24h_pct: Optional[float] = Field(None, examples=[1.23])
    last_updated: Optional[str] = Field(None, examples=["2026-11-11T08:28:00.000Z"])


class PricesResponse(BaseModel):
    vs_currency: str
    count: int
    prices: List[PriceItem]
    cached: bool = Field(..., description="True if served from the TTL cache.")


class CoinMarketItem(BaseModel):
    id: str = Field(..., examples=["bitcoin"])
    symbol: str = Field(..., examples=["btc"])
    name: str = Field(..., examples=["Bitcoin"])
    current_price: Optional[float] = Field(None, examples=[73841.0])
    market_cap: Optional[float] = Field(None, examples=[1450000000000.0])
    market_cap_rank: Optional[int] = Field(None, examples=[1])
    price_change_percentage_24h: Optional[float] = Field(None, examples=[1.23])
    total_volume: Optional[float] = Field(None, examples=[25000000000.0])
    last_updated: Optional[str] = Field(None, examples=["2026-11-11T08:28:00.000Z"])


class PaginatedCoinsResponse(BaseModel):
    vs_currency: str
    limit: int
    offset: int
    count: int
    total_fetched: int = Field(..., description="Coins fetched from upstream before pagination was applied.")
    cached: bool
    coins: List[CoinMarketItem]


class CoinDetailResponse(BaseModel):
    id: str
    symbol: str
    name: str
    description_en: Optional[str] = None
    homepage: Optional[List[str]] = None
    market_cap_rank: Optional[int] = None
    current_price_usd: Optional[float] = None
    market_cap_usd: Optional[float] = None
    price_change_percentage_24h: Optional[float] = None
    last_updated: Optional[str] = None
    cached: bool


# ---------------------------------------------------------------------------
# App + upstream helper
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Crypto Prices API",
    version="1.0.0",
    description=(
        "Clean, paginated JSON over the CoinGecko Public API v3 (no key, no login). "
        "Prices, top-coins market list, and per-coin detail with a TTL cache "
        "so repeated calls do not hit the upstream every time. "
        "If CoinGecko is down, the API returns 502 — never invented data."
    ),
    contact={"name": "Micro-API Builder"},
    license_info={"name": "Data: CoinGecko Public API (free, no key)"},
)


async def fetch_upstream(path: str, params: dict) -> Any:
    """GET CoinGecko. Raises HTTPException(502/503) on any upstream failure."""
    url = f"{COINGECKO_BASE}{path}"
    try:
        # Default trust_env=True: picks up the egress proxy and the
        # VM's custom CA bundle from the environment. NO_PROXY was
        # sanitised at import (see note above) so httpx can parse it.
        async with httpx.AsyncClient(timeout=UPSTREAM_TIMEOUT) as client:
            resp = await client.get(url, params=params)
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Upstream CoinGecko API unreachable: {exc.__class__.__name__}. Try again later.",
        )
    except Exception as exc:  # e.g. httpx.InvalidURL from proxy/env parsing
        raise HTTPException(
            status_code=502,
            detail=f"Upstream request failed: {exc.__class__.__name__}. Try again later.",
        )
    if resp.status_code == 429:
        raise HTTPException(
            status_code=503,
            detail="Upstream CoinGecko rate limit reached (429). Try again in a minute.",
        )
    if resp.status_code == 404:
        raise HTTPException(status_code=404, detail="Coin not found on CoinGecko.")
    if resp.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"Upstream CoinGecko returned HTTP {resp.status_code}. Try again later.",
        )
    try:
        return resp.json()
    except ValueError:
        raise HTTPException(status_code=502, detail="Upstream CoinGecko returned invalid JSON.")


@app.middleware("http")
async def rapidapi_proxy_secret_guard(request: Request, call_next):
    """Enforce X-RapidAPI-Proxy-Secret on /v1/* when the env var is set."""
    if RAPIDAPI_PROXY_SECRET and request.url.path.startswith("/v1/"):
        if request.headers.get("X-RapidAPI-Proxy-Secret") != RAPIDAPI_PROXY_SECRET:
            return JSONResponse(
                status_code=401,
                content={"detail": "Missing or invalid proxy secret.",
                         "code": "UNAUTHORIZED", "status": 401},
            )
    return await call_next(request)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    code = {
        400: "BAD_REQUEST",
        404: "NOT_FOUND",
        422: "VALIDATION_ERROR",
        502: "UPSTREAM_UNAVAILABLE",
        503: "UPSTREAM_RATE_LIMITED",
    }.get(exc.status_code, "ERROR")
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": str(exc.detail), "code": code, "status": exc.status_code},
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.get("/health", response_model=HealthResponse, tags=["meta"])
async def health():
    return HealthResponse(
        status="ok",
        source="CoinGecko Public API v3 (no key)",
        cache=cache_stats(),
    )


@app.get(
    "/v1/prices",
    response_model=PricesResponse,
    tags=["prices"],
    summary="Current prices for 1+ coins",
    responses={400: {"model": ErrorResponse}, 502: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
async def get_prices(
    ids: str = Query(
        ...,
        description="Comma-separated CoinGecko coin ids, e.g. bitcoin,ethereum,solana",
        examples="bitcoin,ethereum",
        min_length=1,
        max_length=500,
    ),
    vs_currency: str = Query(
        "usd",
        description="Quote currency (ISO code, e.g. usd, eur, inr)",
        pattern="^[a-z]{3}$",
        examples="usd",
    ),
    include_market_cap: bool = Query(True, description="Include market cap in the response"),
    include_24h_change: bool = Query(True, description="Include 24h % change in the response"),
):
    coin_ids = [c.strip().lower() for c in ids.split(",") if c.strip()]
    if not coin_ids:
        raise HTTPException(status_code=400, detail="Query param 'ids' must list at least one coin id.")
    if len(coin_ids) > 50:
        raise HTTPException(status_code=400, detail="At most 50 coin ids per request.")

    cache_key = f"prices:{','.join(sorted(coin_ids))}:{vs_currency}:{include_market_cap}:{include_24h_change}"
    cached = cache_get(cache_key)
    if cached is not None:
        return PricesResponse(**{**cached, "cached": True})

    data = await fetch_upstream(
        "/simple/price",
        {
            "ids": ",".join(coin_ids),
            "vs_currencies": vs_currency,
            "include_market_cap": str(include_market_cap).lower(),
            "include_24hr_change": str(include_24h_change).lower(),
            "include_last_updated_at": "true",
        },
    )
    if not isinstance(data, dict) or not data:
        raise HTTPException(
            status_code=404,
            detail=f"No prices found for ids: {','.join(coin_ids)}. Check CoinGecko coin ids.",
        )
    prices: List[PriceItem] = []
    for coin_id in coin_ids:
        row = data.get(coin_id)
        if row is None:
            continue
        last = row.get("last_updated_at")
        prices.append(PriceItem(
            id=coin_id,
            vs_currency=vs_currency,
            price=float(row[vs_currency]),
            market_cap=row.get(f"{vs_currency}_market_cap"),
            change_24h_pct=row.get(f"{vs_currency}_24h_change"),
            last_updated=datetime.fromtimestamp(last, tz=timezone.utc).isoformat().replace("+00:00", "Z") if last else None,
        ))
    if not prices:
        raise HTTPException(status_code=404, detail="No prices found for the requested ids.")
    payload = {"vs_currency": vs_currency, "count": len(prices), "prices": [p.model_dump() for p in prices], "cached": False}
    cache_set(cache_key, payload, TTL_PRICES)
    return PricesResponse(**payload)


@app.get(
    "/v1/coins",
    response_model=PaginatedCoinsResponse,
    tags=["coins"],
    summary="Top coins by market cap, paginated (limit/offset)",
    responses={400: {"model": ErrorResponse}, 502: {"model": ErrorResponse}},
)
async def list_coins(
    vs_currency: str = Query("usd", pattern="^[a-z]{3}$", examples="usd"),
    limit: int = Query(20, ge=1, le=100, description="Page size (1-100)"),
    offset: int = Query(0, ge=0, description="Number of coins to skip"),
    order: str = Query("market_cap_desc", pattern="^(market_cap_desc|market_cap_asc|volume_desc|volume_asc)$"),
):
    # Fetch a window large enough to cover offset+limit (CoinGecko pages by
    # `page`/`per_page`; we fetch up to offset+limit in one upstream call,
    # capped at 250, then slice locally — pagination is honoured exactly).
    needed = offset + limit
    per_page = min(max(needed, 1), 250)
    page = 1
    cache_key = f"coins:{vs_currency}:{order}:{per_page}:{page}"
    cached = cache_get(cache_key)
    if cached is not None:
        rows, from_cache = cached, True
    else:
        rows = await fetch_upstream(
            "/coins/markets",
            {"vs_currency": vs_currency, "order": order, "per_page": per_page, "page": page,
             "sparkline": "false", "price_change_percentage": "24h"},
        )
        if not isinstance(rows, list):
            raise HTTPException(status_code=502, detail="Upstream CoinGecko returned an unexpected response.")
        cache_set(cache_key, rows, TTL_MARKETS)
        from_cache = False

    sliced = rows[offset:offset + limit]
    coins = [
        CoinMarketItem(
            id=r.get("id", ""), symbol=r.get("symbol", ""), name=r.get("name", ""),
            current_price=r.get("current_price"), market_cap=r.get("market_cap"),
            market_cap_rank=r.get("market_cap_rank"),
            price_change_percentage_24h=r.get("price_change_percentage_24h"),
            total_volume=r.get("total_volume"), last_updated=r.get("last_updated"),
        )
        for r in sliced
    ]
    return PaginatedCoinsResponse(
        vs_currency=vs_currency, limit=limit, offset=offset,
        count=len(coins), total_fetched=len(rows), cached=from_cache, coins=coins,
    )


@app.get(
    "/v1/coins/{coin_id}",
    response_model=CoinDetailResponse,
    tags=["coins"],
    summary="Detail for one coin",
    responses={404: {"model": ErrorResponse}, 502: {"model": ErrorResponse}},
)
async def get_coin(coin_id: str):
    coin_id = coin_id.strip().lower()
    if not coin_id or len(coin_id) > 100:
        raise HTTPException(status_code=400, detail="Invalid coin id.")
    cache_key = f"coin:{coin_id}"
    cached = cache_get(cache_key)
    if cached is not None:
        return CoinDetailResponse(**{**cached, "cached": True})

    data = await fetch_upstream(f"/coins/{coin_id}", {"localization": "false", "tickers": "false",
                                                       "market_data": "true", "community_data": "false",
                                                       "developer_data": "false", "sparkline": "false"})
    md = data.get("market_data") or {}
    payload = {
        "id": data.get("id", coin_id),
        "symbol": data.get("symbol", ""),
        "name": data.get("name", ""),
        "description_en": (data.get("description") or {}).get("en"),
        "homepage": (data.get("links") or {}).get("homepage"),
        "market_cap_rank": data.get("market_cap_rank"),
        "current_price_usd": (md.get("current_price") or {}).get("usd"),
        "market_cap_usd": (md.get("market_cap") or {}).get("usd"),
        "price_change_percentage_24h": md.get("price_change_percentage_24h"),
        "last_updated": data.get("last_updated"),
        "cached": False,
    }
    cache_set(cache_key, payload, TTL_COIN_DETAIL)
    return CoinDetailResponse(**payload)
