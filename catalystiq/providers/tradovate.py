"""Tradovate provider: futures contract & product reference data.

Tradovate is a futures brokerage whose REST API serves the instrument
*definitions* backing every futures market - the products (e.g. ``ES`` =
E-mini S&P 500) and their individual contracts/expiries (e.g. ``ESM5``). This
adapter authenticates against the Tradovate REST API and exposes those
definitions as provider-agnostic schema objects (schemas/tradovate.py).

Scope (deliberate):
  * This adapter defaults to the DEMO host (paper trading) - see
    ``environment``. Real credentials are never required to be live.
  * It serves REFERENCE futures data over REST: find/list contracts and
    products. Tradovate delivers real-time quotes, DOM and historical bars
    over its Market Data *WebSocket* (a persistent md.tradovateapi.com
    connection using ``mdAccessToken``), which is a different transport than
    the request/response :class:`HttpTransport` every other adapter here uses;
    that streaming price feed is intentionally out of scope for this REST
    adapter rather than faked.

Auth: a single POST to ``/auth/accesstokenrequest`` returns a bearer
``accessToken`` (~90-minute lifetime) plus an ``mdAccessToken``. The token is
cached and reused until it nears expiry (governed by the injectable monotonic
clock, so tests need no real waiting). Tradovate answers a throttled auth
attempt with a 200 body carrying a ``p-ticket``/``p-time`` *time penalty*
instead of an HTTP error; this adapter fails closed on that (raises a
RATE_LIMITED :class:`ProviderError` reporting the wait) rather than hammering
the endpoint - repeatedly flooding it risks an account-level penalty ticket.

Licensing: futures instrument/market data is exchange-proprietary and tied to
a Tradovate account; it is NOT redistributable and is not sourced into ML
features. Credentials stay server-side and are never logged (the shared
transport redacts them).
"""
from __future__ import annotations

from typing import Any, Callable
import time as _time

from catalystiq.providers.base import (
    DataDomain,
    LicenseClassification,
    ProviderError,
    ProviderErrorCategory,
)
from catalystiq.providers.fetch_tracker import record_fetch
from catalystiq.providers.transport import HttpTransport, RateLimiter
from catalystiq.schemas.tradovate import FuturesContract, FuturesProduct

# Paper-trading (demo) and live REST hosts. This build targets paper by default.
_DEMO_BASE = "https://demo.tradovateapi.com/v1"
_LIVE_BASE = "https://live.tradovateapi.com/v1"


def _base_url(environment: str) -> str:
    return _LIVE_BASE if (environment or "").strip().lower() == "live" else _DEMO_BASE


