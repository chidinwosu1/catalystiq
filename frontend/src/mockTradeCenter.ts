/**
 * DEMO DATA — the Trade Center's discovery workspace. Shaped like the
 * analytical engine's opportunity output so each field can later be swapped
 * for a real fetch/selector without touching the page. Everything here is
 * hand-authored, illustrative content until the scoring model is built.
 */

import type { Rating } from "./types";
import type { RiskLevel } from "./mockDashboardData";

export interface OpportunityDetail {
  symbol: string;
  companyName: string;
  price: string;
  rating: Rating;
  catalystScore: number; // 0-100 (opportunity score)
  confidence: number; // 0-100
  probabilityOfProfit: number; // 0-100
  expectedReturn: string;
  risk: RiskLevel;
  holdingPeriod: string;
  primaryCatalyst: string;
  summary: string;
  strategy: string;
  technical: string;
  market: string;
  riskText: string;
  evidence: string[];
  catalysts: string[];
  entry: string;
  target: string;
  stop: string;
  exit: string;
}

export const opportunities: OpportunityDetail[] = [
  {
    symbol: "NVDA",
    companyName: "NVIDIA Corp",
    price: "$138.42",
    rating: "Buy",
    catalystScore: 91,
    confidence: 82,
    probabilityOfProfit: 68,
    expectedReturn: "+4.2%",
    risk: "Elevated",
    holdingPeriod: "2-10 days",
    primaryCatalyst: "AI capex commentary + 3 analyst upgrades",
    summary:
      "AI-capex commentary and three fresh analyst upgrades have re-accelerated momentum. Semiconductor breadth is expanding beyond NVDA and options positioning leans bullish into the catalyst.",
    strategy: "Swing long — scale in near support, 2-10 day hold, trail stops as it works.",
    technical:
      "Uptrend intact: price above a rising 50-day SMA, RSI 61 with room to run, MACD positive and widening.",
    market:
      "Constructive regime with Technology leading. Rising yields are a mild headwind but risk appetite is firm.",
    riskText:
      "Elevated single-name volatility. A broad tech pullback or a soft AI-capex read from peers would invalidate the setup.",
    evidence: [
      "3 analyst upgrades in the last 5 sessions",
      "Relative volume 1.4x the 20-day average",
      "Sector breadth expanding beyond mega-cap semis",
      "Bullish options skew into the catalyst",
    ],
    catalysts: [
      "AI-capex commentary at industry conference",
      "Peer semiconductor earnings after the close",
      "Ongoing supply-chain restocking headlines",
    ],
    entry: "$136.50 - $138.80",
    target: "$144.20 (+4.2%)",
    stop: "$131.20 (-5.2%)",
    exit: "On close below the 20-day SMA",
  },
  {
    symbol: "UNH",
    companyName: "UnitedHealth Group",
    price: "$524.10",
    rating: "Buy",
    catalystScore: 84,
    confidence: 74,
    probabilityOfProfit: 63,
    expectedReturn: "+2.9%",
    risk: "Moderate",
    holdingPeriod: "1-5 days",
    primaryCatalyst: "Healthcare sector leadership rotation",
    summary:
      "Defensive rotation into Healthcare is favoring large-cap managed care. UNH shows relative strength versus the sector with a clean technical base.",
    strategy: "Swing long — enter on a pullback to support, 1-5 day hold, tighten stops into resistance.",
    technical:
      "Basing above the 50-day SMA; RSI neutral at 54, leaving room. Relative strength vs. XLV improving.",
    market:
      "Rotation toward defensives as breadth broadens. Healthcare leadership score rising week over week.",
    riskText:
      "Headline risk around policy and reimbursement. A break of the base would neutralize the thesis.",
    evidence: [
      "Healthcare leadership score up 2 weeks running",
      "Relative strength vs. sector at the 62nd percentile",
      "Institutional flows into managed care",
      "Low realized volatility supports position sizing",
    ],
    catalysts: [
      "Sector-rotation continuation",
      "Managed-care policy headlines",
      "Monthly membership data",
    ],
    entry: "$518.00 - $524.00",
    target: "$539.20 (+2.9%)",
    stop: "$505.90 (-3.5%)",
    exit: "On a daily close below $512",
  },
  {
    symbol: "AVGO",
    companyName: "Broadcom Inc",
    price: "$172.90",
    rating: "Buy",
    catalystScore: 79,
    confidence: 71,
    probabilityOfProfit: 61,
    expectedReturn: "+3.5%",
    risk: "Moderate",
    holdingPeriod: "3-10 days",
    primaryCatalyst: "Semiconductor breadth expanding beyond NVDA",
    summary:
      "Broadening semiconductor strength is lifting AVGO as leadership widens past NVDA. Steady accumulation and firm relative strength support a swing entry.",
    strategy: "Swing long — accumulate on dips, 3-10 day hold, scale out into the prior high.",
    technical:
      "Higher highs and higher lows over 20 sessions; MACD positive, price riding the 20-day SMA.",
    market: "Semis leading a constructive tape. Rotation within tech favors diversified chip names.",
    riskText: "Moderate beta to the semi complex. A sector-wide reversal is the primary risk.",
    evidence: [
      "Semiconductor breadth expanding beyond mega-caps",
      "20-day uptrend intact",
      "Relative volume above average",
      "Consistent institutional accumulation",
    ],
    catalysts: [
      "Semiconductor sector momentum",
      "Software/infrastructure demand headlines",
      "Peer earnings read-through",
    ],
    entry: "$169.50 - $173.00",
    target: "$179.00 (+3.5%)",
    stop: "$164.30 (-5.0%)",
    exit: "On close below the 20-day SMA",
  },
  {
    symbol: "JPM",
    companyName: "JPMorgan Chase",
    price: "$228.40",
    rating: "Buy",
    catalystScore: 74,
    confidence: 66,
    probabilityOfProfit: 59,
    expectedReturn: "+2.1%",
    risk: "Low",
    holdingPeriod: "5-15 days",
    primaryCatalyst: "Financials firm as yields grind higher",
    summary:
      "Financials are firming as yields grind higher. JPM offers a lower-volatility way to express the theme with a durable technical trend.",
    strategy: "Position long — steady 5-15 day hold, add on strength, wide stop given low volatility.",
    technical: "Grinding uptrend above all key SMAs; low realized volatility, RSI a healthy 57.",
    market:
      "Rising yields support net-interest margins. Financials leadership improving as the curve steepens.",
    riskText: "Low idiosyncratic risk; a sharp reversal in yields would be the main headwind.",
    evidence: [
      "Financials strengthening with higher yields",
      "Low realized volatility (clean trend)",
      "Above all major moving averages",
      "Steady relative strength vs. the market",
    ],
    catalysts: ["Yield-curve steepening", "Bank-sector commentary", "Credit and loan-growth data"],
    entry: "$224.00 - $228.50",
    target: "$233.20 (+2.1%)",
    stop: "$218.90 (-4.0%)",
    exit: "On a weekly close below $220",
  },
  {
    symbol: "COST",
    companyName: "Costco Wholesale",
    price: "$902.10",
    rating: "Hold",
    catalystScore: 71,
    confidence: 61,
    probabilityOfProfit: 55,
    expectedReturn: "+1.8%",
    risk: "Low",
    holdingPeriod: "5-15 days",
    primaryCatalyst: "Defensive quality bid, monthly sales due",
    summary:
      "A defensive quality bid is supporting COST ahead of monthly sales. The setup is constructive but rich, arguing for patience over aggression.",
    strategy: "Watch / partial — wait for a pullback to support before committing; small size given the rating.",
    technical:
      "Uptrend but extended above the 50-day SMA; RSI elevated at 66 argues for a better entry.",
    market:
      "Defensive quality in favor as breadth broadens. Membership model prized in a calm regime.",
    riskText: "Valuation is rich and the name is extended — chasing here carries drawdown risk.",
    evidence: [
      "Defensive quality bid persisting",
      "Membership renewal strength",
      "Low volatility profile",
      "Constructive but extended technicals",
    ],
    catalysts: ["Monthly sales report", "Membership-fee commentary", "Consumer-staples rotation"],
    entry: "$882.00 - $896.00 (on pullback)",
    target: "$918.00 (+1.8%)",
    stop: "$865.00 (-4.1%)",
    exit: "On close below the 50-day SMA",
  },
];
