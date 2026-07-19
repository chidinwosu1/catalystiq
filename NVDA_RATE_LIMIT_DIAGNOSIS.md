# Diagnosis: "Failed to fetch fundamentals for NVDA: Too Many Requests. Rate limited"

Status: **diagnosis only — no behavior changed.** This document traces the error
to its exact source with code references before any fix is proposed.

---

## TL;DR (exact root cause)

The 429 is returned by **Yahoo Finance's `quoteSummary` endpoint**
(`https://query{1,2}.finance.yahoo.com/v10/finance/quoteSummary/NVDA`), reached
through the **`yfinance` package** inside `YahooFinanceProvider.get_fundamentals()`.
It is an **upstream provider rate limit**, not our API, not Render, and not our
own throttling.

The limit is being hit because **fundamentals are fetched far more often than
needed**, and the single biggest amplifier is the **opportunity scan**:
`GET /analysis/opportunity-scan` loops over a 24‑symbol universe (NVDA is #3)
and calls `provider.get_fundamentals()` **once per symbol, unconditionally, with
no caching** — one live Yahoo `.info` call each. Two separate frontend pages call
that scan on mount, React `StrictMode` double‑invokes those effects in dev, and
the Yahoo path deliberately **bypasses the shared token‑bucket rate limiter /
circuit breaker**. A single scan is ~24 uncontrolled `quoteSummary` hits from one
datacenter egress IP; a couple of page mounts turn that into ~50–100 in a few
seconds, which trips Yahoo's per‑IP throttle.

---

## 1. Exact endpoint and provider returning the 429

**Provider: Yahoo Finance, via `yfinance`.**

The user‑visible string is produced here:

`catalystiq/providers/market_data.py:130`
```python
def get_fundamentals(self, symbol: str) -> FundamentalsSnapshot:
    try:
        info = self._ticker(symbol).info          # <-- yfinance hits Yahoo here
    except Exception as exc:                       # pragma: no cover
        raise MarketDataError(
            f"Failed to fetch fundamentals for {symbol}: {exc}"   # line 134
        ) from exc
```

- The prefix `Failed to fetch fundamentals for NVDA:` is our wrapper text.
- The suffix `Too Many Requests. Rate limited` is `str(exc)` — the message
  `yfinance` raises (`YFRateLimitError("Too Many Requests. Rate limited …")`)
  when Yahoo answers `.info` (a `quoteSummary` request) with **HTTP 429**.

`.info` resolves to Yahoo's `quoteSummary` REST endpoint
(`query1/query2.finance.yahoo.com/v10/finance/quoteSummary/<symbol>`, plus a
crumb/cookie handshake in recent `yfinance` versions). These hosts are the ones
returning 429. (`yfinance` is **unpinned** in `requirements.txt`, so the exact
URL/crumb flow is version‑dependent, but the throttling host is Yahoo.)

**The HTTP status reaching our code is 502**, not 429: the router catches
`MarketDataError` and re‑wraps it:

`catalystiq/routers/market_data.py:158`
```python
@router.get("/fundamentals/{symbol}", response_model=FundamentalsSnapshot)
def get_fundamentals(symbol, provider=Depends(get_market_data_provider)):
    try:
        return provider.get_fundamentals(symbol)
    except MarketDataError as exc:
        raise HTTPException(status_code=502, detail=str(exc))   # 429 -> 502
```

So the browser sees `502` with `detail = "Failed to fetch fundamentals for NVDA:
Too Many Requests. Rate limited"`, surfaced by `frontend/src/lib/api.ts`
(`ApiError`).

### Not to be confused with the *other* `/fundamentals/{symbol}`
There are two routes with that suffix:

| Route | Router | Backend | Would produce this error? |
|---|---|---|---|
| `GET /market-data/fundamentals/{symbol}` | `routers/market_data.py` | **Yahoo / yfinance** | **Yes — this one** |
| `GET /fundamentals/{symbol}` | `routers/fundamentals.py` | SEC EDGAR (shared transport) | No — its errors read `sec_edgar: HTTP 429 …` |

The wording `Too Many Requests. Rate limited` is `yfinance`‑specific and comes
only from the Yahoo path. The SEC path formats rate‑limit errors differently
(`ProviderError` from `transport.py`), so it is ruled out.

---

## 2. Response status and rate‑limit / Retry‑After headers (captured *safely*)

**Important evidentiary gap:** on the Yahoo path we currently **cannot** capture
the 429 status or its `Retry-After` / `x-ratelimit-*` headers, because `yfinance`
owns the socket and only hands us a Python exception whose `str()` is the message.
By the time control reaches `market_data.py:134`, the raw `httpx`/`requests`
response (and its headers) has been consumed inside `yfinance`.

Contrast with the **shared transport**, which *does* scrape these safely and is
already the right template — it is simply not wired to the Yahoo adapter:

`catalystiq/providers/transport.py:203`
```python
def _extract_rate_limit(headers):
    out = {}
    for k, v in headers.items():
        lk = k.lower()
        if "ratelimit" in lk or "rate-limit" in lk or lk == "retry-after":
            out[lk] = v            # values only for rate metadata; never secrets
    return out
```
and it already honors `Retry-After` on 429 (`transport.py:356`, `_backoff` at
`:271`) and redacts secret‑bearing keys before logging (`redact()` at `:62`,
`_REDACT_KEYS` at `:34`).

**Safe capture recommendation (no secrets):** the only way to record Yahoo's 429
status + `Retry-After` is to observe the HTTP response *before* `yfinance`
swallows it — i.e. give `yfinance` a shared `requests`/`httpx` session we own and
log `response.status_code` plus the allow‑listed rate headers via the existing
`_extract_rate_limit` filter. That filter is header‑key allow‑listed, so it never
touches `Authorization`, cookies, or the Yahoo `crumb`. **No fix is applied here;
this is the safe path when we do fix it.**

---

## 3. How many fundamentals requests, and from which paths

Every `getFundamentals` / opportunity call is **one live Yahoo `.info` request** —
there is **no caching, memoization, TTL, dedup, or debounce anywhere** on this
path (`score_symbol` calls `get_fundamentals` unconditionally even when Silver
OHLCV is already fresh). Fan‑out by trigger:

### Backend amplifier — the scan (largest)
`catalystiq/analysis/opportunity_score.py`
- `scan_universe()` (`:411`) iterates `SCAN_UNIVERSE` — **24 symbols**, NVDA is #3
  (`:402`).
- For each symbol it calls `score_symbol()` (`:355`), which calls
  `provider.get_fundamentals(symbol)` at **`:382`** just to read `.sector` for the
  sector‑ETF lookup.
- ⇒ **one scan = 24 Yahoo `.info` calls**, sequential, uncontrolled, plus
  `ensure_fresh()` OHLCV calls for each symbol + SPY + the sector ETF.
- `GET /analysis/{symbol}/opportunity-score` does the same for a single symbol
  (1 `.info`).

### Frontend triggers (each mount fires the effect once; `alive`/`cancelled`
guards stop state writes but **not** the network request)
| Path | File | Fundamentals impact |
|---|---|---|
| `getOpportunityScan(4)` on mount | `pages/MarketIntelligencePage.tsx:71` | triggers a full 24‑symbol scan |
| `getOpportunityScan(4)` on mount | `pages/TradeCenterPage.tsx:122` | triggers a **second** full 24‑symbol scan |
| `getOpportunityScore(symbol)` | `components/RuleBasedOpportunityScore.tsx:77` | 1 `.info` per symbol view |
| `getFundamentals(symbol)` on `symbol` change | `pages/TradeTicketPage.tsx:214` | 1 `.info` per ticker submit/nav |
| `Promise.allSettled(positions.map(getFundamentals))` | `pages/PortfolioPage.tsx:68` | **parallel burst**, one `.info` per position |

