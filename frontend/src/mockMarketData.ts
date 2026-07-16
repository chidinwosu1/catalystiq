/**
 * DEMO DATA - the Market Environment, Sector, and News modules from the
 * build spec (§2.2 A/B/G) aren't implemented yet. Everything in this file
 * is illustrative, hand-authored content so the Market Intelligence page
 * has a real layout to render against; none of it is computed.
 */

export interface MarketIndexRow {
  symbol: string;
  label: string;
  level: string;
  changePct: number;
  interpretation: string;
}

export const marketOverview: MarketIndexRow[] = [
  { symbol: "SPX", label: "S&P 500", level: "5,842.31", changePct: -0.4, interpretation: "Broad market drifting lower with yields." },
  { symbol: "IXIC", label: "Nasdaq", level: "18,203.44", changePct: -0.7, interpretation: "Technology is lagging as Treasury yields rise." },
  { symbol: "DJI", label: "Dow", level: "42,118.05", changePct: -0.1, interpretation: "Defensive names holding up better than growth." },
  { symbol: "RUT", label: "Russell 2000", level: "2,198.77", changePct: -1.1, interpretation: "Small caps most sensitive to rate pressure." },
  { symbol: "VIX", label: "VIX", level: "16.82", changePct: 6.3, interpretation: "Volatility ticking up but still in a calm regime." },
  { symbol: "US2Y", label: "2-Year Treasury", level: "4.42%", changePct: 1.8, interpretation: "Short-end pricing in fewer near-term cuts." },
  { symbol: "US10Y", label: "10-Year Treasury", level: "4.31%", changePct: 2.4, interpretation: "Rising yields pressuring long-duration growth stocks." },
  { symbol: "DXY", label: "US Dollar Index", level: "104.12", changePct: 0.5, interpretation: "Dollar strength headwind for multinational earnings." },
  { symbol: "WTI", label: "Oil (WTI)", level: "$78.42", changePct: -0.9, interpretation: "Crude soft on demand concerns." },
  { symbol: "GOLD", label: "Gold", level: "$2,614.20", changePct: 0.3, interpretation: "Modest safe-haven bid." },
];

export interface SectorRow {
  name: string;
  dailyPct: number;
  weeklyPct: number;
  relativeStrength: number;
  volume: "Above avg" | "Average" | "Below avg";
  leadershipScore: number;
}

export const sectorRotation: SectorRow[] = [
  { name: "Technology", dailyPct: -0.7, weeklyPct: 1.8, relativeStrength: 78, volume: "Above avg", leadershipScore: 82 },
  { name: "Healthcare", dailyPct: 0.6, weeklyPct: 2.4, relativeStrength: 71, volume: "Average", leadershipScore: 75 },
  { name: "Financials", dailyPct: 0.3, weeklyPct: 1.1, relativeStrength: 64, volume: "Average", leadershipScore: 68 },
  { name: "Energy", dailyPct: -1.2, weeklyPct: -2.1, relativeStrength: 41, volume: "Below avg", leadershipScore: 38 },
  { name: "Industrials", dailyPct: 0.1, weeklyPct: 0.6, relativeStrength: 58, volume: "Average", leadershipScore: 55 },
  { name: "Consumer Discretionary", dailyPct: -0.4, weeklyPct: 0.2, relativeStrength: 52, volume: "Average", leadershipScore: 49 },
  { name: "Communication Services", dailyPct: -0.2, weeklyPct: 1.3, relativeStrength: 60, volume: "Above avg", leadershipScore: 63 },
  { name: "Utilities", dailyPct: 0.8, weeklyPct: 1.9, relativeStrength: 55, volume: "Below avg", leadershipScore: 52 },
  { name: "Real Estate", dailyPct: -0.6, weeklyPct: -0.8, relativeStrength: 34, volume: "Below avg", leadershipScore: 31 },
  { name: "Materials", dailyPct: -0.3, weeklyPct: -0.4, relativeStrength: 44, volume: "Average", leadershipScore: 40 },
  { name: "Consumer Staples", dailyPct: 0.4, weeklyPct: 0.7, relativeStrength: 49, volume: "Average", leadershipScore: 47 },
];

export type CatalystStatus = "Confirmed" | "Proposed" | "Speculation";

export interface CatalystRow {
  category: string;
  headline: string;
  status: CatalystStatus;
  transmissionPath: string;
}

export const catalysts: CatalystRow[] = [
  {
    category: "Tariffs and trade",
    headline: "Proposed tariff increase on select imports",
    status: "Proposed",
    transmissionPath: "Higher tariffs → higher import costs → inflation pressure → higher Treasury yields → possible pressure on growth stocks",
  },
  {
    category: "Fed decisions",
    headline: "FOMC holds rates, signals data-dependent path",
    status: "Confirmed",
    transmissionPath: "Rates held → policy uncertainty persists → yield volatility → mixed equity reaction",
  },
  {
    category: "Economic releases",
    headline: "CPI print due this week",
    status: "Confirmed",
    transmissionPath: "Hotter CPI → fewer expected cuts → higher yields → growth/value rotation",
  },
  {
    category: "Earnings",
    headline: "Mega-cap tech earnings season begins",
    status: "Confirmed",
    transmissionPath: "Earnings beat/miss → guidance revisions → sector-wide repricing",
  },
  {
    category: "Geopolitical developments",
    headline: "Reports of escalating regional tensions",
    status: "Speculation",
    transmissionPath: "Escalation risk → safe-haven flows → oil/gold bid → risk-off in equities",
  },
];

export interface WatchlistRow {
  symbol: string;
  intradayRating: string;
  swingRating: string;
  confidence: number;
  bullishPct: number;
  bearishPct: number;
  expectedMove: string;
  catalyst: string;
  risk: string;
}

export const dailyWatchlist: WatchlistRow[] = [
  { symbol: "NVDA", intradayRating: "Buy", swingRating: "Buy", confidence: 72, bullishPct: 58, bearishPct: 15, expectedMove: "±4.2%", catalyst: "AI capex commentary", risk: "Valuation stretched" },
  { symbol: "SNOW", intradayRating: "Hold", swingRating: "Hold", confidence: 41, bullishPct: 34, bearishPct: 26, expectedMove: "±3.1%", catalyst: "Guidance call in 6 days", risk: "Modules disagree" },
  { symbol: "PLTR", intradayRating: "Sell", swingRating: "Sell", confidence: 55, bullishPct: 18, bearishPct: 58, expectedMove: "±6.8%", catalyst: "Elevated short interest", risk: "Below 50-DMA" },
  { symbol: "UNH", intradayRating: "Buy", swingRating: "Hold", confidence: 63, bullishPct: 49, bearishPct: 22, expectedMove: "±2.9%", catalyst: "Sector leadership", risk: "Regulatory headline risk" },
];
