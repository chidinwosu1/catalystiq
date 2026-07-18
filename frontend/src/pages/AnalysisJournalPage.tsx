import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, Loader2, Plus, Send } from "lucide-react";
import {
  ApiError,
  getQuote,
  getTechnicalSnapshot,
  type IndicatorReading,
  type Quote,
  type TechnicalSnapshot,
} from "../lib/api";
import SectionCard from "../components/SectionCard";
import StatTile from "../components/StatTile";
import DemoBadge from "../components/DemoBadge";
import RatingBadge from "../components/RatingBadge";
import ProbabilityBar from "../components/ProbabilityBar";
import ConfidenceMeter from "../components/ConfidenceMeter";
import NextAction from "../components/NextAction";
import { getDemoAnalysis, getDemoSetup } from "../mockAnalysisDetail";
import BehavioralAnalysisTable from "../components/BehavioralAnalysisTable";
import ConvictionOpportunities from "../components/dashboard/ConvictionOpportunities";
import WorkflowBar from "../components/trade/WorkflowBar";
import { getStockBehavioralAnalysis } from "../mockBehavioralData";
import type { PageId } from "../types/nav";

interface AnalysisJournalPageProps {
  initialSymbol: string;
  onTrade: (symbol: string) => void;
  onNavigate: (page: PageId) => void;
}

interface JournalEntry {
  id: string;
  ticker: string;
  entryDate: string;
  entryPrice: number;
  exitDate: string;
  exitPrice: string;
  positionSize: number;
  tradeType: string;
  sector: string;
  thesis: string;
  exitReason: string;
  rulesFollowed: boolean;
  notes: string;
}

const TRADE_TYPES = ["Day", "Swing", "Long-term"];

function money(n: number): string {
  return n.toLocaleString("en-US", { style: "currency", currency: "USD" });
}

function emptyDraft(ticker: string): Omit<JournalEntry, "id"> {
  return {
    ticker,
    entryDate: new Date().toISOString().slice(0, 10),
    entryPrice: 0,
    exitDate: "",
    exitPrice: "",
    positionSize: 0,
    tradeType: "Swing",
    sector: "",
    thesis: "",
    exitReason: "",
    rulesFollowed: true,
    notes: "",
  };
}

// SetupSnapshot fields superseded by real, computed values in the Technical
// Indicators section below (rsi/macd/movingAverages/volatility/volume).
const REPLACED_SETUP_KEYS = new Set(["rsi", "macd", "movingAverages", "volatility", "volume"]);

const INDICATOR_LABELS: Record<string, string> = {
  sma_20: "20-day SMA",
  sma_50: "50-day SMA",
  sma_100: "100-day SMA",
  sma_200: "200-day SMA",
  price_vs_sma_50_pct: "Price vs. 50-day SMA",
  sma_50_slope_10d_pct: "50-day SMA slope (10d)",
  rsi_14: "RSI (14)",
  macd_line: "MACD line",
  macd_signal: "MACD signal",
  macd_histogram: "MACD histogram",
  bollinger_percent_b: "Bollinger %B",
  bollinger_bandwidth_pct: "Bollinger bandwidth",
  atr_14: "ATR (14)",
  atr_14_pct: "ATR (14), % of price",
  realized_volatility_20d_annualized_pct: "Realized volatility (20d, annualized)",
  relative_volume_20d_pct: "Relative volume (20d)",
  obv: "On-balance volume",
};

function ordinal(n: number): string {
  const rounded = Math.round(n);
  const suffixes = ["th", "st", "nd", "rd"];
  const v = rounded % 100;
  return `${rounded}${suffixes[(v - 20) % 10] ?? suffixes[v] ?? suffixes[0]}`;
}

function formatIndicatorValue(reading: IndicatorReading): string {
  if (reading.status === "insufficient_data" || reading.value === null) {
    return `Insufficient history (needs ${reading.min_bars_required}+ bars)`;
  }
  const v = reading.value;
  if (reading.name.startsWith("sma_") || reading.name.startsWith("macd_") || reading.name === "atr_14") {
    return money(v);
  }
  if (reading.name === "obv") {
    return v.toLocaleString("en-US", { maximumFractionDigits: 0 });
  }
  if (reading.name.endsWith("_pct")) {
    return `${v.toFixed(2)}%`;
  }
  return v.toFixed(1);
}