class TradovateProvider:
    """Futures reference-data adapter over the Tradovate REST API.

    ``username``/``password`` and the API application credentials
    ``cid``/``sec`` are required; ``app_id``/``app_version``/``device_id`` are
    optional identifiers Tradovate recommends but does not require.
    ``transport`` and ``monotonic`` are injectable for offline tests."""

    PROVIDER_NAME = "tradovate"
    ADAPTER_VERSION = "1.0.0"
    DOMAIN = DataDomain.MARKET_DATA
    LICENSE = LicenseClassification.PROPRIETARY
    # Exchange-proprietary, account-bound futures data: never persisted as raw
    # values into ML features / redistributed (mirrors the Twelve Data guard).
    RESTRICTED_NO_RAW_PERSIST = True

    def __init__(
        self,
        *,
        username: str,
        password: str,
        cid: str,
        sec: str,
        app_id: str = "",
        app_version: str = "1.0",
        device_id: str = "",
        environment: str = "demo",
        transport: HttpTransport | None = None,
        token_ttl_seconds: float = 80 * 60,  # renew before the ~90-min server expiry
        monotonic: Callable[[], float] = _time.monotonic,
    ) -> None:
        missing = [
            attr
            for attr, val in (
                ("tradovate_username", username),
                ("tradovate_password", password),
                ("tradovate_cid", cid),
                ("tradovate_sec", sec),
            )
            if not val
        ]
        if missing:
            raise ProviderError(
                f"Tradovate is missing required configuration: {', '.join(missing)}.",
                category=ProviderErrorCategory.CONFIG,
                provider=self.PROVIDER_NAME,
            )
        self._username = username
        self._password = password
        self._cid = cid
        self._sec = sec
        self._app_id = app_id
        self._app_version = app_version
        self._device_id = device_id
        self._environment = (environment or "demo").strip().lower()
        self._token_ttl = max(1.0, float(token_ttl_seconds))
        self._monotonic = monotonic
        # Conservative pacing well under Tradovate's limits (5000 req/hour, plus
        # per-second/minute caps); the transport also retries transient 429/5xx.
        self._transport = transport or HttpTransport(
            self.PROVIDER_NAME,
            base_url=_base_url(self._environment),
            rate_limiter=RateLimiter(rate_per_sec=2.0, capacity=10),
        )
        self._access_token: str | None = None
        self._md_access_token: str | None = None
        self._token_deadline: float = 0.0

    # --- authentication ----------------------------------------------------
    def _auth_body(self) -> dict:
        # cid is a numeric client id in Tradovate; coerce when it looks numeric,
        # otherwise send as given (the server validates it).
        cid: Any = self._cid
        if isinstance(cid, str) and cid.isdigit():
            cid = int(cid)
        body = {
            "name": self._username,
            "password": self._password,
            "appId": self._app_id or "CatalystIQ",
            "appVersion": self._app_version or "1.0",
            "cid": cid,
            "sec": self._sec,
        }
        if self._device_id:
            body["deviceId"] = self._device_id
        return body

    def _authenticate(self) -> None:
        resp = self._transport.request(
            "POST", "auth/accesstokenrequest", json=self._auth_body()
        ).raise_for_status()
        data = resp.json()
        if not isinstance(data, dict):
            raise ProviderError(
                "Tradovate auth returned an unexpected payload.",
                category=ProviderErrorCategory.MALFORMED_RESPONSE,
                provider=self.PROVIDER_NAME,
            )

        # Time-penalty response: Tradovate answers a throttled auth attempt with
        # a 200 body carrying p-ticket/p-time rather than a 429. Fail closed with
        # the wait so the caller backs off instead of triggering a harsher
        # account-level penalty ticket. (Secrets are never in this message.)
        p_time = data.get("p-time")
        if data.get("p-ticket") is not None or p_time is not None:
            wait = f" (retry after ~{p_time}s)" if p_time is not None else ""
            raise ProviderError(
                f"Tradovate auth is rate-limited by a time penalty{wait}.",
                category=ProviderErrorCategory.RATE_LIMITED,
                provider=self.PROVIDER_NAME,
            )

        token = data.get("accessToken")
        if not token:
            # errorText carries the human-readable cause (e.g. bad credentials);
            # treat a token-less response as an auth failure.
            detail = str(data.get("errorText") or "no accessToken in response")
            raise ProviderError(
                f"Tradovate authentication failed: {detail}",
                category=ProviderErrorCategory.AUTH,
                provider=self.PROVIDER_NAME,
            )
        self._access_token = token
        self._md_access_token = data.get("mdAccessToken")
        self._token_deadline = self._monotonic() + self._token_ttl

    def _ensure_token(self) -> str:
        if self._access_token is None or self._monotonic() >= self._token_deadline:
            self._authenticate()
        assert self._access_token is not None
        return self._access_token

    # --- REST reads --------------------------------------------------------
    def _get(self, path: str, params: dict | None = None) -> Any:
        token = self._ensure_token()
        headers = {"Authorization": f"Bearer {token}"}
        resp = self._transport.request("GET", path, params=params, headers=headers)
        # A stale/revoked token surfaces as 401; re-authenticate once and retry
        # so a caller isn't broken by a token that expired mid-session.
        if resp.status_code in (401, 403):
            self._access_token = None
            token = self._ensure_token()
            headers = {"Authorization": f"Bearer {token}"}
            resp = self._transport.request("GET", path, params=params, headers=headers)
        return resp.raise_for_status().json()

    def find_contract(self, symbol: str) -> FuturesContract:
        """Look up a single futures contract by its exact symbol name (e.g.
        ``ESM5``). Raises NOT_FOUND when Tradovate has no such contract."""
        data = self._get("contract/find", {"name": symbol.strip().upper()})
        contract = _first_entity(data)
        if not contract or contract.get("id") is None:
            raise ProviderError(
                f"Tradovate has no contract named {symbol!r}.",
                category=ProviderErrorCategory.NOT_FOUND,
                provider=self.PROVIDER_NAME,
            )
        record_fetch(self.PROVIDER_NAME)
        return _map_contract(contract)

    def get_contract(self, contract_id: int) -> FuturesContract:
        """Fetch a futures contract by its Tradovate id."""
        data = self._get("contract/item", {"id": contract_id})
        contract = _first_entity(data)
        if not contract or contract.get("id") is None:
            raise ProviderError(
                f"Tradovate has no contract with id {contract_id}.",
                category=ProviderErrorCategory.NOT_FOUND,
                provider=self.PROVIDER_NAME,
            )
        record_fetch(self.PROVIDER_NAME)
        return _map_contract(contract)

    def find_product(self, symbol: str) -> FuturesProduct:
        """Look up a futures product definition by its root symbol (e.g.
        ``ES``). Raises NOT_FOUND when Tradovate has no such product."""
        data = self._get("product/find", {"name": symbol.strip().upper()})
        product = _first_entity(data)
        if not product or product.get("id") is None:
            raise ProviderError(
                f"Tradovate has no product named {symbol!r}.",
                category=ProviderErrorCategory.NOT_FOUND,
                provider=self.PROVIDER_NAME,
            )
        record_fetch(self.PROVIDER_NAME)
        return _map_product(product)

    def list_products(self) -> list[FuturesProduct]:
        """All futures product definitions Tradovate exposes to the account.
        Skips malformed rows rather than fabricating fields."""
        data = self._get("product/list")
        rows = data if isinstance(data, list) else []
        products = [_map_product(row) for row in rows if isinstance(row, dict) and row.get("id") is not None]
        record_fetch(self.PROVIDER_NAME)
        return products


