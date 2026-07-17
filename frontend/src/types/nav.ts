export type PageId = "home" | "trade" | "portfolio" | "markets" | "analysis";

export interface PageDescriptor {
  id: PageId;
  label: string;
  description: string;
}

export const PAGES: PageDescriptor[] = [
  {
    id: "home",
    label: "Command Center",
    description:
      "Your investment command center - today's market read, highest-conviction opportunities, catalysts, alerts, and what to do next.",
  },
  {
    id: "trade",
    label: "Trade",
    description:
      "Build and submit a paper order - real backend, all order types, optional bracket exits and scheduled execution.",
  },
  {
    id: "portfolio",
    label: "Portfolio",
    description:
      "Your real account balance and positions from the connected paper-trading broker.",
  },
  {
    id: "markets",
    label: "Market Analysis",
    description:
      "The daily macro dashboard - index levels, sector ranking, catalysts, and investor behavior analysis.",
  },
  {
    id: "analysis",
    label: "Analysis",
    description:
      "Research a ticker, log your trades, and review your own performance over time.",
  },
];
