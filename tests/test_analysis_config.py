import pytest
from pydantic import ValidationError

from catalystiq.analysis.config import (
    DEFAULT_MARKET_CONTEXT_CONFIG,
    DEFAULT_MARKET_STRUCTURE_CONFIG,
    DEFAULT_RISK_CONFIG,
    DEFAULT_TECHNICAL_CONFIG,
    DEFAULT_VOLUME_LIQUIDITY_CONFIG,
    PRODUCT_CONFIGS,
    RiskConfig,
    TechnicalConfig,
    VolumeLiquidityConfig,
    config_hash,
    get_configuration_version,
    get_effective_config,
)


def test_default_values_match_original_hardcoded_constants():
    # These must stay exactly equal to what shipped before externalization -
    # this pass relocates tunables, it doesn't change any formula.
    assert DEFAULT_TECHNICAL_CONFIG.rsi_period == 14
    assert DEFAULT_TECHNICAL_CONFIG.sma_windows == (20, 50, 100, 200)
    assert DEFAULT_MARKET_STRUCTURE_CONFIG.swing_left_bars == 5
    assert DEFAULT_RISK_CONFIG.var_confidence == 0.95
    assert DEFAULT_VOLUME_LIQUIDITY_CONFIG.liquidity_high_threshold == 10_000_000.0
    assert DEFAULT_MARKET_CONTEXT_CONFIG.beta_correlation_window == 60


def test_configs_are_immutable():
    with pytest.raises(ValidationError):
        DEFAULT_TECHNICAL_CONFIG.rsi_period = 21


def test_validation_rejects_invalid_values():
    with pytest.raises(ValidationError):
        TechnicalConfig(rsi_period=-1)
    with pytest.raises(ValidationError):
        TechnicalConfig(macd_fast=30, macd_slow=20)  # fast must be < slow
    with pytest.raises(ValidationError):
        RiskConfig(var_confidence=1.5)  # must be in (0, 1)
    with pytest.raises(ValidationError):
        VolumeLiquidityConfig(
            liquidity_high_threshold=100, liquidity_moderate_threshold=200, liquidity_low_threshold=50
        )  # must be descending


def test_configuration_version_is_stable_hash():
    v1 = get_configuration_version("technical")
    v2 = get_configuration_version("technical")
    assert v1 == v2
    assert isinstance(v1, str) and len(v1) == 12


def test_configuration_version_changes_when_values_change():
    baseline = config_hash(DEFAULT_TECHNICAL_CONFIG)
    changed = config_hash(TechnicalConfig(rsi_period=21))
    assert baseline != changed


def test_get_effective_config_returns_full_dict():
    effective = get_effective_config("risk")
    assert effective["var_confidence"] == 0.95
    assert "trading_days_per_year" in effective


def test_all_five_products_registered():
    assert set(PRODUCT_CONFIGS.keys()) == {
        "technical",
        "market_structure",
        "risk",
        "volume_liquidity",
        "market_context",
    }
