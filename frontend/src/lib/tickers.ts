// The set of stocks/ETFs the ticker search offers as suggestions. Live data
// (quotes, fundamentals, technicals) works for any valid symbol, so the search
// also lets you confirm a typed symbol that isn't on this list — this is just
// the curated "supported" shortlist shown in the dropdown.
export interface Ticker {
  symbol: string;
  name: string;
}

export const SUPPORTED_TICKERS: Ticker[] = [
  { symbol: "AAPL", name: "Apple Inc." },
  { symbol: "MSFT", name: "Microsoft Corp." },
  { symbol: "NVDA", name: "NVIDIA Corp." },
  { symbol: "GOOGL", name: "Alphabet Inc. (Class A)" },
  { symbol: "AMZN", name: "Amazon.com Inc." },
  { symbol: "META", name: "Meta Platforms Inc." },
  { symbol: "TSLA", name: "Tesla Inc." },
  { symbol: "AVGO", name: "Broadcom Inc." },
  { symbol: "JPM", name: "JPMorgan Chase & Co." },
  { symbol: "UNH", name: "UnitedHealth Group Inc." },
  { symbol: "V", name: "Visa Inc." },
  { symbol: "MA", name: "Mastercard Inc." },
  { symbol: "JNJ", name: "Johnson & Johnson" },
  { symbol: "XOM", name: "Exxon Mobil Corp." },
  { symbol: "WMT", name: "Walmart Inc." },
  { symbol: "PG", name: "Procter & Gamble Co." },
  { symbol: "HD", name: "The Home Depot Inc." },
  { symbol: "COST", name: "Costco Wholesale Corp." },
  { symbol: "ORCL", name: "Oracle Corp." },
  { symbol: "AMD", name: "Advanced Micro Devices Inc." },
  { symbol: "CRM", name: "Salesforce Inc." },
  { symbol: "NFLX", name: "Netflix Inc." },
  { symbol: "ADBE", name: "Adobe Inc." },
  { symbol: "BAC", name: "Bank of America Corp." },
  { symbol: "KO", name: "The Coca-Cola Co." },
  { symbol: "PEP", name: "PepsiCo Inc." },
  { symbol: "DIS", name: "The Walt Disney Co." },
  { symbol: "INTC", name: "Intel Corp." },
  { symbol: "CSCO", name: "Cisco Systems Inc." },
  { symbol: "QCOM", name: "Qualcomm Inc." },
  { symbol: "TXN", name: "Texas Instruments Inc." },
  { symbol: "PFE", name: "Pfizer Inc." },
  { symbol: "MRK", name: "Merck & Co. Inc." },
  { symbol: "ABBV", name: "AbbVie Inc." },
  { symbol: "LLY", name: "Eli Lilly & Co." },
  { symbol: "WFC", name: "Wells Fargo & Co." },
  { symbol: "GS", name: "The Goldman Sachs Group Inc." },
  { symbol: "MS", name: "Morgan Stanley" },
  { symbol: "BA", name: "The Boeing Co." },
  { symbol: "CAT", name: "Caterpillar Inc." },
  { symbol: "GE", name: "GE Aerospace" },
  { symbol: "SPY", name: "SPDR S&P 500 ETF Trust" },
  { symbol: "QQQ", name: "Invesco QQQ Trust" },
  { symbol: "IWM", name: "iShares Russell 2000 ETF" },
  { symbol: "DIA", name: "SPDR Dow Jones Industrial Average ETF" },
];

/** Case-insensitive match on symbol or company name. */
export function filterTickers(query: string): Ticker[] {
  const q = query.trim().toLowerCase();
  if (!q) return SUPPORTED_TICKERS;
  const starts: Ticker[] = [];
  const contains: Ticker[] = [];
  for (const t of SUPPORTED_TICKERS) {
    const sym = t.symbol.toLowerCase();
    const name = t.name.toLowerCase();
    if (sym.startsWith(q) || name.startsWith(q)) starts.push(t);
    else if (sym.includes(q) || name.includes(q)) contains.push(t);
  }
  return [...starts, ...contains];
}
