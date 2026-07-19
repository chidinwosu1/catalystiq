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
  confirmOrder,
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
  type OrderConfirmation,
  type OrderType,
  type Position,
  type Quote,
  type ScheduledOrderRecord,
  type TimeInForce,
} from "../lib/api";
import SectionCard from "../components/SectionCard";
import NextAction from "../components/NextAction";
import TickerSearch from "../components/TickerSearch";
import WorkflowBar from "../components/trade/WorkflowBar";
import type { PageId } from "../types/nav";
import {
  canReviewOrder,
  estimatedValue as computeEstimatedValue,
  formatQuoteAsOf,
  referencePrice as computeReferencePrice,
} from "../lib/tradeTicket";

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
  due: "text-status-warning",
  submitted: "text-status-good",
  failed: "text-status-critical",
  cancelled: "text-ink-muted",
};

export default function TradeTicketPage({
  initialSymbol,
  onViewAnalysis,
  onNavigate,
}: TradeTicketPageProps) {
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

  // The account the order is confirmed against (bound into the confirmation
  // token, §13). Single paper account for now.
  const [accountId, setAccountId] = useState("paper");

  const [reviewing, setReviewing] = useState(false); // scheduled (client-side) review
  const [reviewLoading, setReviewLoading] = useState(false); // fetching a server confirmation
  // Server-issued confirmation for an immediate order: the reviewed details +
  // a single-use, short-lived token bound to the exact order below.
  const [confirmation, setConfirmation] = useState<OrderConfirmation | null>(null);
  const [confirmedOrder, setConfirmedOrder] = useState<NewOrder | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<ApiError | null>(null);
  const [submitResult, setSubmitResult] = useState<Record<string, unknown> | null>(null);
  const [scheduledResult, setScheduledResult] = useState<ScheduledOrderRecord | null>(null);

  useEffect(() => {
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

  // Any change to the order details invalidates a held confirmation - the
  // token is bound to the exact order (§13), so the user must re-review.
  useEffect(() => {
    setConfirmation(null);
    setConfirmedOrder(null);
  }, [
    symbol,
    side,
    qty,
    orderType,
    timeInForce,
    limitPrice,
    stopPrice,
    trailPercent,
    takeProfitPct,
    stopLossPct,
    extendedHours,
    accountId,
    executionMode,
  ]);

  useEffect(() => {
    if (!symbol) {
      setQuote(null);
      setFundamentals(null);
      setQuoteError(null);
      return;
    }
    const controller = new AbortController();
    let cancelled = false;

    // Quote and fundamentals are INDEPENDENT calls. A fundamentals failure
    // (e.g. a Yahoo rate-limit) must never prevent the executable price/quote
    // from loading, and vice-versa. Switching symbol clears the prior symbol's
    // data so a stale quote is never shown against the new ticker.
    setQuote(null);
    setFundamentals(null);
    setQuoteLoading(true);
    setQuoteError(null);

    getQuote(symbol, controller.signal)
      .then((q) => {
        if (!cancelled) setQuote(q);
      })
      .catch((error: unknown) => {
        if (cancelled || controller.signal.aborted) return;
        setQuote(null);
        setQuoteError(error instanceof ApiError ? error : new ApiError(0, "Unexpected error."));
      })
      .finally(() => {
        if (!cancelled) setQuoteLoading(false);
      });

    getFundamentals(symbol, controller.signal)
      .then((f) => {
        if (!cancelled) setFundamentals(f);
      })
      .catch(() => {
        // Non-fatal: the company name just won't show. Never blocks the quote
        // or the order-value estimate.
        if (!cancelled) setFundamentals(null);
      });

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [symbol]);

  function handleTradingStyleChange(style: TradingStyle) {
    setTradingStyle(style);
    setTimePeriod(DEFAULT_TIME_PERIOD[style]);
  }

  const currentPosition = useMemo(
    () => positions.find((p) => p.symbol.toUpperCase() === symbol),
    [positions, symbol]
  );

  // Reference price is null (NOT 0) when no usable, fresh quote is available;
  // recomputed as quantity, limit price, quote, or the freshness clock change,
  // so the estimate always reflects the LATEST valid inputs - never a value
  // frozen at initial ticker load.
  const referencePrice = useMemo(
    () => computeReferencePrice({ orderType, limitPrice, quote, nowMs: nowTick }),
    [orderType, limitPrice, quote, nowTick]
  );

  const qtyNum = parseFloat(qty) || 0;
  const priceAvailable = referencePrice !== null;
  const estimatedValue = computeEstimatedValue(qtyNum, referencePrice);

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

  // "Review Order": for a scheduled order this is a client-side review (no
  // submission happens); for an immediate order it fetches a server
  // confirmation (§13) with the exact reviewed details + a single-use token.
  async function handleReview() {
    setSubmitError(null);
    if (executionMode === "scheduled") {
      setReviewing(true);
      return;
    }
    setReviewLoading(true);
    try {
      const order = buildOrder();
      const conf = await confirmOrder(order, accountId);
      setConfirmation(conf);
      setConfirmedOrder(order);
    } catch (error) {
      setSubmitError(error instanceof ApiError ? error : new ApiError(0, "Unexpected error."));
    } finally {
      setReviewLoading(false);
    }
  }

  // Submit the exact order that was confirmed, with its single-use token.
  async function handleSubmitNow() {
    if (!confirmation || !confirmedOrder) return;
    setSubmitting(true);
    setSubmitError(null);
    try {
      const result = await submitOrder(confirmedOrder, accountId, confirmation.confirmation_token);
      setSubmitResult(result);
      setConfirmation(null);
      setConfirmedOrder(null);
    } catch (error) {
      // A 403 here means the token expired, was already used, or the order
      // changed - drop the confirmation so the user must re-review.
      setConfirmation(null);
      setConfirmedOrder(null);
      setSubmitError(error instanceof ApiError ? error : new ApiError(0, "Unexpected error."));
    } finally {
      setSubmitting(false);
    }
  }

  async function handleScheduleSubmit() {
    setSubmitting(true);
    setSubmitError(null);
    try {
      const when = new Date(scheduledAt);
      const record = await scheduleOrder(buildOrder(), when);
      setScheduledResult(record);
      refreshScheduledOrders();
      setReviewing(false);
    } catch (error) {
      setSubmitError(error instanceof ApiError ? error : new ApiError(0, "Unexpected error."));
    } finally {
      setSubmitting(false);
    }
  }

  function loadOrderIntoTicket(record: ScheduledOrderRecord) {
    const o = record.order;
    setSymbol(record.symbol.toUpperCase());
    setSide(o.side);
    setOrderType(o.type);
    if (o.time_in_force) setTimeInForce(o.time_in_force);
    if (o.qty != null) setQty(String(o.qty));
    setLimitPrice(o.limit_price != null ? String(o.limit_price) : "");
    setStopPrice(o.stop_price != null ? String(o.stop_price) : "");
    if (o.trail_percent != null) setTrailPercent(String(o.trail_percent));
    setExecutionMode("now");
    setConfirmation(null);
    setConfirmedOrder(null);
    setSubmitResult(null);
    setScheduledResult(null);
    setReviewing(false);
    window.scrollTo({ top: 0, behavior: "smooth" });
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

  // Review/submission is blocked whenever a required price is unavailable
  // (refPrice === null), so an order is never reviewed against an unknown or
  // stale price.
  const canReview = canReviewOrder({
    symbol,
    qtyNum,
    assetType,
    orderType,
    limitPrice,
    stopPrice,
    trailPercent,
    executionMode,
    scheduledValid,
    refPrice: referencePrice,
  });

  const priceChangePct =
    quote && quote.previous_close
      ? ((quote.price - quote.previous_close) / quote.previous_close) * 100
      : null;

  // Pending (still counting down) and due (time passed, awaiting manual
  // review - the backend never auto-submits, §13).
  const openScheduled = scheduledOrders.filter(
    (s) => s.status === "pending" || s.status === "due"
  );

  const tokenMsRemaining = confirmation
    ? new Date(confirmation.expires_at).getTime() - nowTick
    : 0;
  const tokenExpired = confirmation !== null && tokenMsRemaining <= 0;
  const isSubmissionDisabled = submitError?.status === 403;

  // Any of these open the confirmation modal overlay.
  const modalOpen = Boolean(confirmation || reviewing || submitResult || scheduledResult);

  // Dismiss whatever the modal is currently showing.
  function closeModal() {
    setConfirmation(null);
    setConfirmedOrder(null);
    setReviewing(false);
    setSubmitResult(null);
    setScheduledResult(null);
  }

  return (
    <div className="mx-auto max-w-5xl space-y-5">
      <WorkflowBar current={3} onNavigate={onNavigate} />
      <NextAction
        step="Next step · Monitor your position"
        prompt="Order placed? Track fills, live P/L, and risk alerts in your portfolio."
        label="View Portfolio"
        icon={<Briefcase size={15} />}
        onClick={() => onNavigate("portfolio")}
        secondary={{ label: "Scan the market", onClick: () => onNavigate("markets") }}
      />
      <div>
        <h1 className="text-xl font-semibold text-ink-primary">Trade Ticket</h1>
        <p className="mt-1 text-sm text-ink-secondary">
          Build your order on the left; the summary on the right stays in view. Click Review Order
          to confirm the exact details - including estimated max loss - then submit.
        </p>
      </div>

      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_340px] lg:items-start">
        {/* LEFT — the order form */}
        <div className="space-y-5">
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
        <TickerSearch
          value={symbol}
          placeholder="Search ticker or company…"
          onSelect={(s) => {
            setReviewing(false);
            setSubmitResult(null);
            setSubmitError(null);
            setSymbol(s);
          }}
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
              {formatQuoteAsOf(quote.as_of) && (
                <p className="mt-0.5 text-[11px] text-ink-muted">As of {formatQuoteAsOf(quote.as_of)}</p>
              )}
            </div>
          </div>
        )}
        {!quoteLoading && !quoteError && !priceAvailable && quote === null && symbol && (
          <p className="mt-3 text-xs text-status-warning">
            Price unavailable — order estimate cannot be calculated and review is disabled.
          </p>
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

        <label className="mt-3 block text-xs text-ink-muted">
          Account
          <input
            type="text"
            value={accountId}
            onChange={(e) => setAccountId(e.target.value)}
            placeholder="paper"
            className="mt-1 w-full rounded-lg border border-border bg-surface-2 px-3 py-2 text-sm text-ink-primary focus:border-brand-blue/50 focus:outline-none"
          />
        </label>

        <div className="mt-4 space-y-1.5 rounded-lg border border-border bg-surface-2 px-3 py-2.5 text-sm">
          <div className="flex justify-between gap-3">
            <span className="text-ink-secondary">Estimated order value</span>
            {estimatedValue !== null ? (
              <span className="font-semibold text-ink-primary">
                {qtyNum || 0} × {money(referencePrice as number)} = {money(estimatedValue)}
              </span>
            ) : (
              <span className="text-right font-medium text-status-warning">
                Price unavailable — estimate cannot be calculated
              </span>
            )}
          </div>
          {account && estimatedValue !== null && (
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
        </div>

        {/* RIGHT — sticky order summary + primary action */}
        <aside className="space-y-3 lg:sticky lg:top-4">
          <SectionCard title="Order Summary">
            <div className="flex items-start justify-between gap-3">
              <div>
                <p className="text-base font-semibold text-ink-primary">{symbol || "—"}</p>
                {fundamentals?.long_name && (
                  <p className="text-xs text-ink-muted">{fundamentals.long_name}</p>
                )}
              </div>
              {quote && (
                <div className="text-right">
                  <p className="text-base font-semibold text-ink-primary">{money(quote.price)}</p>
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
              )}
            </div>

            <div className="mt-3 space-y-1.5 rounded-lg border border-border bg-surface-2 px-3 py-2.5 text-sm">
              <div className="flex justify-between gap-3">
                <span className="text-ink-secondary">Side</span>
                <span
                  className={`font-semibold capitalize ${
                    side === "buy" ? "text-status-good" : "text-status-critical"
                  }`}
                >
                  {side}
                </span>
              </div>
              <Row label="Quantity" value={`${qtyNum || 0} shares`} />
              <Row
                label="Order type"
                value={ORDER_TYPES.find((t) => t.id === orderType)?.label ?? orderType}
              />
              <Row
                label="Time in force"
                value={TIF_OPTIONS.find((t) => t.id === timeInForce)?.label ?? timeInForce}
              />
              <Row
                label="Estimated value"
                value={estimatedValue !== null ? money(estimatedValue) : "Price unavailable"}
              />
              {account && estimatedValue !== null && (
                <Row
                  label="Buying power after"
                  value={money(
                    Math.max(
                      0,
                      Number(account.buying_power) - (side === "buy" ? estimatedValue : 0)
                    )
                  )}
                />
              )}
              {takeProfitPrice && (
                <div className="flex justify-between gap-3">
                  <span className="text-ink-secondary">Take profit</span>
                  <span className="font-medium text-status-good">{money(takeProfitPrice)}</span>
                </div>
              )}
              {stopLossPrice && (
                <div className="flex justify-between gap-3">
                  <span className="text-ink-secondary">Stop loss</span>
                  <span className="font-medium text-status-critical">{money(stopLossPrice)}</span>
                </div>
              )}
              <Row
                label="When"
                value={
                  executionMode === "scheduled"
                    ? scheduledDate
                      ? scheduledDate.toLocaleString()
                      : "Not set"
                    : "Immediately"
                }
              />
            </div>

            {submitError && (
              <div
                className={`mt-3 flex items-start gap-2 rounded-lg border px-3 py-2 text-xs ${
                  isSubmissionDisabled
                    ? "border-status-warning/40 bg-status-warning-soft text-status-warning"
                    : "border-status-critical/40 bg-status-critical-soft text-status-critical"
                }`}
              >
                <AlertTriangle size={13} className="mt-0.5 shrink-0" />
                <span>{submitError.message}</span>
              </div>
            )}

            <div className="mt-4 space-y-2">
              <button
                disabled={!canReview || reviewLoading}
                onClick={handleReview}
                className="flex w-full items-center justify-center gap-2 rounded-lg bg-brand-blue px-4 py-2.5 text-sm font-semibold text-white transition-opacity disabled:opacity-40"
              >
                {reviewLoading && <Loader2 size={14} className="animate-spin" />}
                Review Order
              </button>
              {symbol && (
                <button
                  onClick={() => onViewAnalysis(symbol)}
                  className="w-full rounded-lg border border-border px-4 py-2.5 text-sm font-medium text-ink-secondary hover:text-ink-primary"
                >
                  View Analysis
                </button>
              )}
            </div>
          </SectionCard>
        </aside>
      </div>

      {/* Confirmation / review / result modal overlay */}
      {modalOpen && (
        <div className="fixed inset-0 z-[90] flex items-center justify-center p-4">
          <div
            className="absolute inset-0 bg-black/60 backdrop-blur-sm"
            onClick={closeModal}
            aria-hidden
          />
          <div className="relative z-10 w-full max-w-md">
            {/* Immediate order: server-issued confirmation with reviewed details +
                a single-use, short-lived token (§13). */}
            {confirmation && (
              <SectionCard title="Confirm Order">
          <p className="text-sm font-medium text-ink-primary">
            {confirmation.review.side === "buy" ? "Buy" : "Sell"} {confirmation.review.qty ?? ""}
            {confirmation.review.notional != null
              ? ` ${money(confirmation.review.notional)} of`
              : " shares of"}{" "}
            {confirmation.review.symbol}
          </p>
          <div className="mt-2 space-y-1 rounded-lg border border-border bg-surface-2 px-3 py-2.5 text-sm">
            <Row label="Order type" value={ORDER_TYPES.find((t) => t.id === confirmation.review.type)?.label ?? confirmation.review.type} />
            <Row label="Time in force" value={TIF_OPTIONS.find((t) => t.id === confirmation.review.time_in_force)?.label ?? confirmation.review.time_in_force} />
            {confirmation.review.limit_price != null && (
              <Row label="Limit price" value={money(confirmation.review.limit_price)} />
            )}
            {confirmation.review.stop_price != null && (
              <Row label="Stop price" value={money(confirmation.review.stop_price)} />
            )}
            <Row
              label="Estimated value"
              value={estimatedValue !== null ? money(estimatedValue) : "Price unavailable"}
            />
            <Row
              label="Estimated max loss"
              value={
                confirmation.review.estimated_max_loss != null
                  ? money(confirmation.review.estimated_max_loss)
                  : "Not estimable (no stop / market order)"
              }
              emphasize={confirmation.review.estimated_max_loss != null}
            />
            <Row label="Account" value={confirmation.review.account_id} />
            <Row label="Mode" value={confirmation.review.mode} />
          </div>

          <p
            className={`mt-2 flex items-center gap-1.5 text-xs ${
              tokenExpired ? "text-status-critical" : "text-ink-muted"
            }`}
          >
            <Clock size={12} />
            {tokenExpired
              ? "Confirmation expired - review again to get a fresh token."
              : `Confirmation valid ${formatCountdown(tokenMsRemaining)} · single-use`}
          </p>

          {submitError && (
            <div className="mt-3 flex items-start gap-2 rounded-lg border border-status-critical/40 bg-status-critical-soft px-3 py-2 text-xs text-status-critical">
              <AlertTriangle size={13} className="mt-0.5 shrink-0" />
              <span>{submitError.message}</span>
            </div>
          )}

          <div className="mt-4 flex gap-3">
            <button
              disabled={submitting || tokenExpired}
              onClick={handleSubmitNow}
              className="flex flex-1 items-center justify-center gap-2 rounded-lg bg-brand-blue px-4 py-2.5 text-sm font-semibold text-white disabled:opacity-40"
            >
              {submitting && <Loader2 size={14} className="animate-spin" />}
              Submit Order
            </button>
            <button
              onClick={() => {
                setConfirmation(null);
                setConfirmedOrder(null);
              }}
              className="rounded-lg border border-border px-4 py-2.5 text-sm font-medium text-ink-secondary hover:text-ink-primary"
            >
              Back
            </button>
          </div>
        </SectionCard>
            )}

      {/* Scheduled order: client-side review, then queue a draft (never
          auto-submitted). */}
      {reviewing && (
        <SectionCard title="Review Scheduled Order">
          <p className="text-sm text-ink-primary">
            {side === "buy" ? "Buy" : "Sell"} {qtyNum} shares of {symbol}
          </p>
          <p className="mt-1 text-sm text-ink-secondary">
            {ORDER_TYPES.find((t) => t.id === orderType)?.label} order ·{" "}
            {TIF_OPTIONS.find((t) => t.id === timeInForce)?.label} · account {accountId}
          </p>
          <p className="mt-1 text-sm text-ink-secondary">
            Estimated value: {estimatedValue !== null ? money(estimatedValue) : "Price unavailable"}
          </p>
          <p className="mt-1 flex items-center gap-1.5 text-sm text-ink-secondary">
            <Clock size={13} />
            {scheduledDate && `Becomes due for review at ${scheduledDate.toLocaleString()}`}
          </p>
          <div className="mt-2 flex items-start gap-2 rounded-lg border border-status-warning/30 bg-status-warning-soft px-3 py-2 text-xs text-status-warning">
            <AlertTriangle size={13} className="mt-0.5 shrink-0" />
            <span>
              Scheduling queues a draft - it is <strong>not</strong> submitted automatically. When
              due, review and confirm it here to submit.
            </span>
          </div>

          {submitError && (
            <div className="mt-3 flex items-start gap-2 rounded-lg border border-status-critical/40 bg-status-critical-soft px-3 py-2 text-xs text-status-critical">
              <AlertTriangle size={13} className="mt-0.5 shrink-0" />
              <span>{submitError.message}</span>
            </div>
          )}

          <div className="mt-4 flex gap-3">
            <button
              disabled={submitting}
              onClick={handleScheduleSubmit}
              className="flex flex-1 items-center justify-center gap-2 rounded-lg bg-brand-blue px-4 py-2.5 text-sm font-semibold text-white disabled:opacity-40"
            >
              {submitting && <Loader2 size={14} className="animate-spin" />}
              Queue Scheduled Draft
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
        <SectionCard title="Scheduled Draft Queued">
          <div className="flex items-start gap-2 text-sm text-brand-blue">
            <Clock size={15} className="mt-0.5 shrink-0" />
            <span>
              Queued for{" "}
              {new Date(scheduledResult.scheduled_at).toLocaleString()}. It will become
              <strong> due for manual review</strong> then - it is not submitted automatically.
              You'll confirm it here to submit.
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
          </div>
        </div>
      )}

      {openScheduled.length > 0 && (
        <SectionCard
          title="Scheduled Orders"
          description="Queued drafts. Due orders are ready for manual review - none submit automatically."
        >
          <div className="space-y-2">
            {openScheduled.map((s) => {
              const target = new Date(s.scheduled_at).getTime();
              const isDue = s.status === "due";
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
                      {isDue
                        ? `Due for review (${new Date(s.scheduled_at).toLocaleString()})`
                        : `${formatCountdown(target - nowTick)} (${new Date(s.scheduled_at).toLocaleString()})`}
                    </p>
                  </div>
                  <div className="flex items-center gap-1.5">
                    {isDue && (
                      <button
                        onClick={() => loadOrderIntoTicket(s)}
                        className="rounded-md border border-brand-blue/40 px-2.5 py-1 text-xs font-medium text-brand-blue hover:bg-brand-blue/10"
                      >
                        Review & submit
                      </button>
                    )}
                    <button
                      onClick={() => handleCancelScheduled(s.id)}
                      aria-label="Cancel scheduled order"
                      className="rounded-md p-1.5 text-ink-muted hover:bg-surface-2 hover:text-status-critical"
                    >
                      <X size={15} />
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        </SectionCard>
      )}
    </div>
  );
}

function Row({
  label,
  value,
  emphasize,
}: {
  label: string;
  value: string;
  emphasize?: boolean;
}) {
  return (
    <div className="flex justify-between gap-3">
      <span className="text-ink-secondary">{label}</span>
      <span
        className={`text-right ${emphasize ? "font-semibold text-status-critical" : "text-ink-primary"}`}
      >
        {value}
      </span>
    </div>
  );
}
