import type { AnalysisReport } from "./types";

/**
 * DEMO DATA — the analytical engine (indicators/regime/scoring) and the
 * behavioral FBA engine from the build spec aren't implemented yet
 * (only Phase 1, data plumbing, is built). These reports are hand-authored
 * so the Phase 7 UI has something real to render; nothing here comes from
 * live computation.
 */
export const mockReports: AnalysisReport[] = [
  {
    ticker: "NVDA",
    companyName: "NVIDIA Corp",
    timeframeLabel: "Swing trade · 2-10 day view",
    rating: "Buy",
    probability: { bullish: 58, neutral: 27, bearish: 15 },
    confidence: 72,
    expectedMove: "±4.2%",
    invalidation: "Close below $142.80",
    drivers: [
      { icon: "trend-up", label: "Technical", detail: "Above 20/50-DMA, RSI 61 (not yet overbought)", contribution: "+" },
      { icon: "options", label: "Options", detail: "Call skew rising, IV rank 38", contribution: "+" },
      { icon: "building", label: "Institutional", detail: "3 analyst upgrades in the last 5 sessions", contribution: "+" },
      { icon: "newspaper", label: "Fundamentals", detail: "Valuation stretched vs. sector (PEG 2.1)", contribution: "-" },
    ],
    behavioralSignal: {
      label: "FOMO buying",
      description: "Similar upside volume/velocity spikes after an earnings-beat antecedent extended 68% of the time before reverting, based on 3 years of this ticker's history.",
    },
    trackRecord: { accuracyPct: 64, sampleSize: 127, windowLabel: "past 3 years" },
  },
  {
    ticker: "SNOW",
    companyName: "Snowflake Inc",
    timeframeLabel: "Next-day view",
    rating: "Hold",
    probability: { bullish: 34, neutral: 40, bearish: 26 },
    confidence: 41,
    expectedMove: "±3.1%",
    invalidation: "Close below $148.10 or above $167.50",
    drivers: [
      { icon: "trend-up", label: "Technical", detail: "Range-bound between 50 and 100-DMA", contribution: "neutral" },
      { icon: "options", label: "Options", detail: "Put/call ratio flat, no unusual activity", contribution: "neutral" },
      { icon: "newspaper", label: "News", detail: "Guidance call in 6 days - modules disagree on positioning", contribution: "-" },
    ],
  },
  {
    ticker: "PLTR",
    companyName: "Palantir Technologies",
    timeframeLabel: "Long-term · 6-12 month view",
    rating: "Sell",
    probability: { bullish: 18, neutral: 24, bearish: 58 },
    confidence: 55,
    expectedMove: "±6.8%",
    invalidation: "Close above $38.40",
    drivers: [
      { icon: "trend-up", label: "Technical", detail: "Below 50-DMA, MACD bearish crossover", contribution: "-" },
      { icon: "building", label: "Fundamentals", detail: "EV/EBITDA at 92nd percentile, 5y history", contribution: "-" },
      { icon: "users", label: "Institutional", detail: "Elevated short interest, rising 3 weeks running", contribution: "-" },
    ],
    behavioralSignal: {
      label: "Complacency",
      description: "Realized vol sits near a 2-year low despite unresolved valuation risk; setups like this preceded a vol expansion within 15 days 71% of the time historically.",
    },
    trackRecord: { accuracyPct: 59, sampleSize: 84, windowLabel: "past 3 years" },
    dataQualityWarning: "Options open-interest feed delayed 20 minutes - options-derived figures may lag.",
  },
];
