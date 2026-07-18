"""Startup configuration validation (§2): fails fast on an enabled+wired
source with missing config, never leaks a secret value, and never blocks the
app on a source that's merely enabled-but-not-yet-implemented."""
import pytest

from catalystiq.config import ConfigurationError, Settings, validate_settings


def test_default_settings_are_valid():
    # Out of the box: Yahoo (keyless) works, Webull off, macro/regulatory
    # sources enabled but not yet implemented => no startup failure.
    validate_settings(Settings())


def test_enabled_but_unimplemented_source_missing_key_does_not_block():
    # Twelve Data enabled with no key must NOT fail startup (no adapter yet,
    # arrives in Phase 4) - acceptance criterion 6 (unrelated/optional keys
    # don't break the app).
    validate_settings(Settings(enable_twelve_data=True, twelve_data_api_key=""))


def test_enabled_implemented_source_missing_key_raises():
    # FRED is implemented now; enabling it without a key must fail fast.
    with pytest.raises(ConfigurationError) as exc:
        validate_settings(Settings(enable_fred=True, fred_api_key=""))
    assert "fred" in str(exc.value)
    assert "fred_api_key" in str(exc.value)


def test_enabled_implemented_source_missing_creds_raises():
    with pytest.raises(ConfigurationError) as exc:
        validate_settings(
            Settings(
                enable_webull=True,
                webull_app_key="",
                webull_app_secret="",
                webull_account_id="",
            )
        )
    msg = str(exc.value)
    assert "webull" in msg
    # Reports the missing setting NAMES...
    assert "webull_app_key" in msg


def test_validation_error_never_contains_secret_values():
    secret = "SUPER_SECRET_VALUE"
    with pytest.raises(ConfigurationError) as exc:
        validate_settings(
            Settings(
                enable_webull=True,
                webull_app_key=secret,  # present
                webull_app_secret="",  # missing -> triggers error
                webull_account_id="",
            )
        )
    assert secret not in str(exc.value)


def test_unknown_primary_provider_is_a_config_error():
    with pytest.raises(ConfigurationError) as exc:
        validate_settings(Settings(market_data_primary_provider="not_a_source"))
    assert "MARKET_DATA_PRIMARY_PROVIDER" in str(exc.value)


def test_fully_configured_webull_passes():
    validate_settings(
        Settings(
            enable_webull=True,
            webull_app_key="k",
            webull_app_secret="s",
            webull_account_id="a",
        )
    )
