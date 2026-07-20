"""reconcile_webull CLI: redaction helper and the end-to-end main() flow with a
fake broker patched in (no SDK/network, never places orders)."""
import json

from catalystiq.schemas.broker import AccountInfo, BrokerAccount, OrderRecord, Position
from catalystiq.scripts import reconcile_webull as cli


def test_redact_id():
    assert cli.redact_id("DEM34946") == "****4946"
    assert cli.redact_id("") == ""
    assert cli.redact_id("abc") == "***"  # <= keep length fully masked


class FakeBroker:
    _account_id = "APIID-ABC123"

    def find_account_id(self, ref):
        return "APIID-ABC123"

    def get_order_history(self, start_date=None, end_date=None, symbol=None):
        return [
            OrderRecord(
                order_id="ORD-1",
                client_order_id="COID-1",
                symbol="VOO",
                side="BUY",
                order_type="MARKET",
                status="filled",
                status_raw="FILLED",
                total_qty="1",
                filled_qty="1",
                avg_fill_price="684.65",
                filled_amount="684.65",
            )
        ]

    def get_positions(self):
        return [
            Position(
                symbol="VOO",
                side="long",
                qty="1",
                avg_entry_price="684.65",
                market_value="682.91",
                cost_basis="684.65",
                unrealized_pl="-1.74",
                unrealized_plpc="-0.0025",
                current_price="682.91",
                change_today="-1.74",
            )
        ]

    def get_account(self):
        return AccountInfo(
            status="ACTIVE",
            currency="USD",
            cash="999315.35",
            buying_power="3999310.16",
            portfolio_value="999998.27",
            equity="999998.27",
            last_equity="1000000.00",
            trading_blocked=False,
            account_blocked=False,
            pattern_day_trader=False,
        )


def test_cli_json_output_is_redacted(monkeypatch, capsys):
    monkeypatch.setattr(cli, "get_broker_provider", lambda: FakeBroker())

    rc = cli.main(["--symbol", "VOO", "--json"])

    assert rc == 0  # reconciled OK
    out = capsys.readouterr().out
    payload = json.loads(out)
    # Account id is masked, raw blobs dropped.
    assert payload["account_id"] == "********C123"
    assert payload["_resolved_account_id"] == "********C123"
    assert "raw" not in payload["order"]
    assert payload["symbol"] == "VOO"
    assert payload["order"]["filled_qty"] == "1"


def test_cli_text_report_masks_account(monkeypatch, capsys):
    monkeypatch.setattr(cli, "get_broker_provider", lambda: FakeBroker())

    rc = cli.main(["--account", "DEM34946", "--symbol", "VOO"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "OK - reconciled" in out
    assert "****4946" in out  # requested reference masked
    assert "APIID-ABC123" not in out  # raw account id never printed


def test_cli_no_redact_shows_ids(monkeypatch, capsys):
    monkeypatch.setattr(cli, "get_broker_provider", lambda: FakeBroker())

    rc = cli.main(["--symbol", "VOO", "--json", "--no-redact"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["account_id"] == "APIID-ABC123"


def test_cli_no_matching_order_returns_3(monkeypatch, capsys):
    monkeypatch.setattr(cli, "get_broker_provider", lambda: FakeBroker())

    rc = cli.main(["--symbol", "TSLA"])

    assert rc == 3
    assert "no matching order" in capsys.readouterr().err
