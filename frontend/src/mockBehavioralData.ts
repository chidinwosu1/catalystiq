/**
 * DEMO DATA - this is the build spec's §3 Functional Behavioral Analysis
 * (FBA) engine: Antecedent -> Behavior -> Consequence, with rule-based
 * detectors and empirical reinforcement-history lookups. None of that is
 * built yet (Phase 5). This is a hand-authored illustration of the table
 * shape so the UI has something real to render against.
 */
export interface BehavioralRow {
  trigger: string;
  antecedent: string;
  investorBehavior: string;
  positiveDriver: string;
  negativeDriver: string;
  marketBehavior: string;
}

export const marketWideBehavioralAnalysis: BehavioralRow[] = [
  {
    trigger: "Fed policy",
    antecedent: "FOMC holds rates, chair signals data-dependent path",
    investorBehavior: "Herding into rate-sensitive growth names on relief",
    positiveDriver: "Softer-than-expected inflation prints reinforce the move",
    negativeDriver: "A hawkish follow-up speech or hot jobs report reverses it",
    marketBehavior: "Short-term rally in growth/tech, likely to fade without confirmation",
  },
  {
    trigger: "Macro data",
    antecedent: "CPI print due this week, consensus expects a cooling reading",
    investorBehavior: "Confirmation-seeking flow - options activity skewing toward the consensus outcome",
    positiveDriver: "An in-line or cooler print validates the positioning",
    negativeDriver: "A hot surprise triggers rapid unwind and volatility expansion",
    marketBehavior: "Elevated pre-print volatility, directional move likely to overshoot on release",
  },
  {
    trigger: "Tariffs and trade",
    antecedent: "Proposed tariff increase on select imports",
    investorBehavior: "Recency bias - investors overweighting the last trade-war episode's playbook",
    positiveDriver: "De-escalation rhetoric or delayed implementation calms the reaction",
    negativeDriver: "Retaliatory measures announced, extending the selloff",
    marketBehavior: "Sector-specific pressure on import-exposed names, broad market drag",
  },
  {
    trigger: "Earnings",
    antecedent: "Mega-cap tech earnings season begins with elevated expectations",
    investorBehavior: "FOMO buying into names with a history of post-earnings drift",
    positiveDriver: "Beat-and-raise guidance extends the drift for 5-10 sessions historically",
    negativeDriver: "In-line results without guidance raise often see \"sell the news\"",
    marketBehavior: "Binary reaction likely - large gap move in either direction on the print",
  },
  {
    trigger: "Geopolitical",
    antecedent: "Reports of escalating regional tensions",
    investorBehavior: "Panic-selling risk in richly-valued growth names if headlines worsen",
    positiveDriver: "De-escalation or containment reassures markets within days",
    negativeDriver: "Confirmed escalation triggers a broader risk-off cascade",
    marketBehavior: "Safe-haven bid (gold, Treasuries), risk-off rotation out of high-beta equities",
  },
];

export function getStockBehavioralAnalysis(symbol: string): BehavioralRow[] {
  return [
    {
      trigger: "Technical level breach",
      antecedent: `${symbol} closed below its 50-day moving average on above-average volume`,
      investorBehavior: "Momentum-following selling as technical traders exit on the breach",
      positiveDriver: "A quick reclaim of the level within 1-2 sessions often extinguishes the move",
      negativeDriver: "A failed retest confirms the breakdown, inviting further momentum selling",
      marketBehavior: "Short-term downside continuation, historically mean-reverts within 5-10 sessions",
    },
    {
      trigger: "Analyst activity",
      antecedent: "Recent rating change or price-target revision from a major desk",
      investorBehavior: "Herding - retail and algo flow following the revision direction",
      positiveDriver: "Follow-through upgrades from other desks confirm the thesis",
      negativeDriver: "No confirmation from other coverage - the move fades as an isolated data point",
      marketBehavior: "Initial gap move, typically retraces a portion within the week absent confirmation",
    },
    {
      trigger: "Options positioning",
      antecedent: "Unusual call volume detected ahead of a known catalyst",
      investorBehavior: "Confirmation-seeking flow concentrated on one side of the debate",
      positiveDriver: "The catalyst resolves in the direction the flow was positioned for",
      negativeDriver: "A surprise outcome triggers a rapid unwind of the crowded positioning",
      marketBehavior: "Elevated implied volatility into the event, sharp realized-vol move after",
    },
  ];
}
