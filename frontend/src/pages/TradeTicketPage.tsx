import { useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  Briefcase,
  CheckCircle2,
  ChevronDown,
  Clock,
  Loader2,
  X,
} from "lucide-react";
import {
  ApiError,
  cancelScheduledOrder,
  getAccount,
  getFundamentals,
  getPositions,
  getQuote,
  getScheduledOrders,
  scheduleOrder,
  submitOrder,
  type AccountInfo,
  type FundamentalsSnapshot,
  type NewOrder,
  type OrderType,
  type Position,
  type Quote,
  type ScheduledOrderRecord,
  type TimeInForce,
} from "../lib/api";
import SectionCard from "../components/SectionCard";
import NextAction from "../components/NextAction";
import type { PageId } from "../types/nav";

interface TradeTicketPageProps {
  initialSymbol: string;
  onViewAnalysis: (symbol: string) => void;
  onNavigate: (page: PageId) => void;
}

type TradingStyle = "day" | "swing";
type AssetType = "stocks" | "futures" | "options";

const ORDER_TYPES: { id: OrderType; label: string; description: string }[] = [
  { id: "market", label: "Market", description: "Executes at the best available price" },
  { id: "limit", label: "Limit", description: "You set the highest buy / lowest sell price" },
  { id: "stop", label: "Stop", description: "Activates once the stop price is reached" },
  { id: "stop_limit", label: "Stop-Limit", description: "Stop price triggers a limit order" },
  { id: "trailing_stop", label: "Trailing Stop", description: "Trails price by a % or $ amount" },
];

const TIF_OPTIONS: { id: TimeInForce; label: string }[] = [
  { id: "day", label: "Day" },
  { id: "gtc", label: "Good Til Canceled" },
  { id: "ioc", label: "Immediate or Cancel" },
  { id: "fok", label: "Fill or Kill" },
];

const ASSET_TYPES: { id: AssetType; label: string; supported: boolean }[] = [
  { id: "stocks", label: "Stocks", supported: true },
  { id: "futures", label: "Futures", supported: false },
  { id: "options", label: "Options", supported: false },
];

const DEFAULT_TIME_PERIOD: Record<TradingStyle, string> = {
  day: "Same session",
  swing: "2-10 days",
};

function money(n: number): string {
  return n.toLocaleString("en-US", { style: "currency", currency: "USD" });
}

function formatCountdown(msRemaining: number): string {
  if (msRemaining <= 0) return "Executing…";
  const totalSeconds = Math.floor(msRemaining / 1000);
  const h = Math.floor(totalSeconds / 3600);
  const m = Math.floor((totalSeconds % 3600) / 60);
  const s = totalSeconds % 60;
  if (h > 0) return `in ${h}h ${m}m ${s}s`;
  if (m > 0) return `in ${m}m ${s}s`;
  return `in ${s}s`;
}

const STATUS_CLASS: Record<string, string> = {
  pending: "text-brand-blue",
  submitted: "text-status-good",
  failed: "text-status-critical",
  cancelled: "text-ink-muted",
};

