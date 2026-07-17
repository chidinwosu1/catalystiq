"""Reference-calculation adapter for the Gold Data Validation Engine.

Cross-checks Gold indicator outputs against a second, independently-coded
implementation per indicator - TA-Lib where it has the same indicator
(talib_adapter.py), TradingView's published formula for standard
indicators TA-Lib doesn't carry (tradingview_formulas.py), and an
independent financial-statistics implementation for Beta/Sharpe/VaR
(independent_stats.py). `registry.py` maps each indicator to its source
and tolerance; `comparator.py` runs the actual comparison and persists the
audit trail (GoldReferenceCheck).

Deliberately NOT wired into the synchronous request path - see
catalystiq/validation/reference/scheduler.py and market_price_pipeline.py's
anomaly-flagging hook for where this actually gets invoked (CI, an async
sampled/flagged job, never inline with a user request).

Composite outputs with no single universal external reference value
(market regime, trend structure, breakout state, market-context leading/
lagging classifications) are explicitly out of scope for numeric
comparison here - see composite_scenarios.py and
tests/test_composite_reference_scenarios.py for how those are validated
instead (documented decision rules + synthetic scenarios).
"""
