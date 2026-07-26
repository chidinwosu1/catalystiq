"""Provider base identity + source registry/factory (Phase 1 foundation)."""
import pytest

from catalystiq.config import Settings
from catalystiq.providers.base import DataDomain, ProviderError, ProviderErrorCategory
from catalystiq.providers.broker import WebullBroker
from catalystiq.providers.twelve_data import TwelveDataProvider
from catalystiq.providers import registry


def test_existing_adapters_declare_identity():
    # Yahoo has been removed; Webull (brokerage + market data) and Twelve Data
    # (price fallback) are the price sources now.
    assert WebullBroker.PROVIDER_NAME == "webull"
    assert WebullBroker.DOMAIN is DataDomain.BROKERAGE
    assert WebullBroker.ADAPTER_VERSION

    assert TwelveDataProvider.PROVIDER_NAME == "twelve_data"
    assert TwelveDataProvider.DOMAIN is DataDomain.MARKET_DATA


def test_registry_covers_every_spec_source():
    names = {s.name for s in registry.SOURCE_REGISTRY}
    expected = {
        "twelve_data",
        "sec_edgar",
        "finnhub",
        "fred",
        "bls",
        "bea",
        "nyse",
        "finra",
        "nasdaq_trader",
        "webull",
    }
    assert expected <= names
    assert "yahoo" not in names  # Yahoo fully removed


def test_registry_descriptors_are_non_secret():
    # A descriptor must never carry a credential value - only setting *names*.
    for s in registry.SOURCE_REGISTRY:
        for attr in s.required_settings:
            assert isinstance(attr, str) and attr.islower()


def test_keyless_sources_are_always_enabled():
    settings = Settings()
    # NYSE has no enable flag => always on regardless of settings.
    assert registry.is_source_enabled("nyse", settings) is True


def test_enable_flag_gates_source():
    assert registry.is_source_enabled("webull", Settings(enable_webull=False)) is False
    assert registry.is_source_enabled("webull", Settings(enable_webull=True)) is True


def test_missing_settings_reports_names_only():
    settings = Settings(enable_webull=True, webull_app_key="", webull_app_secret="", webull_account_id="")
    missing = registry.missing_settings("webull", settings)
    assert set(missing) == {"webull_app_key", "webull_app_secret", "webull_account_id"}
    settings2 = Settings(
        enable_webull=True, webull_app_key="k", webull_app_secret="s", webull_account_id="a"
    )
    assert registry.missing_settings("webull", settings2) == []


def test_build_adapter_unknown_source_raises_config_error():
    with pytest.raises(ProviderError) as exc:
        registry.build_adapter("does_not_exist", Settings())
    assert exc.value.category is ProviderErrorCategory.CONFIG


def test_build_adapter_disabled_source_raises_config_error():
    with pytest.raises(ProviderError) as exc:
        registry.build_adapter("webull", Settings(enable_webull=False))
    assert exc.value.category is ProviderErrorCategory.CONFIG


def test_build_adapter_unconfigured_source_raises_config_error():
    with pytest.raises(ProviderError) as exc:
        registry.build_adapter("webull", Settings(enable_webull=True))
    assert exc.value.category is ProviderErrorCategory.CONFIG


def test_build_adapter_unimplemented_source_raises_config_error(monkeypatch):
    # All real sources are implemented now; exercise the defensive
    # "registered but not implemented" branch with a synthetic descriptor.
    from catalystiq.providers.base import DataDomain

    fake = registry.SourceDescriptor(
        name="__fake__", domain=DataDomain.MARKET_DATA, enable_setting=None, implemented=False
    )
    monkeypatch.setitem(registry._BY_NAME, "__fake__", fake)
    with pytest.raises(ProviderError) as exc:
        registry.build_adapter("__fake__", Settings())
    assert exc.value.category is ProviderErrorCategory.CONFIG
    assert "not implemented" in str(exc.value).lower()


def test_build_adapter_keyless_source_constructs():
    # NYSE is keyless + always enabled, so its adapter builds without any config
    # (the price adapters need live credentials, exercised elsewhere).
    adapter = registry.build_adapter("nyse", Settings())
    assert adapter is not None