def _first_entity(data: Any) -> dict | None:
    """Tradovate `find`/`item` endpoints return either a single object or a
    one-element list depending on the entity; normalize to the first dict."""
    if isinstance(data, dict):
        return data
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return data[0]
    return None


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _map_contract(raw: dict) -> FuturesContract:
    return FuturesContract(
        id=int(raw["id"]),
        name=str(raw.get("name") or ""),
        product_id=_as_int(raw.get("productId")),
        contract_maturity_id=_as_int(raw.get("contractMaturityId")),
        status=raw.get("status"),
        tick_size=_as_float(raw.get("providerTickSize") or raw.get("tickSize")),
    )


def _map_product(raw: dict) -> FuturesProduct:
    return FuturesProduct(
        id=int(raw["id"]),
        name=str(raw.get("name") or ""),
        product_type=raw.get("productType"),
        description=raw.get("description"),
        exchange_id=_as_int(raw.get("exchangeId")),
        currency_id=_as_int(raw.get("currencyId")),
        value_per_point=_as_float(raw.get("valuePerPoint")),
        tick_size=_as_float(raw.get("tickSize")),
        price_format_type=raw.get("priceFormatType"),
        status=raw.get("status"),
    )


def get_tradovate_provider() -> TradovateProvider:
    """Build the Tradovate adapter from settings. Raises a CONFIG-category
    :class:`ProviderError` when required credentials are unset."""
    from catalystiq.config import get_settings

    settings = get_settings()
    return TradovateProvider(
        username=settings.tradovate_username,
        password=settings.tradovate_password,
        cid=settings.tradovate_cid,
        sec=settings.tradovate_sec,
        app_id=settings.tradovate_app_id,
        app_version=settings.tradovate_app_version,
        device_id=settings.tradovate_device_id,
        environment=settings.tradovate_environment,
        token_ttl_seconds=settings.tradovate_token_ttl_seconds,
    )