export default function TradeTicketPage({
  initialSymbol,
  onViewAnalysis,
  onNavigate,
}: TradeTicketPageProps) {
  const [symbolInput, setSymbolInput] = useState(initialSymbol);
  const [symbol, setSymbol] = useState(initialSymbol.trim().toUpperCase());

  const [quote, setQuote] = useState<Quote | null>(null);
  const [fundamentals, setFundamentals] = useState<FundamentalsSnapshot | null>(null);
  const [quoteLoading, setQuoteLoading] = useState(false);
  const [quoteError, setQuoteError] = useState<ApiError | null>(null);

  const [account, setAccount] = useState<AccountInfo | null>(null);
  const [positions, setPositions] = useState<Position[]>([]);

  const [tradingStyle, setTradingStyle] = useState<TradingStyle>("swing");
  const [timePeriod, setTimePeriod] = useState(DEFAULT_TIME_PERIOD.swing);
  const [assetType, setAssetType] = useState<AssetType>("stocks");

  const [side, setSide] = useState<"buy" | "sell">("buy");
  const [qty, setQty] = useState("10");
  const [orderType, setOrderType] = useState<OrderType>("market");
  const [timeInForce, setTimeInForce] = useState<TimeInForce>("day");
  const [showAdvancedTif, setShowAdvancedTif] = useState(false);

  const [limitPrice, setLimitPrice] = useState("");
  const [stopPrice, setStopPrice] = useState("");
  const [trailPercent, setTrailPercent] = useState("2");

  const [showRiskControls, setShowRiskControls] = useState(false);
  const [takeProfitPct, setTakeProfitPct] = useState("");
  const [stopLossPct, setStopLossPct] = useState("");
  const [extendedHours, setExtendedHours] = useState(false);

  const [executionMode, setExecutionMode] = useState<"now" | "scheduled">("now");
  const [scheduledAt, setScheduledAt] = useState("");
  const [scheduledOrders, setScheduledOrders] = useState<ScheduledOrderRecord[]>([]);
  const [nowTick, setNowTick] = useState(() => Date.now());

  const [reviewing, setReviewing] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<ApiError | null>(null);
  const [submitResult, setSubmitResult] = useState<Record<string, unknown> | null>(null);
  const [scheduledResult, setScheduledResult] = useState<ScheduledOrderRecord | null>(null);

  useEffect(() => {
    setSymbolInput(initialSymbol);
    setSymbol(initialSymbol.trim().toUpperCase());
  }, [initialSymbol]);

  useEffect(() => {
    getAccount()
      .then(setAccount)
      .catch(() => setAccount(null));
    getPositions()
      .then(setPositions)
      .catch(() => setPositions([]));
  }, []);

  function refreshScheduledOrders() {
    getScheduledOrders()
      .then(setScheduledOrders)
      .catch(() => {
        /* non-fatal - the list section just stays empty */
      });
  }

  useEffect(() => {
    refreshScheduledOrders();
    const interval = setInterval(refreshScheduledOrders, 10_000);
    return () => clearInterval(interval);
  }, []);

  useEffect(() => {
    const interval = setInterval(() => setNowTick(Date.now()), 1000);
    return () => clearInterval(interval);
  }, []);

  useEffect(() => {
    if (!symbol) {
      setQuote(null);
      setFundamentals(null);
      return;
    }
    let cancelled = false;
    setQuoteLoading(true);
    setQuoteError(null);

    Promise.all([getQuote(symbol), getFundamentals(symbol)])
      .then(([q, f]) => {
        if (cancelled) return;
        setQuote(q);
        setFundamentals(f);
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setQuote(null);
        setFundamentals(null);
        setQuoteError(error instanceof ApiError ? error : new ApiError(0, "Unexpected error."));
      })
      .finally(() => {
        if (!cancelled) setQuoteLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [symbol]);

  function handleSymbolSubmit(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key !== "Enter") return;
    setReviewing(false);
    setSubmitResult(null);
    setSubmitError(null);
    setSymbol(symbolInput.trim().toUpperCase());
  }

  function handleTradingStyleChange(style: TradingStyle) {
    setTradingStyle(style);
    setTimePeriod(DEFAULT_TIME_PERIOD[style]);
  }

  const currentPosition = useMemo(
    () => positions.find((p) => p.symbol.toUpperCase() === symbol),
    [positions, symbol]
  );

  const referencePrice = useMemo(() => {
    if (orderType === "limit" || orderType === "stop_limit") {
      return parseFloat(limitPrice) || quote?.price || 0;
    }
    return quote?.price ?? 0;
  }, [orderType, limitPrice, quote]);

  const qtyNum = parseFloat(qty) || 0;
  const estimatedValue = qtyNum * referencePrice;

  const takeProfitPrice = useMemo(() => {
    const pct = parseFloat(takeProfitPct);
    if (!pct || !referencePrice) return null;
    const direction = side === "buy" ? 1 : -1;
    return referencePrice * (1 + (direction * pct) / 100);
  }, [takeProfitPct, referencePrice, side]);

  const stopLossPrice = useMemo(() => {
    const pct = parseFloat(stopLossPct);
    if (!pct || !referencePrice) return null;
    const direction = side === "buy" ? -1 : 1;
    return referencePrice * (1 + (direction * pct) / 100);
  }, [stopLossPct, referencePrice, side]);

  function buildOrder(): NewOrder {
    const order: NewOrder = {
      symbol,
      side,
      type: orderType,
      time_in_force: timeInForce,
      qty: qtyNum,
      extended_hours: extendedHours,
    };
    if (orderType === "limit" || orderType === "stop_limit") {
      order.limit_price = parseFloat(limitPrice) || undefined;
    }
    if (orderType === "stop" || orderType === "stop_limit") {
      order.stop_price = parseFloat(stopPrice) || undefined;
    }
    if (orderType === "trailing_stop") {
      order.trail_percent = parseFloat(trailPercent) || undefined;
    }
    if (takeProfitPrice) order.take_profit_price = Number(takeProfitPrice.toFixed(2));
    if (stopLossPrice) order.stop_loss_price = Number(stopLossPrice.toFixed(2));
    return order;
  }

  async function handleSubmit() {
    setSubmitting(true);
    setSubmitError(null);
    try {
      if (executionMode === "scheduled") {
        const when = new Date(scheduledAt);
        const record = await scheduleOrder(buildOrder(), when);
        setScheduledResult(record);
        refreshScheduledOrders();
      } else {
        const result = await submitOrder(buildOrder());
        setSubmitResult(result);
      }
      setReviewing(false);
    } catch (error) {
      setSubmitError(error instanceof ApiError ? error : new ApiError(0, "Unexpected error."));
    } finally {
      setSubmitting(false);
    }
  }

  async function handleCancelScheduled(id: number) {
    try {
      await cancelScheduledOrder(id);
      refreshScheduledOrders();
    } catch {
      // Non-fatal - the row's own status will just remain as-is until refresh.
    }
  }

  const scheduledDate = executionMode === "scheduled" && scheduledAt ? new Date(scheduledAt) : null;
  const scheduledValid = scheduledDate !== null && scheduledDate.getTime() > Date.now();

  const canReview =
    symbol.length > 0 &&
    qtyNum > 0 &&
    assetType === "stocks" &&
    (orderType !== "limit" || parseFloat(limitPrice) > 0) &&
    (orderType !== "stop" || parseFloat(stopPrice) > 0) &&
    (orderType !== "stop_limit" || (parseFloat(limitPrice) > 0 && parseFloat(stopPrice) > 0)) &&
    (orderType !== "trailing_stop" || parseFloat(trailPercent) > 0) &&
    (executionMode !== "scheduled" || scheduledValid);

  const priceChangePct =
    quote && quote.previous_close
      ? ((quote.price - quote.previous_close) / quote.previous_close) * 100
      : null;

  const pendingScheduled = scheduledOrders.filter((s) => s.status === "pending");

  return (
    <div className="mx-auto max-w-2xl space-y-5">
      <div>
        <h1 className="text-xl font-semibold text-ink-primary">Trade Ticket</h1>
        <p className="mt-1 text-sm text-ink-secondary">
          Submits a real paper order through the connected broker - not a simulation of a
          simulation.
        </p>
      </div>

      <SectionCard title="Trade Setup">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <div>
            <p className="mb-1.5 text-xs text-ink-muted">Trading style</p>
            <div className="flex rounded-lg border border-border p-1">
              {(["day", "swing"] as const).map((s) => (
                <button
                  key={s}
                  onClick={() => handleTradingStyleChange(s)}
                  className={`flex-1 rounded-md py-1.5 text-sm font-medium capitalize transition-colors ${
                    tradingStyle === s
                      ? "bg-surface-2 text-ink-primary"
                      : "text-ink-secondary hover:text-ink-primary"
                  }`}
                >
                  {s} trading
                </button>
              ))}
            </div>
          </div>
          <label className="text-xs text-ink-muted">
            Time period
            <input
              type="text"
              value={timePeriod}
              onChange={(e) => setTimePeriod(e.target.value)}
              className="mt-1 w-full rounded-lg border border-border bg-surface-2 px-3 py-2 text-sm text-ink-primary focus:border-brand-blue/50 focus:outline-none"
            />
          </label>
        </div>

        <div className="mt-3">
          <p className="mb-1.5 text-xs text-ink-muted">Asset type</p>
          <div className="flex gap-2">
            {ASSET_TYPES.map((a) => (
              <button
                key={a.id}
                disabled={!a.supported}
                onClick={() => setAssetType(a.id)}
                title={a.supported ? undefined : "Coming soon - not tradeable yet"}
                className={`rounded-full border px-3 py-1 text-xs font-medium transition-colors ${
                  assetType === a.id
                    ? "border-brand-blue/50 bg-brand-blue/10 text-ink-primary"
                    : "border-border text-ink-secondary hover:text-ink-primary"
                } ${!a.supported ? "cursor-not-allowed opacity-40" : ""}`}
              >
                {a.label}
                {!a.supported && " (soon)"}
              </button>
            ))}
          </div>
        </div>
      </SectionCard>

      <SectionCard title="Ticker">
        <input
          type="text"
          placeholder="Search ticker, press Enter…"
          value={symbolInput}
          onChange={(e) => setSymbolInput(e.target.value)}
          onKeyDown={handleSymbolSubmit}
          className="w-full rounded-lg border border-border bg-surface-2 px-3 py-2 text-sm text-ink-primary placeholder:text-ink-muted focus:border-brand-blue/50 focus:outline-none"
        />

        {quoteLoading && (
          <div className="mt-3 flex items-center gap-2 text-sm text-ink-secondary">
            <Loader2 size={14} className="animate-spin" /> Fetching quote…
          </div>
        )}

        {quoteError && (
          <div className="mt-3 flex items-start gap-2 rounded-lg border border-status-critical/40 bg-status-critical-soft px-3 py-2 text-xs text-status-critical">
            <AlertTriangle size={13} className="mt-0.5 shrink-0" />
            <span>{quoteError.message}</span>
          </div>
        )}

        {quote && !quoteLoading && (
          <div className="mt-3 flex items-end justify-between">
            <div>
              <p className="text-sm text-ink-primary">
                {fundamentals?.long_name ?? symbol}
                {fundamentals?.long_name && (
                  <span className="text-ink-secondary"> · {symbol}</span>
                )}
              </p>
              {currentPosition && (
                <p className="mt-0.5 text-xs text-ink-muted">
                  Current position: {currentPosition.qty} shares @{" "}
                  {money(Number(currentPosition.avg_entry_price))}
                </p>
              )}
            </div>
            <div className="text-right">
              <p className="text-lg font-semibold text-ink-primary">{money(quote.price)}</p>
              {priceChangePct !== null && (
                <p
                  className={`text-xs font-medium ${
                    priceChangePct >= 0 ? "text-status-good" : "text-status-critical"
                  }`}
                >
                  {priceChangePct >= 0 ? "+" : ""}
                  {priceChangePct.toFixed(2)}%
                </p>
              )}
            </div>
          </div>
        )}
      </SectionCard>

      <SectionCard title="Action & Quantity">
        <div className="grid grid-cols-2 gap-3">
          <div className="flex rounded-lg border border-border p-1">
            {(["buy", "sell"] as const).map((s) => (
              <button
                key={s}
                onClick={() => setSide(s)}
                className={`flex-1 rounded-md py-1.5 text-sm font-semibold capitalize transition-colors ${
                  side === s
                    ? s === "buy"
                      ? "bg-status-good-soft text-status-good"
                      : "bg-status-critical-soft text-status-critical"
                    : "text-ink-secondary hover:text-ink-primary"
                }`}
              >
                {s}
              </button>
            ))}
          </div>
          <div>
            <input
              type="number"
              min={0}
              step="any"
              value={qty}
              onChange={(e) => setQty(e.target.value)}
              placeholder="Shares"
              className="w-full rounded-lg border border-border bg-surface-2 px-3 py-2 text-sm text-ink-primary focus:border-brand-blue/50 focus:outline-none"
            />
          </div>
        </div>

        <div className="mt-4 space-y-1.5 rounded-lg border border-border bg-surface-2 px-3 py-2.5 text-sm">
          <div className="flex justify-between">
            <span className="text-ink-secondary">Estimated order value</span>
            <span className="font-semibold text-ink-primary">
              {qtyNum || 0} × {money(referencePrice)} = {money(estimatedValue)}
            </span>
          </div>
          {account && (
            <div className="flex justify-between text-xs text-ink-muted">
              <span>Estimated buying power remaining</span>
              <span>
                {money(
                  Math.max(0, Number(account.buying_power) - (side === "buy" ? estimatedValue : 0))
                )}
              </span>
            </div>
          )}
        </div>
      </SectionCard>

      <SectionCard title="Order Type">
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          {ORDER_TYPES.map((t) => (
            <button
              key={t.id}
              onClick={() => setOrderType(t.id)}
              className={`rounded-lg border px-3 py-2.5 text-left transition-colors ${
                orderType === t.id
                  ? "border-brand-blue/50 bg-brand-blue/10"
                  : "border-border hover:border-border-strong"
              }`}
            >
              <p className="text-sm font-medium text-ink-primary">{t.label}</p>
              <p className="mt-0.5 text-xs text-ink-secondary">{t.description}</p>
            </button>
          ))}
        </div>

        <div className="mt-3 grid grid-cols-2 gap-3">
          {(orderType === "limit" || orderType === "stop_limit") && (
            <label className="text-xs text-ink-muted">
              Limit price
              <input
                type="number"
                step="any"
                value={limitPrice}
                onChange={(e) => setLimitPrice(e.target.value)}
                className="mt-1 w-full rounded-lg border border-border bg-surface-2 px-3 py-2 text-sm text-ink-primary focus:border-brand-blue/50 focus:outline-none"
              />
            </label>
          )}
          {(orderType === "stop" || orderType === "stop_limit") && (
            <label className="text-xs text-ink-muted">
              Stop price
              <input
                type="number"
                step="any"
                value={stopPrice}
                onChange={(e) => setStopPrice(e.target.value)}
                className="mt-1 w-full rounded-lg border border-border bg-surface-2 px-3 py-2 text-sm text-ink-primary focus:border-brand-blue/50 focus:outline-none"
              />
            </label>
          )}
          {orderType === "trailing_stop" && (
            <label className="text-xs text-ink-muted">
              Trailing amount (%)
              <input
                type="number"
                step="any"
                value={trailPercent}
                onChange={(e) => setTrailPercent(e.target.value)}
                className="mt-1 w-full rounded-lg border border-border bg-surface-2 px-3 py-2 text-sm text-ink-primary focus:border-brand-blue/50 focus:outline-none"
              />
            </label>
          )}
        </div>

        <button
          onClick={() => setShowAdvancedTif((v) => !v)}
          className="mt-4 flex items-center gap-1 text-xs font-medium text-ink-secondary hover:text-ink-primary"
        >
          <ChevronDown size={13} className={showAdvancedTif ? "rotate-180" : ""} />
          Time in force: {TIF_OPTIONS.find((t) => t.id === timeInForce)?.label}
        </button>
        {showAdvancedTif && (
          <div className="mt-2 flex flex-wrap gap-2">
            {TIF_OPTIONS.map((t) => (
              <button
                key={t.id}
                onClick={() => setTimeInForce(t.id)}
                className={`rounded-full border px-3 py-1 text-xs font-medium transition-colors ${
                  timeInForce === t.id
                    ? "border-brand-blue/50 bg-brand-blue/10 text-ink-primary"
                    : "border-border text-ink-secondary hover:text-ink-primary"
                }`}
              >
                {t.label}
              </button>
            ))}
          </div>
        )}
      </SectionCard>

      <SectionCard
        title="Risk Controls"
        description="Optional - automatically calculated exit prices"
        action={
          <button
            onClick={() => setShowRiskControls((v) => !v)}
            className="text-xs font-medium text-brand-blue hover:underline"
          >
            {showRiskControls ? "Hide" : "Add"}
          </button>
        }
      >
        {showRiskControls && (
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <label className="text-xs text-ink-muted">
                Take profit (%)
                <input
                  type="number"
                  step="any"
                  value={takeProfitPct}
                  onChange={(e) => setTakeProfitPct(e.target.value)}
                  placeholder="e.g. 5"
                  className="mt-1 w-full rounded-lg border border-border bg-surface-2 px-3 py-2 text-sm text-ink-primary focus:border-brand-blue/50 focus:outline-none"
                />
                {takeProfitPrice && (
                  <span className="mt-1 block text-status-good">
                    → {money(takeProfitPrice)}
                  </span>
                )}
              </label>
              <label className="text-xs text-ink-muted">
                Stop loss (%)
                <input
                  type="number"
                  step="any"
                  value={stopLossPct}
                  onChange={(e) => setStopLossPct(e.target.value)}
                  placeholder="e.g. 5"
                  className="mt-1 w-full rounded-lg border border-border bg-surface-2 px-3 py-2 text-sm text-ink-primary focus:border-brand-blue/50 focus:outline-none"
                />
                {stopLossPrice && (
                  <span className="mt-1 block text-status-critical">
                    → {money(stopLossPrice)}
                  </span>
                )}
              </label>
            </div>
            <p className="text-[11px] text-ink-muted">
              Setting both places a bracket order; setting just one places a one-triggers-other
              order.
            </p>
            <label className="flex items-center gap-2 text-xs text-ink-secondary">
              <input
                type="checkbox"
                checked={extendedHours}
                onChange={(e) => setExtendedHours(e.target.checked)}
                className="accent-brand-blue"
              />
              Allow extended-hours execution
            </label>
          </div>
        )}
      </SectionCard>

      <SectionCard title="When" description="Execute immediately, or schedule for a specific time">
        <div className="flex rounded-lg border border-border p-1">
          {(["now", "scheduled"] as const).map((mode) => (
            <button
              key={mode}
              onClick={() => setExecutionMode(mode)}
              className={`flex-1 rounded-md py-1.5 text-sm font-medium transition-colors ${
                executionMode === mode
                  ? "bg-surface-2 text-ink-primary"
                  : "text-ink-secondary hover:text-ink-primary"
              }`}
            >
              {mode === "now" ? "Execute now" : "Schedule for later"}
            </button>
          ))}
        </div>

        {executionMode === "scheduled" && (
          <div className="mt-3">
            <label className="text-xs text-ink-muted">
              Execution time
              <input
                type="datetime-local"
                value={scheduledAt}
                onChange={(e) => setScheduledAt(e.target.value)}
                className="mt-1 w-full rounded-lg border border-border bg-surface-2 px-3 py-2 text-sm text-ink-primary focus:border-brand-blue/50 focus:outline-none"
              />
            </label>
            {scheduledDate && (
              <p
                className={`mt-2 flex items-center gap-1.5 text-xs ${scheduledValid ? "text-brand-blue" : "text-status-critical"}`}
              >
                <Clock size={13} />
                {scheduledValid
                  ? `Will execute ${formatCountdown(scheduledDate.getTime() - nowTick)} (${scheduledDate.toLocaleString()})`
                  : "Pick a time in the future"}
              </p>
            )}
          </div>
        )}
      </SectionCard>

      {!reviewing && !submitResult && !scheduledResult && (
        <div className="flex gap-3">
          <button
            disabled={!canReview}
            onClick={() => setReviewing(true)}
            className="flex-1 rounded-lg bg-brand-blue px-4 py-2.5 text-sm font-semibold text-white transition-opacity disabled:opacity-40"
          >
            Review Order
          </button>
          {symbol && (
            <button
              onClick={() => onViewAnalysis(symbol)}
              className="rounded-lg border border-border px-4 py-2.5 text-sm font-medium text-ink-secondary hover:text-ink-primary"
            >
              View Analysis
            </button>
          )}
        </div>
      )}

      {reviewing && (
        <SectionCard title="Order Summary">
          <p className="text-sm text-ink-primary">
            {side === "buy" ? "Buy" : "Sell"} {qtyNum} shares of {symbol}
          </p>
          <p className="mt-1 text-sm text-ink-secondary">
            {ORDER_TYPES.find((t) => t.id === orderType)?.label} order ·{" "}
            {TIF_OPTIONS.find((t) => t.id === timeInForce)?.label} · {tradingStyle} trading (
            {timePeriod})
          </p>
          <p className="mt-1 text-sm text-ink-secondary">
            Estimated value: {money(estimatedValue)}
          </p>
          {takeProfitPrice && (
            <p className="mt-1 text-sm text-status-good">
              Take profit: {money(takeProfitPrice)}
            </p>
          )}
          {stopLossPrice && (
            <p className="mt-1 text-sm text-status-critical">Stop loss: {money(stopLossPrice)}</p>
          )}
          <p className="mt-1 flex items-center gap-1.5 text-sm text-ink-secondary">
            <Clock size={13} />
            {executionMode === "now"
              ? "Executes immediately on submit"
              : scheduledDate && `Scheduled for ${scheduledDate.toLocaleString()}`}
          </p>

          {submitError && (
            <div className="mt-3 flex items-start gap-2 rounded-lg border border-status-critical/40 bg-status-critical-soft px-3 py-2 text-xs text-status-critical">
              <AlertTriangle size={13} className="mt-0.5 shrink-0" />
              <span>{submitError.message}</span>
            </div>
          )}

          <div className="mt-4 flex gap-3">
            <button
              disabled={submitting}
              onClick={handleSubmit}
              className="flex flex-1 items-center justify-center gap-2 rounded-lg bg-brand-blue px-4 py-2.5 text-sm font-semibold text-white disabled:opacity-40"
            >
              {submitting && <Loader2 size={14} className="animate-spin" />}
              {executionMode === "now" ? "Submit Paper Trade" : "Schedule Paper Trade"}
            </button>
            <button
              onClick={() => setReviewing(false)}
              className="rounded-lg border border-border px-4 py-2.5 text-sm font-medium text-ink-secondary hover:text-ink-primary"
            >
              Cancel
            </button>
          </div>
        </SectionCard>
      )}

      {submitResult && (
        <SectionCard title="Order Submitted">
          <div className="flex items-start gap-2 text-sm text-status-good">
            <CheckCircle2 size={15} className="mt-0.5 shrink-0" />
            <span>Order sent to the broker. Check the Portfolio tab for status.</span>
          </div>
          <pre className="mt-3 max-h-40 overflow-auto rounded-lg bg-surface-2 p-3 text-[11px] text-ink-secondary">
            {JSON.stringify(submitResult, null, 2)}
          </pre>
          <button
            onClick={() => setSubmitResult(null)}
            className="mt-3 text-xs font-medium text-brand-blue hover:underline"
          >
            Place another order
          </button>
        </SectionCard>
      )}

      {scheduledResult && (
        <SectionCard title="Order Scheduled">
          <div className="flex items-start gap-2 text-sm text-brand-blue">
            <Clock size={15} className="mt-0.5 shrink-0" />
            <span>
              Queued - the backend will submit it automatically at{" "}
              {new Date(scheduledResult.scheduled_at).toLocaleString()}. See the pending list
              below.
            </span>
          </div>
          <button
            onClick={() => setScheduledResult(null)}
            className="mt-3 text-xs font-medium text-brand-blue hover:underline"
          >
            Place another order
          </button>
        </SectionCard>
      )}

      {pendingScheduled.length > 0 && (
        <SectionCard title="Pending Scheduled Orders">
          <div className="space-y-2">
            {pendingScheduled.map((s) => {
              const target = new Date(s.scheduled_at).getTime();
              return (
                <div
                  key={s.id}
                  className="flex items-center justify-between rounded-lg border border-border px-3 py-2.5 text-sm"
                >
                  <div>
                    <p className="font-medium text-ink-primary">
                      {s.order.side === "buy" ? "Buy" : "Sell"} {s.order.qty ?? ""} {s.symbol}
                    </p>
                    <p className={`mt-0.5 flex items-center gap-1 text-xs ${STATUS_CLASS[s.status]}`}>
                      <Clock size={12} />
                      {formatCountdown(target - nowTick)} ({new Date(s.scheduled_at).toLocaleString()})
                    </p>
                  </div>
                  <button
                    onClick={() => handleCancelScheduled(s.id)}
                    aria-label="Cancel scheduled order"
                    className="rounded-md p-1.5 text-ink-muted hover:bg-surface-2 hover:text-status-critical"
                  >
                    <X size={15} />
                  </button>
                </div>
              );
            })}
          </div>
        </SectionCard>
      )}

      <NextAction
        step="Next step · Monitor your position"
        prompt="Order placed? Track fills, live P/L, and risk alerts in your portfolio."
        label="View Portfolio"
        icon={<Briefcase size={15} />}
        onClick={() => onNavigate("portfolio")}
        secondary={{ label: "Scan the market", onClick: () => onNavigate("markets") }}
      />
    </div>
  );
}
