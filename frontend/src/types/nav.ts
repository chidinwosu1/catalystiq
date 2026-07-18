// "markets" (Market Overview) and "ticket" (order entry) are real pages but
// intentionally not in the primary nav: Market Overview opens as a slide-out
// inside the Trade Center, and the order ticket is reached as the "Confirm
// Trade" step from an opportunity or from Investment Strategy.
export type PageId =
  | "home"
  | "trade"
  | "portfolio"
  | "markets"
  | "analysis"
  | "ticket"
  | "preferences";

export interface PageDescriptor {
  id: PageId;
  label: string;
  description: string;
}

// Primary navigation — the investing workflow: discover, evaluate, decide, manage.
export const PAGES: PageDescriptor[] = [
  {
    id: "home",
    label: "Home",
    description:
      "The Catalyst IQ overview - what it is, how it works, what powers it, and where to start.",
  },
  {
    id: "trade",
    label: "Trade Center",
    description:
      "Discover and compare today's highest-conviction opportunities, with the market read a slide-out away.",
  },
  {
    id: "analysis",
    label: "Investment Strategy",
    description:
      "Turn an opportunity into a personalized strategy: research, journal, and your own performance.",
  },
  {
    id: "portfolio",
    label: "Portfolio",
    description:
      "Your real account balance and positions from the connected paper-trading broker.",
  },
];
