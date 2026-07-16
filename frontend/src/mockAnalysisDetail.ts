import { mockReports } from "./mockData";
import type { AnalysisReport } from "./types";

/** DEMO DATA - see mockData.ts. The Technical module isn't built yet. */
export interface SetupSnapshot {
  trend: string;
  volume: string;
  relativeStrength: string;
  support: string;
  resistance: string;
  movingAverages: string;
  rsi: string;
  macd: string;
  volatility: string;
  optionsActivity: string;
  news: string;
  earningsDate: string;
}

const GENERIC_SETUP: SetupSnapshot = {
  trend: "Uptrend, higher highs/higher lows over 20 sessions",
  volume: "Slightly above 20-day average",
  relativeStrength: "62nd percentile vs. sector",
  support: "5% below current price",
  resistance: "4% above current price",
  movingAverages: "Above 20 and 50-DMA, below 200-DMA",
  rsi: "58 (neutral)",
  macd: "Bullish crossover 3 sessions ago",
  volatility: "IV rank 34",
  optionsActivity: "No unusual flow detected",
  news: "No major headlines in the last 48h",
  earningsDate: "In 18 days",
};

const genericReport = (symbol: string): AnalysisReport => ({
  ticker: symbol,
  companyName: symbol,
  timeframeLabel: "Swing trade · 2-10 day view",
  rating: "Hold",
  probability: { bullish: 38, neutral: 36, bearish: 26 },
  confidence: 48,
  expectedMove: "±3.5%",
  invalidation: "Close below the 50-DMA",
  drivers: [
    { icon: "trend-up", label: "Technical", detail: "Mixed signals, no clear edge", contribution: "neutral" },
    { icon: "newspaper", label: "Fundamentals", detail: "In line with sector averages", contribution: "neutral" },
  ],
});

export function getDemoAnalysis(symbol: string): AnalysisReport {
  const match = mockReports.find((r) => r.ticker === symbol.toUpperCase());
  return match ?? genericReport(symbol.toUpperCase());
}

export function getDemoSetup(_symbol: string): SetupSnapshot {
  return GENERIC_SETUP;
}
