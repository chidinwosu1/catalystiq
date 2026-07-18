export type PageId = "home" | "trade" | "portfolio" | "markets" | "analysis";

export interface PageDescriptor {
  id: PageId;
  label: string;
  description: string;
}

// Ordered to follow the investment workflow: get the market read, form a
// strategy, place the trade, then manage the book.
export const PAGES: PageDescriptor[] = [
  {
    id: "home",
    label: "Home",
    description:
      "The Catalyst IQ overview - what it is, how it works, what powers it, and where to start.",
  },
  {
    id: "markets",
    label: "Market Analysis",
    description:
      "The daily macro dashboard - index levels, sector ranking, catalysts, and investor behavior analysis.",
  },
  {
    id: "analysis",
    label: "Investment Strategy",
    description:
      "Today's highest-conviction opportunities, per-ticker research, and your own trade journal and performance.",
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
];
