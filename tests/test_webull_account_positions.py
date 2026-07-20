"""Mapping of Webull's real (sandbox-verified) balance/positions JSON to the
provider-agnostic AccountInfo / Position schemas."""
from __future__ import annotations

from catalystiq.providers.broker import _map_webull_account, _map_webull_positions

# Real Webull sandbox responses captured via /paper/webull-raw.
BALANCE = {
    "total_asset_currency": "USD",
    "total_net_liquidation_value": "999998.27",
    "total_market_value": "682.92",
    "total_cash_balance": "999315.35",
    "total_unrealized_profit_loss": "-1.73",
    "total_day_profit_loss": "-1.73",
    "day_trades_left": "UNLIMITED",
    "maintenance_margin": "170.73",
    "open_margin_calls": [],
    "account_currency_assets": [
        {
            "currency": "USD",
            "net_liquidation_value": "999998.27",
            "market_value": "682.92",
            "cash_balance": "999315.35",
            "option_buying_power": "999657.68",
            "day_buying_power": "3999310.16",
            "overnight_buying_power": "1999315.35",
            "night_trading_buying_power": "999315.35",
            "unrealized_profit_loss": "-1.73",
            "day_profit_loss": "-1.73",
        }
    ],
}

POSITIONS = [
    {
        "currency": "USD",
        "quantity": "1",
        "cost": "684.65",
        "proportion": "1.0000",
        "position_id": "DSGQ8DB1AN4S6M4J0L4CGUKSE8",
        "symbol": "VOO",
        "instrument_type": "EQUITY",
        "cost_price": "684.65",
        "last_price": "682.91",
        "market_value": "682.91",
        "unrealized_profit_loss": "-1.74",
        "unrealized_profit_loss_rate": "-0.0025",
        "day_profit_loss": "-1.74",
        "day_realized_profit_loss": "0.00",
    }
]


def test_map_account():
    a = _map_webull_account(BALANCE)
    assert a.currency == "USD"
    assert a.cash == "999315.35"
    assert a.portfolio_value == "999998.27"
    assert a.equity == "999998.27"
    assert a.buying_power == "3999310.16"  # day buying power
    # last_equity = net_liq - day_pl = 999998.27 - (-1.73) = 1000000.00
    assert float(a.last_equity) == 1000000.0
    assert a.trading_blocked is False
    assert a.account_blocked is False
    assert a.status == "ACTIVE"


def test_map_account_flags_margin_call():
    data = dict(BALANCE, open_margin_calls=[{"amount": "100"}])
    assert _map_webull_account(data).trading_blocked is True


def test_map_positions():
    ps = _map_webull_positions(POSITIONS)
    assert len(ps) == 1
    p = ps[0]
    assert p.symbol == "VOO"
    assert p.side == "long"
    assert p.qty == "1"
    assert p.avg_entry_price == "684.65"
    assert p.current_price == "682.91"
    assert p.market_value == "682.91"
    assert p.cost_basis == "684.65"
    assert p.unrealized_pl == "-1.74"
    assert p.unrealized_plpc == "-0.0025"
    assert p.change_today == "-1.74"


def test_map_positions_empty_and_short():
    assert _map_webull_positions([]) == []
    short = _map_webull_positions([dict(POSITIONS[0], quantity="-5")])
    assert short[0].side == "short"
    # Tolerate a dict-wrapped list too.
    wrapped = _map_webull_positions({"positions": POSITIONS})
    assert wrapped[0].symbol == "VOO"