### Multipliers stacking on top
- **`StrictMode`** (`frontend/src/main.tsx`) double‑invokes every effect in dev ⇒
  each scan/score/fundamentals fetch fires **twice**.
- **Two pages** both auto‑scan on mount ⇒ 2 × 24 = 48 `.info` in dev before any
  StrictMode doubling (→ up to ~96).
- **No shared HTTP rate limiter** on Yahoo (see §4) ⇒ nothing spaces these out.
- **`yfinance` internal retries** — recent versions retry 429s themselves, adding
  hidden requests underneath each of ours before surfacing the error.

For NVDA specifically: it is hit by *every* scan (universe member), by the trade
ticket if you look up NVDA, and by the portfolio page if NVDA is a holding — so
it is one of the most frequently requested symbols in the app.

---

## 4. Was the limit ours, the provider's, Render's, or our own throttling?

| Candidate source | Verdict | Evidence |
|---|---|---|
| **Upstream provider (Yahoo)** | **✅ This is it** | Message `Too Many Requests. Rate limited` is `yfinance`'s `YFRateLimitError` text for a Yahoo `quoteSummary` **429**; `market_data_provider` defaults to `"yahoo"` (`config.py:97`). |
| Our API (FastAPI) | ❌ Ruled out | No rate limiter on these routes; our layer only *re‑wraps* the upstream 429 as a 502. (`routers/auth.py:7` even notes login has no rate limiter — we don't emit 429s here.) |
| Render / shared egress | ⚠️ Aggravator, not the emitter | Render does not inject a 429 with this body. But the free plan runs a **single instance behind a shared datacenter egress IP**; Yahoo throttles **per IP** and treats datacenter IPs far more harshly than residential ones, so the same call volume trips the limit sooner in prod than locally. It worsens the problem; it does not *cause* the message. |
| **Our own throttling** | ❌ Ruled out (and part of the problem) | The token‑bucket `RateLimiter` + `CircuitBreaker` in `transport.py` are wired only to the REST adapters (SEC/FRED/etc.). `YahooFinanceProvider` uses `yfinance`'s own HTTP and **never touches `HttpTransport`** — confirmed by the module docstring at `transport.py:4‑8`. So Yahoo calls have **no** client‑side pacing at all. |

**Conclusion:** the 429 is emitted by **Yahoo Finance (upstream)**. Render's shared
datacenter IP makes it happen at lower volume in production, and the **absence** of
our own throttling on the Yahoo path is why the volume gets high enough to trip it.

---

## 5. Duplicate‑call check (what actually causes repeats)

| Suspected cause | Causes duplicate fundamentals calls? | Detail |
|---|---|---|
| **Ticker typing** (per keystroke) | **❌ No** | `TradeTicketPage` fetches in a `useEffect` keyed on **`symbol`**, not `symbolInput`. `symbol` only updates on **Enter** (`handleSymbolSubmit`, `:235`) or an `initialSymbol` prop change (`:150`). Keystrokes only update `symbolInput`; no request per character. |
| **React rerenders / `StrictMode`** | **✅ Yes (dev)** | `StrictMode` (`main.tsx`) double‑invokes mount effects ⇒ each scan/score/fundamentals fetch fires twice in dev. The `alive`/`cancelled` cleanup flags prevent stale **state updates** but do **not** abort the in‑flight `fetch` (no `AbortController`), so both requests still hit Yahoo. |
| **Background scans** | **✅ Yes — primary amplifier** | The opportunity scan fans one page mount into 24 Yahoo `.info` calls; two pages auto‑run it (`MarketIntelligencePage`, `TradeCenterPage`). Note: the in‑process **`scheduler_loop` is NOT involved** — it only flips scheduled orders to `due` and makes zero provider calls (`scheduler.py`). "Background" here means client‑initiated scans, not a server cron. |
| **Retries** | **✅ Yes (hidden)** | No retry in our Yahoo path or in `api.ts`, but `yfinance` itself retries 429s internally, multiplying request count beneath each of our calls. (The shared transport's controlled retry/backoff is *not* on this path.) |
| **Multiple components** | **✅ Yes** | Fundamentals are requested independently by `TradeTicketPage`, `PortfolioPage` (per‑position burst), `RuleBasedOpportunityScore`, and both scan pages, with no shared cache — the same symbol is refetched by each. |

---

## 6. Evidence summary (file:line)

- Error origin (Yahoo/yfinance): `catalystiq/providers/market_data.py:130‑134`
- 429→502 re‑wrap: `catalystiq/routers/market_data.py:158‑165`
- Default provider `"yahoo"`: `catalystiq/config.py:97`
- Yahoo bypasses shared limiter/breaker (by design): `catalystiq/providers/transport.py:4‑8`
- Safe header scrape template (unused on Yahoo): `transport.py:203‑209`, `Retry-After` at `:356`, secret redaction at `:34`/`:62`
- Unconditional per‑symbol `.info` in scoring: `catalystiq/analysis/opportunity_score.py:382`
- 24‑symbol scan loop, NVDA in universe: `opportunity_score.py:402`, `:411‑428`
- Scheduler makes no provider calls: `catalystiq/scheduler.py`
- Frontend scan-on-mount (×2 pages): `MarketIntelligencePage.tsx:71`, `TradeCenterPage.tsx:122`
- Per‑symbol score fetch: `RuleBasedOpportunityScore.tsx:77`
- Ticker fetch keyed on `symbol` (not typing): `TradeTicketPage.tsx:204‑233`, `:235`
- Portfolio parallel burst: `PortfolioPage.tsx:68`
- `StrictMode`: `frontend/src/main.tsx`
- `yfinance` unpinned: `requirements.txt`

---

## 7. Recommended fixes (NOT applied — for review first)

Ordered by leverage; none weakens a safeguard or exposes a secret.

1. **Stop the scan from calling `.info` per symbol.** `score_symbol()` fetches
   full fundamentals only to read `.sector`. Make the sector optional/cached (or
   derive it from data already held) so a 24‑symbol scan doesn't fan out into 24
   `quoteSummary` hits. Highest‑leverage single change.
2. **Cache fundamentals with a TTL** (fundamentals change slowly — hours/day is
   fine). Kills the repeat calls from multiple components, portfolio bursts, and
   re‑scans.
3. **Route the Yahoo adapter through a rate limiter** (own the `yfinance`
   session and gate it, or add a shared `RateLimiter` like the REST adapters
   already use) so bursts are paced under Yahoo's per‑IP ceiling.
4. **Capture 429 diagnostics safely**: log `status_code` + allow‑listed
   `Retry-After` / `x-ratelimit-*` via the existing `_extract_rate_limit` filter,
   and honor `Retry-After` before retrying. Never log cookies/crumb/`Authorization`.
5. **Frontend hygiene**: dedupe the two auto‑scans (shared cache/query), and add
   an `AbortController` so `StrictMode`/rapid navigation cancels the superseded
   request instead of leaving it in flight.
6. **Pin `yfinance`** so the endpoint/crumb behavior and its internal retry policy
   are reproducible.
7. Optionally, surface Yahoo 429s to the UI as a **429/"rate limited, retrying"**
   state rather than a generic 502, so it reads as transient.