export default function AnalysisJournalPage({
  initialSymbol,
  onTrade,
  onNavigate,
}: AnalysisJournalPageProps) {
  const [symbolInput, setSymbolInput] = useState(initialSymbol || "AAPL");
  const [symbol, setSymbol] = useState((initialSymbol || "AAPL").toUpperCase());
  const [quote, setQuote] = useState<Quote | null>(null);
  const [quoteLoading, setQuoteLoading] = useState(false);
  const [quoteError, setQuoteError] = useState<ApiError | null>(null);

  const [technical, setTechnical] = useState<TechnicalSnapshot | null>(null);
  const [technicalLoading, setTechnicalLoading] = useState(false);
  const [technicalError, setTechnicalError] = useState<ApiError | null>(null);

  const [entries, setEntries] = useState<JournalEntry[]>([]);
  const [draft, setDraft] = useState(emptyDraft(symbol));
  const [showForm, setShowForm] = useState(false);

  useEffect(() => {
    if (initialSymbol) {
      setSymbolInput(initialSymbol);
      setSymbol(initialSymbol.toUpperCase());
    }
  }, [initialSymbol]);

  useEffect(() => {
    if (!symbol) return;
    let cancelled = false;
    setQuoteLoading(true);
    setQuoteError(null);
    getQuote(symbol)
      .then((q) => !cancelled && setQuote(q))
      .catch((err: unknown) => {
        if (cancelled) return;
        setQuote(null);
        setQuoteError(err instanceof ApiError ? err : new ApiError(0, "Unexpected error."));
      })
      .finally(() => !cancelled && setQuoteLoading(false));
    return () => {
      cancelled = true;
    };
  }, [symbol]);

  useEffect(() => {
    if (!symbol) return;
    let cancelled = false;
    setTechnicalLoading(true);
    setTechnicalError(null);
    getTechnicalSnapshot(symbol)
      .then((snap) => !cancelled && setTechnical(snap))
      .catch((err: unknown) => {
        if (cancelled) return;
        setTechnical(null);
        setTechnicalError(err instanceof ApiError ? err : new ApiError(0, "Unexpected error."));
      })
      .finally(() => !cancelled && setTechnicalLoading(false));
    return () => {
      cancelled = true;
    };
  }, [symbol]);

  const demoAnalysis = useMemo(() => getDemoAnalysis(symbol), [symbol]);
  const demoSetup = useMemo(() => getDemoSetup(symbol), [symbol]);
  const demoBehavioral = useMemo(() => getStockBehavioralAnalysis(symbol), [symbol]);

  function handleSymbolSubmit(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key !== "Enter") return;
    setSymbol(symbolInput.trim().toUpperCase());
  }

  function selectSymbol(sym: string) {
    const upper = sym.trim().toUpperCase();
    setSymbolInput(upper);
    setSymbol(upper);
    if (typeof window !== "undefined") window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function addEntry() {
    setEntries((prev) => [{ ...draft, id: crypto.randomUUID() }, ...prev]);
    setDraft(emptyDraft(symbol));
    setShowForm(false);
  }

  const closedTrades = useMemo(
    () =>
      entries
        .filter((e) => e.exitDate && e.exitPrice)
        .map((e) => {
          const pl = (Number(e.exitPrice) - e.entryPrice) * e.positionSize;
          const holdingDays = Math.max(
            0,
            Math.round(
              (new Date(e.exitDate).getTime() - new Date(e.entryDate).getTime()) /
                (1000 * 60 * 60 * 24)
            )
          );
          return { ...e, pl, holdingDays };
        }),
    [entries]
  );

  const performance = useMemo(() => {
    if (closedTrades.length === 0) return null;
    const wins = closedTrades.filter((t) => t.pl > 0);
    const losses = closedTrades.filter((t) => t.pl < 0);
    const winRate = (wins.length / closedTrades.length) * 100;
    const avgGain = wins.length ? wins.reduce((s, t) => s + t.pl, 0) / wins.length : 0;
    const avgLoss = losses.length ? losses.reduce((s, t) => s + t.pl, 0) / losses.length : 0;
    const grossGain = wins.reduce((s, t) => s + t.pl, 0);
    const grossLoss = Math.abs(losses.reduce((s, t) => s + t.pl, 0));
    const profitFactor = grossLoss > 0 ? grossGain / grossLoss : grossGain > 0 ? Infinity : 0;
    const best = closedTrades.reduce((a, b) => (b.pl > a.pl ? b : a));
    const worst = closedTrades.reduce((a, b) => (b.pl < a.pl ? b : a));
    const avgHolding =
      closedTrades.reduce((s, t) => s + t.holdingDays, 0) / closedTrades.length;

    const byType = new Map<string, { count: number; pl: number }>();
    for (const t of closedTrades) {
      const entry = byType.get(t.tradeType) ?? { count: 0, pl: 0 };
      entry.count += 1;
      entry.pl += t.pl;
      byType.set(t.tradeType, entry);
    }

    return { winRate, avgGain, avgLoss, profitFactor, best, worst, avgHolding, byType };
  }, [closedTrades]);

  return (
    <div className="space-y-6">
      <WorkflowBar current={3} onNavigate={onNavigate} />
      <div>
        <h1 className="text-xl font-semibold text-ink-primary">Investment Strategy</h1>
        <p className="mt-1 text-sm text-ink-secondary">
          Start from today's highest-conviction opportunities, research a ticker, then log and
          review your own trades.
        </p>
      </div>

      <ConvictionOpportunities onReview={selectSymbol} />

      <SectionCard
        title="Stock Analysis"
        action={<DemoBadge />}
        description="Price and the Technical Indicators section below are real. Rating, probability, confidence, and the remaining setup fields are illustrative until the full scoring model is built."
      >
        <input
          type="text"
          value={symbolInput}
          onChange={(e) => setSymbolInput(e.target.value)}
          onKeyDown={handleSymbolSubmit}
          placeholder="Search ticker, press Enter…"
          className="w-full rounded-lg border border-border bg-surface-2 px-3 py-2 text-sm text-ink-primary placeholder:text-ink-muted focus:border-brand-blue/50 focus:outline-none"
        />

        {quoteLoading && (
          <div className="mt-3 flex items-center gap-2 text-sm text-ink-secondary">
            <Loader2 size={14} className="animate-spin" /> Fetching price…
          </div>
        )}
        {quoteError && (
          <div className="mt-3 flex items-start gap-2 rounded-lg border border-status-critical/40 bg-status-critical-soft px-3 py-2 text-xs text-status-critical">
            <AlertTriangle size={13} className="mt-0.5 shrink-0" />
            <span>{quoteError.message}</span>
          </div>
        )}

        <div className="mt-4 flex items-start justify-between gap-3">
          <div>
            <h3 className="text-lg font-medium text-ink-primary">{symbol}</h3>
            {quote && <p className="text-sm text-ink-secondary">{money(quote.price)} (live)</p>}
          </div>
          <RatingBadge rating={demoAnalysis.rating} />
        </div>

        <div className="mt-3">
          <ProbabilityBar probability={demoAnalysis.probability} />
        </div>
        <div className="mt-3">
          <ConfidenceMeter confidence={demoAnalysis.confidence} />
        </div>

        <div className="mt-4 grid grid-cols-2 gap-3 text-xs sm:grid-cols-3">
          {Object.entries(demoSetup)
            .filter(([key]) => !REPLACED_SETUP_KEYS.has(key))
            .map(([key, value]) => (
              <div key={key} className="rounded-lg border border-border px-3 py-2">
                <p className="uppercase tracking-wide text-ink-muted">
                  {key.replace(/([A-Z])/g, " $1")}
                </p>
                <p className="mt-0.5 text-ink-primary">{value}</p>
              </div>
            ))}
        </div>

        <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-3">
          <StatTile label="Expected move" value={demoAnalysis.expectedMove} />
          <StatTile label="Invalidation" value={demoAnalysis.invalidation} />
          {quote && <StatTile label="Entry reference" value={money(quote.price)} />}
        </div>

        <button
          onClick={() => onTrade(symbol)}
          className="mt-4 rounded-lg bg-brand-blue px-4 py-2 text-sm font-semibold text-white"
        >
          Trade {symbol}
        </button>
      </SectionCard>

      <SectionCard
        title="Technical Indicators"
        description="Real, computed from live price history - no invented numbers. Fields show &quot;Insufficient history&quot; instead of a guess when there isn't enough data yet."
      >
        {technicalLoading && (
          <div className="flex items-center gap-2 text-sm text-ink-secondary">
            <Loader2 size={14} className="animate-spin" /> Computing indicators…
          </div>
        )}
        {technicalError && (
          <div className="flex items-start gap-2 rounded-lg border border-status-critical/40 bg-status-critical-soft px-3 py-2 text-xs text-status-critical">
            <AlertTriangle size={13} className="mt-0.5 shrink-0" />
            <span>{technicalError.message}</span>
          </div>
        )}
        {technical && (
          <>
            {technical.warnings.length > 0 && (
              <div className="mb-3 flex items-start gap-2 rounded-lg border border-status-warning/40 bg-status-warning-soft px-3 py-2 text-xs text-status-warning">
                <AlertTriangle size={13} className="mt-0.5 shrink-0" />
                <span>{technical.warnings.join(" ")}</span>
              </div>
            )}
            <div className="grid grid-cols-2 gap-3 text-xs sm:grid-cols-3 lg:grid-cols-4">
              {technical.indicators.map((reading) => (
                <div key={reading.name} className="rounded-lg border border-border px-3 py-2">
                  <p className="uppercase tracking-wide text-ink-muted">
                    {INDICATOR_LABELS[reading.name] ?? reading.name}
                  </p>
                  <p
                    className={`mt-0.5 ${
                      reading.status === "insufficient_data" ? "text-ink-muted" : "text-ink-primary"
                    }`}
                  >
                    {formatIndicatorValue(reading)}
                  </p>
                  {reading.percentile_5y !== null && (
                    <p className="mt-0.5 text-[11px] text-ink-muted">
                      {ordinal(reading.percentile_5y)} percentile (5y)
                    </p>
                  )}
                </div>
              ))}
            </div>
          </>
        )}
      </SectionCard>

      <BehavioralAnalysisTable
        title={`Investor Functional Behavior Analysis — ${symbol}`}
        description="How investors are likely to react to this ticker's recent triggers, and what would push that reaction positively or negatively"
        rows={demoBehavioral}
      />

      <SectionCard
        title="Trade Journal"
        description="Stored locally in this session only - not yet saved to the backend"
        action={
          <button
            onClick={() => {
              setDraft(emptyDraft(symbol));
              setShowForm((v) => !v);
            }}
            className="flex items-center gap-1 text-xs font-medium text-brand-blue hover:underline"
          >
            <Plus size={13} /> Log a trade
          </button>
        }
      >
        {showForm && (
          <div className="mb-4 space-y-3 rounded-lg border border-border p-3">
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              <label className="text-xs text-ink-muted">
                Ticker
                <input
                  value={draft.ticker}
                  onChange={(e) => setDraft({ ...draft, ticker: e.target.value.toUpperCase() })}
                  className="mt-1 w-full rounded-lg border border-border bg-surface-2 px-2 py-1.5 text-sm text-ink-primary focus:outline-none"
                />
              </label>
              <label className="text-xs text-ink-muted">
                Position size
                <input
                  type="number"
                  value={draft.positionSize || ""}
                  onChange={(e) =>
                    setDraft({ ...draft, positionSize: Number(e.target.value) || 0 })
                  }
                  className="mt-1 w-full rounded-lg border border-border bg-surface-2 px-2 py-1.5 text-sm text-ink-primary focus:outline-none"
                />
              </label>
              <label className="text-xs text-ink-muted">
                Trade type
                <select
                  value={draft.tradeType}
                  onChange={(e) => setDraft({ ...draft, tradeType: e.target.value })}
                  className="mt-1 w-full rounded-lg border border-border bg-surface-2 px-2 py-1.5 text-sm text-ink-primary focus:outline-none"
                >
                  {TRADE_TYPES.map((t) => (
                    <option key={t} value={t}>
                      {t}
                    </option>
                  ))}
                </select>
              </label>
              <label className="text-xs text-ink-muted">
                Sector
                <input
                  value={draft.sector}
                  onChange={(e) => setDraft({ ...draft, sector: e.target.value })}
                  className="mt-1 w-full rounded-lg border border-border bg-surface-2 px-2 py-1.5 text-sm text-ink-primary focus:outline-none"
                />
              </label>
              <label className="text-xs text-ink-muted">
                Entry date
                <input
                  type="date"
                  value={draft.entryDate}
                  onChange={(e) => setDraft({ ...draft, entryDate: e.target.value })}
                  className="mt-1 w-full rounded-lg border border-border bg-surface-2 px-2 py-1.5 text-sm text-ink-primary focus:outline-none"
                />
              </label>
              <label className="text-xs text-ink-muted">
                Entry price
                <input
                  type="number"
                  value={draft.entryPrice || ""}
                  onChange={(e) =>
                    setDraft({ ...draft, entryPrice: Number(e.target.value) || 0 })
                  }
                  className="mt-1 w-full rounded-lg border border-border bg-surface-2 px-2 py-1.5 text-sm text-ink-primary focus:outline-none"
                />
              </label>
              <label className="text-xs text-ink-muted">
                Exit date
                <input
                  type="date"
                  value={draft.exitDate}
                  onChange={(e) => setDraft({ ...draft, exitDate: e.target.value })}
                  className="mt-1 w-full rounded-lg border border-border bg-surface-2 px-2 py-1.5 text-sm text-ink-primary focus:outline-none"
                />
              </label>
              <label className="text-xs text-ink-muted">
                Exit price
                <input
                  type="number"
                  value={draft.exitPrice}
                  onChange={(e) => setDraft({ ...draft, exitPrice: e.target.value })}
                  className="mt-1 w-full rounded-lg border border-border bg-surface-2 px-2 py-1.5 text-sm text-ink-primary focus:outline-none"
                />
              </label>
            </div>
            <label className="block text-xs text-ink-muted">
              Original thesis
              <textarea
                value={draft.thesis}
                onChange={(e) => setDraft({ ...draft, thesis: e.target.value })}
                rows={2}
                placeholder="Entered because healthcare was leading and UNH had an earnings catalyst."
                className="mt-1 w-full rounded-lg border border-border bg-surface-2 px-2 py-1.5 text-sm text-ink-primary focus:outline-none"
              />
            </label>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <label className="text-xs text-ink-muted">
                Exit reason
                <input
                  value={draft.exitReason}
                  onChange={(e) => setDraft({ ...draft, exitReason: e.target.value })}
                  className="mt-1 w-full rounded-lg border border-border bg-surface-2 px-2 py-1.5 text-sm text-ink-primary focus:outline-none"
                />
              </label>
              <label className="mt-1 flex items-center gap-2 self-end text-xs text-ink-secondary">
                <input
                  type="checkbox"
                  checked={draft.rulesFollowed}
                  onChange={(e) => setDraft({ ...draft, rulesFollowed: e.target.checked })}
                  className="accent-brand-blue"
                />
                Followed my trading rules
              </label>
            </div>
            <div className="flex gap-2">
              <button
                onClick={addEntry}
                disabled={!draft.ticker || !draft.positionSize || !draft.entryPrice}
                className="rounded-lg bg-brand-blue px-3 py-1.5 text-xs font-semibold text-white disabled:opacity-40"
              >
                Save entry
              </button>
              <button
                onClick={() => setShowForm(false)}
                className="rounded-lg border border-border px-3 py-1.5 text-xs font-medium text-ink-secondary"
              >
                Cancel
              </button>
            </div>
          </div>
        )}

        {entries.length === 0 ? (
          <p className="text-sm text-ink-secondary">No trades logged yet.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[720px] text-left text-sm">
              <thead>
                <tr className="border-b border-border text-xs uppercase tracking-wide text-ink-muted">
                  <th className="py-2 pr-3 font-medium">Ticker</th>
                  <th className="py-2 pr-3 font-medium">Entry</th>
                  <th className="py-2 pr-3 font-medium">Exit</th>
                  <th className="py-2 pr-3 font-medium">Size</th>
                  <th className="py-2 pr-3 font-medium">P/L</th>
                  <th className="py-2 pr-3 font-medium">Type</th>
                  <th className="py-2 font-medium">Rules?</th>
                </tr>
              </thead>
              <tbody>
                {entries.map((e) => {
                  const closed = e.exitDate && e.exitPrice;
                  const pl = closed ? (Number(e.exitPrice) - e.entryPrice) * e.positionSize : null;
                  return (
                    <tr key={e.id} className="border-b border-border last:border-0">
                      <td className="py-2.5 pr-3 font-medium text-ink-primary">{e.ticker}</td>
                      <td className="py-2.5 pr-3 text-ink-secondary">
                        {e.entryDate} @ {money(e.entryPrice)}
                      </td>
                      <td className="py-2.5 pr-3 text-ink-secondary">
                        {closed ? `${e.exitDate} @ ${money(Number(e.exitPrice))}` : "Open"}
                      </td>
                      <td className="py-2.5 pr-3 text-ink-secondary">{e.positionSize}</td>
                      <td
                        className={`py-2.5 pr-3 font-medium ${
                          pl === null
                            ? "text-ink-muted"
                            : pl >= 0
                              ? "text-status-good"
                              : "text-status-critical"
                        }`}
                      >
                        {pl === null ? "—" : money(pl)}
                      </td>
                      <td className="py-2.5 pr-3 text-ink-secondary">{e.tradeType}</td>
                      <td className="py-2.5 text-ink-secondary">{e.rulesFollowed ? "Yes" : "No"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </SectionCard>

      <SectionCard
        title="Performance Analytics"
        description="Am I actually following a profitable process? Computed from your own logged trades above."
      >
        {!performance ? (
          <p className="text-sm text-ink-secondary">
            Log at least one closed trade (with an exit date and price) to see your stats here.
          </p>
        ) : (
          <>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              <StatTile label="Win rate" value={`${performance.winRate.toFixed(0)}%`} />
              <StatTile label="Avg gain" value={money(performance.avgGain)} tone="good" />
              <StatTile label="Avg loss" value={money(performance.avgLoss)} tone="critical" />
              <StatTile
                label="Profit factor"
                value={
                  performance.profitFactor === Infinity
                    ? "∞"
                    : performance.profitFactor.toFixed(2)
                }
              />
              <StatTile
                label="Best trade"
                value={`${performance.best.ticker} ${money(performance.best.pl)}`}
                tone="good"
              />
              <StatTile
                label="Worst trade"
                value={`${performance.worst.ticker} ${money(performance.worst.pl)}`}
                tone="critical"
              />
              <StatTile
                label="Avg holding period"
                value={`${performance.avgHolding.toFixed(1)} days`}
              />
              <StatTile label="Closed trades" value={closedTrades.length} />
            </div>

            <div className="mt-4">
              <p className="mb-2 text-xs font-medium uppercase tracking-wide text-ink-muted">
                Performance by trade type
              </p>
              <div className="space-y-1.5">
                {[...performance.byType.entries()].map(([type, stats]) => (
                  <div
                    key={type}
                    className="flex items-center justify-between rounded-lg border border-border px-3 py-2 text-sm"
                  >
                    <span className="text-ink-primary">
                      {type} <span className="text-ink-muted">({stats.count})</span>
                    </span>
                    <span
                      className={stats.pl >= 0 ? "text-status-good" : "text-status-critical"}
                    >
                      {money(stats.pl)}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          </>
        )}
      </SectionCard>

      <NextAction
        step="Next step · Place the trade"
        prompt={`Thesis confirmed for ${symbol}? Take the setup to the trade ticket and set your risk controls.`}
        label={`Trade ${symbol}`}
        icon={<Send size={15} />}
        onClick={() => onTrade(symbol)}
        secondary={{ label: "Back to market scan", onClick: () => onNavigate("markets") }}
      />
    </div>
  );
}
