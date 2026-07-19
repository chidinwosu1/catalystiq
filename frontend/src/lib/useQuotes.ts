import { useEffect, useState } from "react";
import { getQuote } from "./api";

/**
 * Fetches live quotes for a set of symbols and returns a symbol→price map.
 * Best-effort: symbols whose quote can't be fetched are simply absent, so
 * callers fall back to their demo price. Re-runs when the symbol set changes.
 */
export function useQuotes(symbols: string[]): {
  prices: Record<string, number>;
  loading: boolean;
} {
  const key = symbols.join(",");
  const [prices, setPrices] = useState<Record<string, number>>({});
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    const list = key ? key.split(",") : [];
    if (list.length === 0) return;
    let cancelled = false;
    setLoading(true);
    Promise.all(
      list.map((sym) =>
        getQuote(sym)
          .then((q) => [sym, q.price] as const)
          .catch(() => null)
      )
    ).then((results) => {
      if (cancelled) return;
      const next: Record<string, number> = {};
      for (const r of results) if (r) next[r[0]] = r[1];
      setPrices(next);
      setLoading(false);
    });
    return () => {
      cancelled = true;
    };
  }, [key]);

  return { prices, loading };
}
