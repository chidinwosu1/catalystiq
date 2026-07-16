import { useEffect, useState } from "react";
import { AlertTriangle, CheckCircle2, Loader2, X } from "lucide-react";
import {
  ApiError,
  getQuote,
  ingestPriceHistory,
  type DataQualityReport,
  type Quote,
} from "../lib/api";

interface LiveLookupPanelProps {
  symbol: string;
  onClose: () => void;
}

type LoadState =
  | { status: "loading" }
  | { status: "error"; error: ApiError }
  | { status: "success"; quote: Quote; report: DataQualityReport };

/**
 * Pulls real data through the Phase 1 backend (quote + ingest, which runs
 * the Data Validation Layer). Deliberately shows only what's actually been
 * computed - no rating, no probability split, no confidence score. The
 * analytical/behavioral engines that would produce those don't exist yet,
 * and the build spec is explicit that nothing should be invented.
 */
export default function LiveLookupPanel({ symbol, onClose }: LiveLookupPanelProps) {
  const [state, setState] = useState<LoadState>({ status: "loading" });

  useEffect(() => {
    let cancelled = false;
    setState({ status: "loading" });

    Promise.all([getQuote(symbol), ingestPriceHistory(symbol)])
      .then(([quote, report]) => {
        if (!cancelled) setState({ status: "success", quote, report });
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        const apiError =
          error instanceof ApiError ? error : new ApiError(0, "Unexpected error.");
        setState({ status: "error", error: apiError });
      });

    return () => {
      cancelled = true;
    };
  }, [symbol]);

  return (
    <div className="mb-6 rounded-xl border border-brand-blue/30 bg-surface p-5">
      <div className="mb-3 flex items-start justify-between gap-3">
        <div>
          <p className="text-xs font-medium text-ink-muted">
            Live lookup · real backend data, no forecast
          </p>
          <h3 className="text-lg font-medium text-ink-primary">{symbol}</h3>
        </div>
        <button
          onClick={onClose}
          aria-label="Close live lookup"
          className="rounded-md p-1 text-ink-muted hover:bg-surface-2 hover:text-ink-primary"
        >
          <X size={16} />
        </button>
      </div>

      {state.status === "loading" && (
        <div className="flex items-center gap-2 py-6 text-sm text-ink-secondary">
          <Loader2 size={16} className="animate-spin" />
          Fetching quote and price history, then running the Data Validation Layer…
        </div>
      )}

      {state.status === "error" && (
        <div className="rounded-lg border border-status-critical/40 bg-status-critical-soft px-3 py-2.5 text-sm text-status-critical">
          <div className="flex items-center gap-2 font-semibold">
            <AlertTriangle size={15} />
            Couldn't fetch data{state.error.status ? ` (HTTP ${state.error.status})` : ""}
          </div>
          <p className="mt-1 text-ink-secondary">{state.error.message}</p>
          {state.error.status === 502 && (
            <p className="mt-1 text-ink-muted">
              This usually means the market data provider rejected the request - e.g. an
              unknown ticker, or (in this build's sandbox) an outbound network restriction.
            </p>
          )}
          {state.error.status === 0 && (
            <p className="mt-1 text-ink-muted">
              Make sure the backend is running (uvicorn app:app) and VITE_API_BASE_URL points
              at it.
            </p>
          )}
        </div>
      )}

      {state.status === "success" && (
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3">
            <div className="rounded-lg border border-border px-3 py-2.5">
              <p className="text-[11px] uppercase tracking-wide text-ink-muted">Last price</p>
              <p className="mt-0.5 text-sm font-semibold text-ink-primary">
                ${state.quote.price.toFixed(2)}
              </p>
            </div>
            <div className="rounded-lg border border-border px-3 py-2.5">
              <p className="text-[11px] uppercase tracking-wide text-ink-muted">
                Previous close
              </p>
              <p className="mt-0.5 text-sm font-semibold text-ink-primary">
                {state.quote.previous_close != null
                  ? `$${state.quote.previous_close.toFixed(2)}`
                  : "—"}
              </p>
            </div>
          </div>

          <div
            className={`rounded-lg border px-3 py-2.5 text-sm ${
              state.report.passed
                ? "border-status-good/40 bg-status-good-soft text-status-good"
                : "border-status-warning/40 bg-status-warning-soft text-status-warning"
            }`}
          >
            <div className="flex items-center gap-2 font-semibold">
              {state.report.passed ? <CheckCircle2 size={15} /> : <AlertTriangle size={15} />}
              Data quality: {state.report.passed ? "passed" : "flagged"} ·{" "}
              {state.report.bar_count} bars ingested
            </div>
            {state.report.issues.length > 0 && (
              <ul className="mt-2 space-y-1 text-xs text-ink-secondary">
                {state.report.issues.map((issue, i) => (
                  <li key={i}>
                    <span className="font-medium text-ink-primary">{issue.type}</span>
                    {issue.date ? ` (${issue.date})` : ""} — {issue.detail}
                  </li>
                ))}
              </ul>
            )}
          </div>

          <p className="text-xs text-ink-muted">
            This is raw ingested data, not analysis - Catalyst IQ's scoring/forecast engine
            isn't built yet, so there's no rating or probability to show here.
          </p>
        </div>
      )}
    </div>
  );
}
