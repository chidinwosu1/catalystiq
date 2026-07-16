export type PageId = "trade" | "portfolio" | "markets" | "analysis";

export const PAGES: { id: PageId; label: string }[] = [
  { id: "trade", label: "Trade" },
  { id: "portfolio", label: "Portfolio" },
  { id: "markets", label: "Markets" },
  { id: "analysis", label: "Analysis" },
];
